"""LLM diagnoser: IBM Granite (or any model) served locally by Ollama.

The model sees the same Evidence as every other variant, rendered as compact JSON,
and must answer in a fixed JSON schema (Ollama structured outputs). Its answer is then
validated like untrusted input:

* the cause must be one of the known causes, otherwise it becomes ``unknown``;
* the suspect change must be an id that is actually in the evidence, otherwise it is
  dropped (models invent commit ids);
* confidence is clamped to [0, 1].

It never proposes an action. The planner does that from the cause.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx

from oncallify.causes import DIAGNOSABLE, Cause, parse
from oncallify.diagnose.base import register
from oncallify.diagnose.retrieval import CaseMemory
from oncallify.model import Diagnosis, Evidence, to_dict

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "diagnose.txt"

CAUSE_HELP = {
    Cause.CONNECTION_LEAK: "connections are borrowed and never returned; pool busy while request threads are idle",
    Cause.POOL_UNDERSIZED: "a config change reduced pool size or wait time; pool exhausted under normal load",
    Cause.BAD_CONFIG: "a config change broke a dependency address (e.g. fx.url); connection refused",
    Cause.FX_DEGRADED: "the FX provider is slow or failing and nothing changed on our side",
    Cause.CREDENTIAL_DRIFT: "the database password was rotated but the app still uses the old one",
    Cause.MEMORY_LEAK: "heap climbs steadily toward the max; GC pressure or out-of-memory",
    Cause.TRANSIENT: "a short blip that already stopped; no action needed",
}

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "cause": {"type": "string", "enum": [c.value for c in DIAGNOSABLE] + ["unknown"]},
        "suspect_change": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["cause", "suspect_change", "confidence", "rationale", "evidence_refs"],
}


def render_evidence(ev: Evidence, max_points: int = 20) -> dict[str, Any]:
    """Compact view for the prompt: metrics are thinned, logs are already templated."""
    d = to_dict(ev)
    thin: dict[str, Any] = {}
    for k, pts in d["metrics"].items():
        step = max(1, len(pts) // max_points)
        thin[k] = [[round(t), round(v, 3)] for t, v in pts[::step]]
    d["metrics"] = thin
    for a in d["alerts"]:
        a.pop("fingerprint", None)
    return dict(d)


def build_prompt(ev: Evidence, similar: list[dict[str, str]]) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    causes = "\n".join(f"- {c.value}: {CAUSE_HELP[c]}" for c in DIAGNOSABLE)
    past = "\n".join(f"- ({s['cause']}) {s['summary']}" for s in similar) or "(none)"
    return (
        template.replace("{{CAUSES}}", causes)
        .replace("{{SIMILAR}}", past)
        .replace("{{EVIDENCE}}", json.dumps(render_evidence(ev), separators=(",", ":")))
    )


class OllamaDiagnoser:
    def __init__(self, *, base_url: str, model: str, name: str, temperature: float = 0.2, seed: int = 0,
                 memory: CaseMemory | None = None, k: int = 2, timeout: float = 120.0,
                 transport: httpx.BaseTransport | None = None):
        self.name = name
        self.model = model
        self.temperature = temperature
        self.seed = seed
        self.memory = memory
        self.k = k
        self._http = httpx.Client(base_url=base_url, timeout=timeout, transport=transport)

    def _chat(self, prompt: str) -> tuple[dict[str, Any], int]:
        r = self._http.post("/api/chat", json={
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "format": RESPONSE_SCHEMA,
            "options": {"temperature": self.temperature, "seed": self.seed},
        })
        r.raise_for_status()
        body = r.json()
        tokens = int(body.get("prompt_eval_count", 0)) + int(body.get("eval_count", 0))
        return json.loads(body["message"]["content"]), tokens

    def diagnose(self, evidence: Evidence) -> Diagnosis:
        t0 = time.perf_counter()
        similar = self.memory.similar(evidence, self.k) if self.memory else []
        notes: list[str] = [f"retrieved {len(similar)} past case(s)"] if self.memory else []
        try:
            raw, tokens = self._chat(build_prompt(evidence, similar))
        except (httpx.HTTPError, json.JSONDecodeError, KeyError) as e:
            return Diagnosis(cause=Cause.UNKNOWN.value, confidence=0.0, rationale=f"model call failed: {e}",
                             variant=self.name, latency_ms=(time.perf_counter() - t0) * 1000,
                             notes=["llm_error"])
        return self._validate(raw, evidence, tokens, (time.perf_counter() - t0) * 1000, notes)

    def _validate(self, raw: dict[str, Any], ev: Evidence, tokens: int, latency_ms: float,
                  notes: list[str]) -> Diagnosis:
        cause = parse(str(raw.get("cause", "")))
        if str(raw.get("cause", "")).strip().lower() != cause.value:
            notes.append(f"model answered unknown cause {raw.get('cause')!r}")
        suspect = raw.get("suspect_change")
        known_ids = {c.id for c in ev.changes}
        if suspect is not None:
            suspect = str(suspect)
            matches = [cid for cid in known_ids if cid.startswith(suspect) or suspect.startswith(cid)]
            if len(matches) == 1:
                suspect = matches[0]
            else:
                notes.append(f"dropped suspect change {suspect!r}: not in the evidence")
                suspect = None
        try:
            conf = min(1.0, max(0.0, float(raw.get("confidence", 0.0))))
        except (TypeError, ValueError):
            conf = 0.0
        refs = [str(r) for r in raw.get("evidence_refs") or []][:8]
        return Diagnosis(cause=cause.value, confidence=conf, rationale=str(raw.get("rationale", ""))[:600],
                         evidence_refs=refs, suspect_change=suspect, variant=self.name, latency_ms=latency_ms,
                         tokens=tokens, notes=notes)


@register("granite")
def _granite(*, ollama_url: str = "http://127.0.0.1:11434", llm_model: str = "granite4:micro", seed: int = 0,
             **_: Any) -> OllamaDiagnoser:
    return OllamaDiagnoser(base_url=ollama_url, model=llm_model, name="granite", seed=seed)


@register("granite-rag")
def _granite_rag(*, ollama_url: str = "http://127.0.0.1:11434", llm_model: str = "granite4:micro", seed: int = 0,
                 memory: CaseMemory | None = None, **_: Any) -> OllamaDiagnoser:
    return OllamaDiagnoser(base_url=ollama_url, model=llm_model, name="granite-rag", seed=seed, memory=memory)
