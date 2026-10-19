"""Command line: ``oncallify <command>``. Run ``oncallify -h`` for the list."""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import secrets
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path

from oncallify.settings import Settings

DEFAULT_CORPUS = Path(__file__).resolve().parents[3] / "eval" / "corpus"
DEFAULT_REPORTS = Path(__file__).resolve().parents[3] / "eval" / "reports"


def _settings() -> Settings:
    return Settings()


def cmd_serve(a: argparse.Namespace) -> int:
    import uvicorn

    from oncallify.api import create_app
    from oncallify.wiring import build_pipeline

    s = _settings()
    if not s.ingest_token:
        print("ONCALLIFY_INGEST_TOKEN is required", file=sys.stderr)
        return 2
    pipeline = build_pipeline(s)
    if a.collect:
        threading.Thread(target=_collector(s).run, daemon=True).start()
    uvicorn.run(create_app(pipeline, web_dist=s.web_dist), host="127.0.0.1", port=s.agent_port, log_level="info")
    return 0


def _collector(s: Settings):  # type: ignore[no-untyped-def]
    from oncallify.clients import GatewayClient, ServiceClient
    from oncallify.collector import Collector

    log_path = s.run_dir / "ledgerline" / "logs" / "ledgerline.jsonl"
    return Collector(GatewayClient(s.gateway_url, s.ingest_token), ServiceClient(s.ledgerline_url), log_path)


def cmd_collect(_: argparse.Namespace) -> int:
    c = _collector(_settings())
    try:
        c.run()
    except KeyboardInterrupt:
        c.stop()
    return 0


def cmd_loadgen(a: argparse.Namespace) -> int:
    from oncallify.loadgen import LoadGenerator

    lg = LoadGenerator(_settings().ledgerline_url, workers=a.workers, seed=a.seed).start()
    with contextlib.suppress(KeyboardInterrupt):
        time.sleep(a.seconds)
    lg.stop()
    print(json.dumps({"statuses": dict(lg.stats.statuses), "errors": lg.stats.errors}))
    return 0


def cmd_approver(a: argparse.Namespace) -> int:
    from oncallify.wiring import write_approver

    s = _settings()
    path = s.approvers_file or (s.run_dir / "approvers.json")
    token = a.token or secrets.token_urlsafe(24)
    write_approver(path, a.name, token)
    print(json.dumps({"approver": a.name, "token": token, "file": str(path)}))
    return 0


def cmd_eval_capture(a: argparse.Namespace) -> int:
    from oncallify.eval.scenarios import default_specs, run_capture

    specs = default_specs(groups=a.groups)
    if a.families:
        wanted = set(a.families.split(","))
        specs = [sp for sp in specs if sp.family in wanted]
    if a.only:
        specs = [sp for sp in specs if sp.scenario_id in set(a.only.split(","))]
    summary = run_capture(_settings(), Path(a.out), specs, skip_existing=not a.overwrite,
                          log=lambda m: print(m, flush=True))
    print(json.dumps({"captured": len(summary["captured"]), "skipped": summary["skipped"]}))
    return 0


def cmd_eval_train(a: argparse.Namespace) -> int:
    from oncallify.diagnose.ml import DEFAULT_MODEL_PATH, train
    from oncallify.eval.corpus import load

    bundles = load(Path(a.corpus))
    model = train([b.evidence for b in bundles], [b.truth.cause for b in bundles])
    out = Path(a.out) if a.out else DEFAULT_MODEL_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(model, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"model": str(out), "n_train": model["n_train"], "classes": model["classes"]}))
    return 0


def cmd_eval_run(a: argparse.Namespace) -> int:
    from oncallify.eval import report, stats
    from oncallify.eval.corpus import load
    from oncallify.eval.replay import outcomes_to_rows, run_variant

    s = _settings()
    bundles = load(Path(a.corpus))
    if not bundles:
        print(f"no bundles in {a.corpus}", file=sys.stderr)
        return 2
    variants = a.variants.split(",")
    cache = Path(a.out) / ".cache"
    results = {}
    for v in variants:
        print(f"running {v} on {len(bundles)} scenarios...", flush=True)
        results[v] = run_variant(v, bundles, repeats=a.repeats, cache_dir=cache, ollama_url=s.ollama_url,
                                 llm_model=s.llm_model)
    baseline = variants[0]
    summaries = [report.summarize(results[v]) for v in variants]
    comparisons = [report.compare(results[baseline], results[v]) for v in variants[1:]]
    validation = asdict(stats.validate_tests(n_sims=a.aa_sims, n_pairs=len(bundles))) if a.aa_sims else None
    families = sorted({b.family for b in bundles})
    note = (f"Corpus: {len(bundles)} incidents captured from the live stack ({len(families)} fault families x "
            f"{len({b.group for b in bundles})} variations). Baseline: `{baseline}`. "
            f"Repeats for nondeterministic variants: {a.repeats}.")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    body = {"corpus_note": note, "summaries": summaries, "comparisons": comparisons, "validation": validation,
            "outcomes": {v: outcomes_to_rows(o) for v, o in results.items()}}
    (out / "report.json").write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")
    md = report.markdown(summaries, comparisons, validation, note)
    (out / "report.md").write_text(md, encoding="utf-8")
    if a.store:
        from oncallify.store import open_store

        st = open_store(a.store)
        st.save_report(",".join(variants), {k: v for k, v in body.items() if k != "outcomes"})
        st.close()
    print(md)
    return 0


def cmd_eval_gate(a: argparse.Namespace) -> int:
    from oncallify.eval import report
    from oncallify.eval.corpus import load
    from oncallify.eval.replay import run_variant

    s = _settings()
    bundles = load(Path(a.corpus))
    cache = Path(a.cache)
    base = run_variant(a.baseline, bundles, repeats=a.repeats, cache_dir=cache, ollama_url=s.ollama_url,
                       llm_model=s.llm_model)
    cand = run_variant(a.candidate, bundles, repeats=a.repeats, cache_dir=cache, ollama_url=s.ollama_url,
                       llm_model=s.llm_model)
    result = report.gate(base, cand, accuracy_margin=a.margin)
    print(json.dumps(result, indent=1))
    return 0 if result["passed"] else 1


def cmd_eval_validate(a: argparse.Namespace) -> int:
    from oncallify.eval import stats

    v = stats.validate_tests(n_sims=a.sims, n_pairs=a.pairs)
    print(json.dumps(asdict(v), indent=1))
    ok = v.mcnemar_fpr <= 0.06 and v.signflip_fpr <= 0.06 and 0.93 <= v.bootstrap_coverage <= 0.97 \
        and v.peeking_fpr_msprt <= 0.06
    return 0 if ok else 1


def cmd_experiment(a: argparse.Namespace) -> int:
    from oncallify.clients import GatewayClient

    s = _settings()
    g = GatewayClient(s.gateway_url, s.ingest_token)
    if a.action == "create":
        body = {"name": a.name, "salt": a.salt or secrets.token_hex(6),
                "variants": [{"name": v, "weight": 1} for v in a.variants.split(",")], "status": "running"}
        print(json.dumps(g.put_experiment(body)))
    else:
        from oncallify.eval.stats import srm_check

        counts = g.experiment_counts(a.name)
        srm = srm_check(counts["assignments"], counts["expected_weights"])
        print(json.dumps({**counts, "srm": asdict(srm)}, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    p = argparse.ArgumentParser(prog="oncallify", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("serve", help="run the agent API (and optionally the collector)")
    sp.add_argument("--collect", action="store_true", help="also ship logs and metrics to the gateway")
    sp.set_defaults(fn=cmd_serve)
    sub.add_parser("collect", help="ship logs and metrics to the gateway").set_defaults(fn=cmd_collect)

    sp = sub.add_parser("loadgen", help="send closed-loop traffic to Ledgerline")
    sp.add_argument("--workers", type=int, default=10)
    sp.add_argument("--seconds", type=float, default=60)
    sp.add_argument("--seed", type=int, default=0)
    sp.set_defaults(fn=cmd_loadgen)

    sp = sub.add_parser("approver", help="create an approver token (stored hashed)")
    sp.add_argument("--name", required=True)
    sp.add_argument("--token")
    sp.set_defaults(fn=cmd_approver)

    ev = sub.add_parser("eval", help="offline experiments").add_subparsers(dest="evcmd", required=True)
    sp = ev.add_parser("capture", help="break the live stack and record replay bundles")
    sp.add_argument("--out", default=str(DEFAULT_CORPUS))
    sp.add_argument("--groups", type=int, default=8)
    sp.add_argument("--families")
    sp.add_argument("--only")
    sp.add_argument("--overwrite", action="store_true")
    sp.set_defaults(fn=cmd_eval_capture)

    sp = ev.add_parser("train", help="train the ml-logreg diagnoser on the corpus")
    sp.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    sp.add_argument("--out")
    sp.set_defaults(fn=cmd_eval_train)

    sp = ev.add_parser("run", help="paired replay of variants and a comparison report")
    sp.add_argument("--variants", default="rules-v1,rules-v2,ml-logreg")
    sp.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    sp.add_argument("--out", default=str(DEFAULT_REPORTS))
    sp.add_argument("--repeats", type=int, default=3)
    sp.add_argument("--aa-sims", type=int, default=2000)
    sp.add_argument("--store")
    sp.set_defaults(fn=cmd_eval_run)

    sp = ev.add_parser("gate", help="CI gate: candidate must be non-inferior to baseline")
    sp.add_argument("--baseline", default="rules-v2")
    sp.add_argument("--candidate", required=True)
    sp.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    sp.add_argument("--cache", default=str(DEFAULT_REPORTS / ".cache"))
    sp.add_argument("--repeats", type=int, default=3)
    sp.add_argument("--margin", type=float, default=0.05)
    sp.set_defaults(fn=cmd_eval_gate)

    sp = ev.add_parser("validate", help="A/A simulation: do the tests reject at their nominal rate?")
    sp.add_argument("--sims", type=int, default=2000)
    sp.add_argument("--pairs", type=int, default=56)
    sp.set_defaults(fn=cmd_eval_validate)

    sp = sub.add_parser("experiment", help="live experiments on the gateway")
    sp.add_argument("action", choices=["create", "srm"])
    sp.add_argument("--name", required=True)
    sp.add_argument("--variants", default="rules-v2,ml-logreg")
    sp.add_argument("--salt")
    sp.set_defaults(fn=cmd_experiment)

    a = p.parse_args(argv)
    return int(a.fn(a))


if __name__ == "__main__":
    raise SystemExit(main())
