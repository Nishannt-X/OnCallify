"""Retrieval of similar past incidents (the "RAG on vs off" experiment arm).

Each past case is described in a short, fixed text form (error kinds, alerts, what
changed, resource signals) and embedded. Embeddings come from Granite Embedding via
Ollama when available; otherwise a local TF-IDF embedding is used, so the arm still
runs (and is labelled) without a model server.

Leakage guard: in offline evaluation a case must never retrieve itself. ``without``
returns a view of the memory that excludes given case ids while sharing the cached
embeddings.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import httpx

from oncallify import features
from oncallify.model import Evidence

Embedder = Callable[[Sequence[str]], list[list[float]]]


def describe(ev: Evidence) -> str:
    """Stable, short text form of an incident used for similarity."""
    f = features.extract(ev)
    kinds = sorted(ev.logs.by_error_kind.items(), key=lambda kv: -kv[1])[:3]
    parts = [
        "alerts: " + ", ".join(sorted({a.rule for a in ev.alerts})),
        "errors: " + ", ".join(f"{k} {v}" for k, v in kinds),
        "changes: " + "; ".join(f"{c.source} {' '.join(sorted(c.keys))}" for c in ev.changes) if ev.changes else
        "changes: none",
        f"heap max {f['heap_ratio_max']:.2f} rise {f['heap_ratio_rise']:.2f}",
        f"pool saturation {f['pool_saturation']:.2f} held/busy {f['pool_held_per_busy_thread']:.1f}",
        f"still active {f['errors_still_active']:.2f}",
    ]
    hints = [h for h in ("connection_refused", "provider_503", "password_failed", "timed_out") if f[f"msg_{h}"]]
    if hints:
        parts.append("messages: " + ", ".join(hints))
    return " | ".join(parts)


def tfidf_embedder(corpus: Sequence[str]) -> Embedder:
    """A deterministic TF-IDF embedder fitted on *corpus* (fallback when no model server)."""
    tokenize: Callable[[str], list[str]] = lambda s: re.findall(r"[a-z_]+|\d+(?:\.\d+)?", s.lower())  # noqa: E731
    docs = [set(tokenize(d)) for d in corpus]
    vocab = sorted(set().union(*docs)) if docs else []
    index = {t: i for i, t in enumerate(vocab)}
    n = max(1, len(docs))
    idf = [math.log((1 + n) / (1 + sum(1 for d in docs if t in d))) + 1 for t in vocab]

    def embed(texts: Sequence[str]) -> list[list[float]]:
        out = []
        for text in texts:
            tf = Counter(t for t in tokenize(text) if t in index)
            vec = [0.0] * len(vocab)
            for t, c in tf.items():
                vec[index[t]] = c * idf[index[t]]
            out.append(vec)
        return out

    return embed


def ollama_embedder(base_url: str, model: str, transport: httpx.BaseTransport | None = None) -> Embedder:
    http = httpx.Client(base_url=base_url, timeout=60.0, transport=transport)

    def embed(texts: Sequence[str]) -> list[list[float]]:
        r = http.post("/api/embed", json={"model": model, "input": list(texts)})
        r.raise_for_status()
        return [list(map(float, v)) for v in r.json()["embeddings"]]

    return embed


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


@dataclass(slots=True)
class Case:
    case_id: str
    cause: str
    summary: str


class CaseMemory:
    def __init__(self, cases: list[Case], embedder: Embedder, *, _vectors: dict[str, list[float]] | None = None,
                 _excluded: frozenset[str] = frozenset()):
        self.cases = cases
        self.embedder = embedder
        self._vectors = _vectors if _vectors is not None else {}
        self._excluded = _excluded
        missing = [c for c in cases if c.case_id not in self._vectors]
        if missing:
            for c, v in zip(missing, embedder([c.summary for c in missing]), strict=True):
                self._vectors[c.case_id] = v

    def without(self, case_ids: set[str]) -> CaseMemory:
        return CaseMemory(self.cases, self.embedder, _vectors=self._vectors,
                          _excluded=self._excluded | frozenset(case_ids))

    def similar(self, ev: Evidence, k: int) -> list[dict[str, str]]:
        query = self.embedder([describe(ev)])[0]
        scored = [
            (_cos(query, self._vectors[c.case_id]), c)
            for c in self.cases
            if c.case_id not in self._excluded
        ]
        scored.sort(key=lambda sc: -sc[0])
        return [{"case_id": c.case_id, "cause": c.cause, "summary": c.summary, "score": f"{s:.3f}"}
                for s, c in scored[:k]]
