"""End-to-end demo against the running local stack (deploy/local/stack.sh up).

Night 1: Ledgerline leaks connections. OnCallify diagnoses, proposes restart_app, a
         human approves by typing the fingerprint, the fix runs and is verified.
         The human then grants a standing contract for that exact fix.
Night 2: the same fault returns. The contract covers it: fixed and verified, nobody paged.

Usage: python3 scripts/e2e_demo.py [--approver-token TOKEN] [--fault connection_leak]
Prints every timeline step; exits non-zero if either night does not end resolved.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
RUN = Path(os.environ.get("ONCALLIFY_RUN", ROOT / ".run"))
AGENT = os.environ.get("AGENT_URL", "http://127.0.0.1:7080")
GATEWAY = os.environ.get("GATEWAY_URL", "http://127.0.0.1:7070")
LEDGERLINE = os.environ.get("LEDGERLINE_URL", "http://127.0.0.1:8080/ledgerline")


def say(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')}  {msg}", flush=True)


def wait_quiet(http: httpx.Client, timeout: float = 180) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not http.get(f"{GATEWAY}/v1/alerts", params={"status": "firing"}).json()["alerts"]:
            return
        time.sleep(3)
    raise SystemExit("alerts did not clear")


def new_incident(http: httpx.Client, known: set[str], until: set[str], timeout: float = 240) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for inc in http.get(f"{AGENT}/api/incidents").json()["incidents"]:
            if inc["id"] not in known and inc["status"] in until:
                return http.get(f"{AGENT}/api/incidents/{inc['id']}").json()
        time.sleep(3)
    raise SystemExit(f"no new incident reached {until}")


def show(inc: dict) -> None:
    for t in inc["timeline"]:
        print(f"           {t['ts'][11:19]}  {t['kind']:<24} {t['detail'][:100]}")


def night(http: httpx.Client, fault: str, admin: str, token: str, *, expect_contract: bool) -> dict:
    wait_quiet(http)
    known = {i["id"] for i in http.get(f"{AGENT}/api/incidents").json()["incidents"]}
    load = subprocess.Popen([sys.executable, "-m", "oncallify.cli", "loadgen", "--workers", "10", "--seconds", "240"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(15)
        http.post(f"{LEDGERLINE}/admin/faults", headers={"X-Admin-Token": admin},
                  json={"fault": fault, "enabled": True}).raise_for_status()
        say(f"injected {fault}")
        if expect_contract:
            inc = new_incident(http, known, {"resolved", "escalated", "awaiting_approval"})
        else:
            inc = new_incident(http, known, {"awaiting_approval", "escalated"})
            if inc["status"] == "awaiting_approval":
                p = inc["proposal"]
                say(f"proposal: {p['action']} {p['params']}  ->  human types fingerprint {p['fingerprint']}")
                http.post(f"{AGENT}/api/incidents/{inc['id']}/approve", headers={"X-Approver-Token": token},
                          json={"proposal_id": p["id"], "confirm": p["fingerprint"]}).raise_for_status()
            deadline = time.time() + 300
            while time.time() < deadline and inc["status"] not in ("resolved", "escalated"):
                time.sleep(4)
                inc = http.get(f"{AGENT}/api/incidents/{inc['id']}").json()
    finally:
        load.terminate()
    show(inc)
    return inc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--approver-token", default=os.environ.get("ONCALLIFY_APPROVER_TOKEN", ""))
    ap.add_argument("--fault", default="connection_leak")
    a = ap.parse_args()
    if not a.approver_token:
        print("pass --approver-token (create one: oncallify approver --name you)", file=sys.stderr)
        return 2
    admin = (RUN / "vault" / "ledgerline-admin-token").read_text().strip()
    http = httpx.Client(timeout=15)

    say("NIGHT 1: a human is paged")
    one = night(http, a.fault, admin, a.approver_token, expect_contract=False)
    say(f"night 1 ended: {one['status']}")
    if one["status"] != "resolved":
        return 1
    r = http.post(f"{AGENT}/api/contracts", headers={"X-Approver-Token": a.approver_token},
                  json={"incident_id": one["id"], "days": 7, "max_uses": 3, "min_confidence": 0.7})
    r.raise_for_status()
    say(f"human grants contract {r.json()['contract_id']} for this exact fix (7 days, 3 uses)")

    say("NIGHT 2: the same fault returns")
    two = night(http, a.fault, admin, a.approver_token, expect_contract=True)
    approvals = [(x["source"], x["approver"]) for x in two["approvals"]]
    say(f"night 2 ended: {two['status']}  approvals={approvals}")
    contracts = http.get(f"{AGENT}/api/contracts").json()["contracts"]
    print(json.dumps([{k: c[k] for k in ("id", "action", "uses", "max_uses", "status")} for c in contracts], indent=1))
    return 0 if two["status"] == "resolved" and approvals and approvals[0][0] == "contract" else 1


if __name__ == "__main__":
    raise SystemExit(main())
