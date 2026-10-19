"""Prometheus text, log reduction, change ledger, metric series."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from oncallify import logreduce, promtext
from oncallify.changes import ChangeLedger, GitConfigSource, JsonlSource, append_jsonl, diff_properties
from oncallify.evidence import metric_series
from oncallify.model import iso, utcnow


def test_promtext_parses_labels_escapes_and_skips_comments_and_nan() -> None:
    text = (
        "# HELP x y\n# TYPE c counter\n"
        'c{kind="pool_exhausted",route="POST /t"} 12\n'
        'c{k="a\\"b"} 1\n'
        "g 3.5\n"
        "broken NaN\n"
        "not a metric line\n"
    )
    samples = promtext.parse(text)
    assert [(s.name, s.value) for s in samples] == [("c", 12.0), ("c", 1.0), ("g", 3.5)]
    assert samples[0].labels == {"kind": "pool_exhausted", "route": "POST /t"}
    assert samples[1].labels == {"k": 'a"b'}


def test_log_templates_collapse_variable_parts_and_rank_errors_first() -> None:
    lines = [{"ts": f"2026-10-20T00:00:{i:02d}Z", "level": "INFO", "msg": f"request {i} ok"} for i in range(30)]
    lines += [{"ts": f"2026-10-20T00:00:{i:02d}Z", "level": "ERROR", "msg": "request failed",
               "error_kind": "pool_exhausted",
               "detail": f"PoolExhaustedException: [http-nio-127.0.0.1-8080-exec-{i}] Timeout: Pool empty size:{i}"}
              for i in range(5)]
    s = logreduce.summarize(lines, recent_since="2026-10-20T00:00:03Z")
    assert s.total_lines == 35
    assert s.by_error_kind == {"pool_exhausted": 5}
    assert s.recent_error_kinds == {"pool_exhausted": 2}
    assert s.templates[0].level == "ERROR" and s.templates[0].count == 5
    assert "<thread>" in s.templates[0].template and "<n>" in s.templates[0].template
    assert s.templates[1].count == 30  # thirty INFO lines, one template


def test_diff_properties_reports_old_and_new() -> None:
    assert diff_properties({"a": "1", "b": "2"}, {"a": "1", "b": "3", "c": "4"}) == {"b": ["2", "3"], "c": [None, "4"]}


def test_git_ledger_reads_commits_with_key_diffs_and_ignores_harness(config_repo) -> None:  # type: ignore[no-untyped-def]
    conf, git = config_repo["conf"], config_repo["git"]
    f = conf / "ledgerline.properties"
    f.write_text(f.read_text().replace("pool.max=10", "pool.max=2"))
    git("commit", "-qam", "shrink pool", author="dev-asha")
    f.write_text(f.read_text().replace("pool.max=2", "pool.max=10"))
    git("commit", "-qam", "reset", author="scenario-reset")
    since, until = utcnow() - timedelta(minutes=5), utcnow() + timedelta(minutes=1)
    changes = GitConfigSource(conf, ignore_authors=["scenario-reset"]).changes(since, until)
    summaries = [c.summary for c in changes]
    assert "reset" not in summaries  # the harness author is invisible to the ledger
    shrink = next(c for c in changes if c.summary == "shrink pool")
    assert shrink.keys == {"pool.max": ["10", "2"]}
    assert shrink.author == "dev-asha"
    assert len(shrink.id) == 12 and shrink.parent


def test_jsonl_source_filters_by_time(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "vault.jsonl"
    now = datetime.now(tz=UTC)
    append_jsonl(path, {"ts": iso(now - timedelta(hours=2)), "id": "old", "summary": "old"})
    append_jsonl(path, {"ts": iso(now), "id": "new", "summary": "rotated secret ledgerline-db",
                        "keys": {"secret": [None, "ledgerline-db"]}})
    path.write_text(path.read_text() + "not json\n")
    ledger = ChangeLedger([JsonlSource(path, "vault")])
    got = ledger.changes(now - timedelta(minutes=1), now + timedelta(minutes=1))
    assert [c.id for c in got] == ["new"] and got[0].source == "vault"


def test_metric_series_turns_counters_into_increases_and_handles_restart() -> None:
    def scrape(t: int, errors: float, active: float) -> dict:  # type: ignore[type-arg]
        return {"ts": f"2026-10-20T00:00:{t:02d}.000000Z", "body": {"samples": [
            {"name": "ledgerline_errors_total", "labels": {"kind": "pool_exhausted"}, "value": errors},
            {"name": "ledgerline_pool_active", "labels": {}, "value": active},
            {"name": "unrelated_metric", "labels": {}, "value": 1},
        ]}}

    out = metric_series([scrape(0, 100, 3), scrape(1, 110, 10), scrape(2, 4, 0)], "2026-10-20T00:00:00.000000Z")
    assert out["errors:pool_exhausted"] == [[1.0, 10.0], [2.0, 4.0]]  # 100->110 = +10, then restart -> +4
    assert out["ledgerline_pool_active"] == [[0.0, 3.0], [1.0, 10.0], [2.0, 0.0]]
    assert "unrelated_metric" not in out
