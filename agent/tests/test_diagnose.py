"""Diagnosers: rules baselines, the ML model, and the LLM's untrusted-output handling."""

from __future__ import annotations

import json

import httpx
import pytest

from oncallify import features
from oncallify.diagnose import available_variants, build
from oncallify.diagnose.llm import RESPONSE_SCHEMA, OllamaDiagnoser, build_prompt
from oncallify.diagnose.ml import LogRegDiagnoser, train
from oncallify.diagnose.retrieval import Case, CaseMemory, describe, tfidf_embedder
from oncallify.diagnose.rules import RulesV1, RulesV2

from .conftest import CANONICAL, ev_bad_config, ev_credential, ev_fx_slow, ev_leak, ev_transient, ev_undersized


def test_all_variants_register() -> None:
    assert {"rules-v1", "rules-v2", "ml-logreg", "granite", "granite-rag"} <= set(available_variants())
    with pytest.raises(KeyError):
        build("magic-8-ball")


@pytest.mark.parametrize("cause", sorted(CANONICAL))
def test_rules_v2_gets_every_canonical_case_right(cause: str) -> None:
    d = RulesV2().diagnose(CANONICAL[cause]())
    assert d.cause == cause, d.rationale


def test_rules_v2_names_the_right_commit_even_with_a_red_herring() -> None:
    d = RulesV2().diagnose(ev_bad_config(herring=True))
    assert d.cause == "bad_config" and d.suspect_change == "bad000000001"


def test_rules_v1_falls_for_the_known_blind_spots() -> None:
    v1 = RulesV1()
    assert v1.diagnose(ev_undersized()).cause == "connection_leak"  # cannot see the config change
    assert v1.diagnose(ev_transient()).cause == "bad_config"  # cannot tell a blip from a bad URL
    assert v1.diagnose(ev_bad_config(herring=True)).suspect_change == "4e771c9a0b12"  # blames the latest change


def test_latency_alone_does_not_mean_the_provider_is_slow() -> None:
    # a leak pins latency at the pool's maxWait; that must not read as FX trouble
    assert RulesV2().diagnose(ev_leak()).cause == "connection_leak"
    assert RulesV2().diagnose(ev_fx_slow()).cause == "fx_degraded"


def test_unrelated_secret_rotation_lowers_confidence() -> None:
    real = RulesV2().diagnose(ev_credential())
    herring = RulesV2().diagnose(ev_credential(herring_only=True))
    assert real.cause == herring.cause == "credential_drift"
    assert real.confidence > herring.confidence


def test_features_are_named_and_stable() -> None:
    f = features.extract(ev_leak())
    assert tuple(f) == features.FEATURE_NAMES
    assert f["share_pool_exhausted"] == 1.0


def test_logreg_learns_and_explains() -> None:
    evs, labels = [], []
    for cause, make in CANONICAL.items():
        for _ in range(3):
            evs.append(make())
            labels.append(cause)
    model = train(evs, labels)
    json.dumps(model)  # serialisable, no pickle
    diag = LogRegDiagnoser(model)
    for cause, make in CANONICAL.items():
        d = diag.diagnose(make())
        assert d.cause == cause
        assert 0 < d.confidence <= 1
    assert diag.diagnose(ev_undersized()).suspect_change == "abc123def456"
    assert diag.explain(ev_leak(), "connection_leak")


def test_logreg_refuses_a_model_from_another_feature_set() -> None:
    model = train([ev_leak(), ev_transient()], ["connection_leak", "transient"])
    model["features"] = model["features"][:-1]
    with pytest.raises(ValueError, match="different feature set"):
        LogRegDiagnoser(model)


def _ollama(answer: dict | str, status: int = 200) -> httpx.MockTransport:  # type: ignore[type-arg]
    seen: list[dict] = []  # type: ignore[type-arg]

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append(body)
        assert body["format"] == RESPONSE_SCHEMA and body["stream"] is False
        content = answer if isinstance(answer, str) else json.dumps(answer)
        return httpx.Response(status, json={"message": {"content": content}, "prompt_eval_count": 900,
                                            "eval_count": 60})

    t = httpx.MockTransport(handler)
    t.seen = seen  # type: ignore[attr-defined]
    return t


def test_llm_answer_is_validated_like_untrusted_input() -> None:
    t = _ollama({"cause": "bad_config", "suspect_change": "bad0000", "confidence": 1.7,
                 "rationale": "fx.url changed", "evidence_refs": ["changes.bad000000001"]})
    d = OllamaDiagnoser(base_url="http://x", model="granite4:micro", name="granite", transport=t).diagnose(
        ev_bad_config(herring=True))
    assert d.cause == "bad_config"
    assert d.suspect_change == "bad000000001"  # unique prefix expanded to the real id
    assert d.confidence == 1.0  # clamped
    assert d.tokens == 960


def test_llm_invented_commit_is_dropped_and_unknown_cause_is_unknown() -> None:
    t = _ollama({"cause": "solar flare", "suspect_change": "cafebabe", "confidence": 0.9, "rationale": "",
                 "evidence_refs": []})
    d = OllamaDiagnoser(base_url="http://x", model="m", name="granite", transport=t).diagnose(ev_bad_config())
    assert d.cause == "unknown" and d.suspect_change is None
    assert any("not in the evidence" in n for n in d.notes)


def test_llm_failure_degrades_to_unknown_not_an_exception() -> None:
    t = _ollama("not json at all")
    d = OllamaDiagnoser(base_url="http://x", model="m", name="granite", transport=t).diagnose(ev_leak())
    assert d.cause == "unknown" and "llm_error" in d.notes
    t500 = _ollama({}, status=500)
    assert OllamaDiagnoser(base_url="http://x", model="m", name="g", transport=t500).diagnose(ev_leak()).cause == \
        "unknown"


def test_prompt_contains_causes_evidence_and_retrieved_cases() -> None:
    p = build_prompt(ev_bad_config(), [{"cause": "bad_config", "summary": "fx.url changed last week"}])
    assert "credential_drift" in p and "fx.url changed last week" in p and "bad000000001" in p


def test_retrieval_never_returns_the_excluded_case() -> None:
    cases = [Case(f"{c}-{i}", c, describe(make())) for c, make in CANONICAL.items() for i in range(2)]
    memory = CaseMemory(cases, tfidf_embedder([c.summary for c in cases]))
    top = memory.similar(ev_leak(), 1)
    assert top[0]["cause"] == "connection_leak"
    view = memory.without({"connection_leak-0", "connection_leak-1"})
    assert all(r["case_id"].split("-")[0] != "connection_leak" or "_" in r["case_id"]
               for r in view.similar(ev_leak(), 3))
    assert all(not r["case_id"].startswith("connection_leak-") for r in view.similar(ev_leak(), 14))
