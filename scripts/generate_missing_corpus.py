"""Generates missing corpus bundles so eval/corpus has all 56 scenarios (7 fault families x 8 groups).

Preserves any existing real captures in eval/corpus.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

# Add agent/src and agent/tests to sys.path
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "agent" / "src"))
sys.path.insert(0, str(root / "agent" / "tests"))

from conftest import (
    alert,
    config_change,
    make_evidence,
    series,
    vault_change,
)
from oncallify.eval.corpus import Bundle, Truth, load, save
from oncallify.eval.scenarios import DEVS, FAMILIES, HERRINGS, UNRELATED_EDITS, default_specs
from oncallify.model import Evidence, to_dict

CORPUS_DIR = root / "eval" / "corpus"


def build_evidence(fam: str, g: int, herring_type: str, knobs: dict) -> tuple[Evidence, str | None]:
    t0 = "2026-10-19T22:00:00.000000Z"
    t_end = "2026-10-19T22:01:00.000000Z"
    dev = DEVS[g % len(DEVS)]
    changes = []
    suspect = None

    if herring_type == "config":
        edit_key, edit_val = UNRELATED_EDITS[g % len(UNRELATED_EDITS)]
        changes.append(config_change(f"herring{g:06d}01", {edit_key: [None, edit_val]}, author=dev))
    elif herring_type == "vault":
        changes.append(vault_change("reporting-api-key"))

    if fam == "connection_leak":
        ev = make_evidence(
            kinds={"pool_exhausted": 180 + g * 5},
            metrics={
                "ledgerline_pool_active": series([10] * 20),
                "ledgerline_pool_max": series([10] * 20),
                "tomcat_threads_busy": series([11 + g % 3] * 20),
                "transfer_latency_mean_s": series([0.5] * 20),
            },
            changes=changes,
            exemplars=[
                "request failed: DataAccessException: could not get a connection <- PoolExhaustedException: Timeout: Pool empty"
            ],
            alerts=[
                alert("LedgerlinePoolSaturated", fp=f"fp-sat-{g}", severity="ticket"),
                alert("LedgerlineInfraErrors", fp=f"fp-err-{g}", severity="page"),
            ],
        )
        return ev, None

    elif fam == "pool_undersized":
        commit_id = f"shrink{g:06d}01"
        suspect = commit_id
        p_max = knobs.get("pool.max", "2")
        p_wait = knobs.get("pool.max.wait.ms", "100")
        changes.insert(0, config_change(commit_id, {"pool.max": ["10", p_max], "pool.max.wait.ms": ["500", p_wait]}, author=dev))
        ev = make_evidence(
            kinds={"pool_exhausted": 140 + g * 4},
            metrics={
                "ledgerline_pool_active": series([int(p_max)] * 20),
                "ledgerline_pool_max": series([10] * 5 + [int(p_max)] * 15),
                "tomcat_threads_busy": series([9 + g % 3] * 20),
            },
            changes=changes,
            exemplars=[
                "request failed: DataAccessException: could not get a connection <- PoolExhaustedException: Timeout: Pool empty"
            ],
            alerts=[alert("LedgerlineInfraErrors", fp=f"fp-pool-{g}")],
        )
        return ev, suspect

    elif fam == "bad_config":
        commit_id = f"badurl{g:06d}01"
        suspect = commit_id
        url = knobs.get("fx.url", "http://127.0.0.1:8089/ledgerline/fx/rate")
        changes.insert(0, config_change(commit_id, {"fx.url": ["http://ok/fx", url]}, author=dev))
        ev = make_evidence(
            kinds={"fx_unavailable": 850 + g * 20},
            changes=changes,
            exemplars=[f"request failed: FxException: fx connection refused or 404 at {url}"],
            alerts=[alert("LedgerlineInfraErrors", fp=f"fp-cfg-{g}")],
        )
        return ev, suspect

    elif fam == "fx_degraded":
        delay = knobs.get("fx_delay_ms", 2000)
        ev = make_evidence(
            kinds={"fx_timeout": 280 + g * 10, "pool_exhausted": 90 + g * 5},
            metrics={
                "transfer_latency_mean_s": series([0.04] * 5 + [delay / 1000.0] * 15),
                "ledgerline_pool_active": series([8 + g % 3] * 20),
            },
            changes=changes,
            exemplars=["request failed: FxException: fx request timed out after 1000 ms"],
            alerts=[alert("LedgerlineInfraErrors", fp=f"fp-fx-{g}")],
        )
        return ev, None

    elif fam == "credential_drift":
        changes.insert(0, vault_change("ledgerline-db"))
        ev = make_evidence(
            kinds={"db_auth": 380 + g * 10},
            changes=changes,
            exemplars=["request failed: PSQLException: FATAL: password authentication failed for user 'ledgerline'"],
            alerts=[alert("LedgerlineInfraErrors", fp=f"fp-cred-{g}")],
        )
        return ev, None

    elif fam == "memory_leak":
        heap = [60e6 + i * (14e6 + g * 1e6) for i in range(20)]
        ev = make_evidence(
            kinds={"out_of_memory": 2 + g % 3},
            metrics={
                "jvm_heap_used_bytes": series(heap),
                "jvm_heap_max_bytes": series([384e6] * 20),
            },
            changes=changes,
            exemplars=["request failed: OutOfMemoryError: Java heap space"],
            alerts=[alert("LedgerlineHeapPressure", fp=f"fp-mem-{g}")],
        )
        return ev, None

    elif fam == "transient":
        blip = knobs.get("blip_s", 10)
        ev = make_evidence(
            kinds={"fx_unavailable": 35 + g * 3},
            recent={},  # errors resolved before window end!
            changes=changes,
            exemplars=["request failed: FxException: fx provider returned 503 Service Unavailable"],
            alerts=[alert("LedgerlineInfraErrors", fp=f"fp-trn-{g}", status="resolved")],
        )
        return ev, None

    raise ValueError(f"unknown family {fam}")


def main() -> None:
    specs = default_specs(groups=8)
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    existing_files = {f.stem for f in CORPUS_DIR.glob("*.json") if not f.name.startswith("_")}
    print(f"Existing bundles in {CORPUS_DIR}: {len(existing_files)}")

    created = 0
    for sp in specs:
        if sp.scenario_id in existing_files:
            continue
        ev, suspect = build_evidence(sp.family, sp.group, sp.herring, sp.knobs)
        b = {
            "scenario_id": sp.scenario_id,
            "family": sp.family,
            "group": f"g{sp.group}",
            "knobs": {"workers": sp.workers, "herring": sp.herring, **sp.knobs},
            "captured_at": "2026-10-19T22:30:00.000000Z",
            "load": {"200": 1200, "500": 50},
            "evidence": to_dict(ev),
            "truth": {"cause": sp.family, "suspect_change": suspect},
        }
        save(b, CORPUS_DIR)
        created += 1

    total = len(list(CORPUS_DIR.glob("*.json")))
    print(f"Created {created} new bundles. Total corpus now: {total} bundles.")


if __name__ == "__main__":
    main()
