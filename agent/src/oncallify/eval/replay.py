"""Paired replay: run diagnoser variants over the corpus and score them.

Scoring (per scenario and repeat):

* ``cause_correct``  - diagnosed cause equals the injected cause
* ``action_correct`` - the shared planner's action is acceptable for the true cause,
  and for a revert it targets the right commit
* ``unsafe``         - an action that changes production but is not a correct one
  (wrong fix, or reverting an innocent commit)
* ``missed``         - no action although the truth needed one

Fairness rules:

* every variant goes through the same planner, so only diagnosis quality differs;
* the learned variant (ml-logreg) is scored with leave-one-group-out cross-validation:
  it never sees the group (variation seed) it is tested on;
* the retrieval variant never retrieves the scenario it is being tested on;
* LLM answers are cached per (variant, model, prompt hash, seed) so re-running a report
  is cheap and reproducible.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from oncallify import planner
from oncallify.causes import Cause
from oncallify.diagnose import build
from oncallify.diagnose.base import Diagnoser
from oncallify.diagnose.ml import LogRegDiagnoser, train
from oncallify.diagnose.retrieval import Case, CaseMemory, describe, tfidf_embedder
from oncallify.eval.corpus import Bundle
from oncallify.model import Diagnosis


@dataclass(slots=True)
class Outcome:
    variant: str
    scenario_id: str
    family: str
    group: str
    repeat: int
    true_cause: str
    pred_cause: str
    confidence: float
    action: str
    cause_correct: bool
    action_correct: bool
    unsafe: bool
    missed: bool
    latency_ms: float
    tokens: int


def score(variant: str, bundle: Bundle, diagnosis: Diagnosis, repeat: int) -> Outcome:
    plan = planner.plan(diagnosis, bundle.evidence)
    t = bundle.truth
    acceptable = plan.action in t.acceptable_actions
    if acceptable and plan.action == "revert_config_change":
        acceptable = t.suspect_change is not None and plan.params.get("commit") == t.suspect_change
    changes_prod = plan.action != "none"
    return Outcome(
        variant=variant, scenario_id=bundle.scenario_id, family=bundle.family, group=bundle.group, repeat=repeat,
        true_cause=t.cause, pred_cause=diagnosis.cause, confidence=diagnosis.confidence, action=plan.action,
        cause_correct=diagnosis.cause == t.cause, action_correct=acceptable,
        unsafe=changes_prod and not acceptable,
        missed=(not changes_prod) and "none" not in t.acceptable_actions,
        latency_ms=diagnosis.latency_ms, tokens=diagnosis.tokens,
    )


class CachedDiagnoser:
    """Wraps a nondeterministic diagnoser with an on-disk answer cache."""

    def __init__(self, inner: Diagnoser, cache_dir: Path, key_extra: str):
        self.inner = inner
        self.name = inner.name
        self.cache_dir = cache_dir
        self.key_extra = key_extra

    def diagnose_bundle(self, bundle: Bundle) -> Diagnosis:
        digest = hashlib.sha256(f"{self.key_extra}|{bundle.evidence.digest()}".encode()).hexdigest()[:24]
        path = self.cache_dir / self.name / f"{bundle.scenario_id}-{digest}.json"
        if path.exists():
            return Diagnosis(**json.loads(path.read_text(encoding="utf-8")))
        d = self.inner.diagnose(bundle.evidence)
        if "llm_error" not in d.notes:  # never cache a failure
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(asdict(d)), encoding="utf-8")
        return d


def _memory(bundles: list[Bundle]) -> CaseMemory:
    cases = [Case(b.scenario_id, b.truth.cause, describe(b.evidence)) for b in bundles]
    return CaseMemory(cases, tfidf_embedder([c.summary for c in cases]))


def run_variant(variant: str, bundles: list[Bundle], *, repeats: int = 1, cache_dir: Path | None = None,
                ollama_url: str = "http://127.0.0.1:11434", llm_model: str = "granite4:micro",
                progress: Callable[[str], None] | None = None) -> list[Outcome]:
    out: list[Outcome] = []
    if variant == "ml-logreg":
        # leave-one-group-out: train on every other variation seed, test on this one
        for g in sorted({b.group for b in bundles}):
            train_set = [b for b in bundles if b.group != g]
            model = train([b.evidence for b in train_set], [b.truth.cause for b in train_set])
            diag = LogRegDiagnoser(model)
            for b in (b for b in bundles if b.group == g):
                out.append(score(variant, b, diag.diagnose(b.evidence), 0))
        return sorted(out, key=lambda o: o.scenario_id)

    memory = _memory(bundles) if variant.endswith("-rag") else None
    deterministic = variant.startswith("rules")
    for r in range(1 if deterministic else repeats):
        inner = build(variant, ollama_url=ollama_url, llm_model=llm_model, seed=r, memory=memory)
        cached = CachedDiagnoser(inner, cache_dir, f"{llm_model}|seed={r}") if (cache_dir and not deterministic) \
            else None
        for b in bundles:
            if memory is not None:
                inner.memory = memory.without({b.scenario_id})  # type: ignore[attr-defined]
            d = cached.diagnose_bundle(b) if cached else inner.diagnose(b.evidence)
            out.append(score(variant, b, d, r))
            if progress:
                progress(f"{variant} r{r} {b.scenario_id}: {d.cause}")
    return out


def outcomes_to_rows(outcomes: list[Outcome]) -> list[dict[str, Any]]:
    return [asdict(o) for o in outcomes]


def confusion(outcomes: list[Outcome]) -> dict[str, dict[str, int]]:
    causes = [c.value for c in Cause]
    table = {t: dict.fromkeys(causes, 0) for t in causes}
    for o in outcomes:
        table.setdefault(o.true_cause, dict.fromkeys(causes, 0))
        table[o.true_cause][o.pred_cause] = table[o.true_cause].get(o.pred_cause, 0) + 1
    return {t: {p: n for p, n in row.items() if n} for t, row in table.items() if any(row.values())}
