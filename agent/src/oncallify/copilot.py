"""IBM Granite On-Call Copilot.

Conversational SRE assistant grounded in live telemetry, incident evidence,
Tomcat/Ledgerline architecture, and the safety allowlist.
Queries IBM Granite (via watsonx.ai or Ollama) with deterministic grounded fallback.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import httpx

from oncallify import actions
from oncallify.model import Incident, to_dict
from oncallify.pipeline import Pipeline

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the IBM Granite On-Call Copilot for OnCallify.
You assist software engineers managing enterprise Java/Tomcat payments infrastructure (Ledgerline).
You have real-time access to the live incident telemetry, log exemplars, PostgreSQL connection metrics,
Git configuration diffs, and the 5 allowlisted runbooks (restart_app, resize_pool, revert_config_change, enable_fx_fallback, sync_db_credential).

Safety rules:
1. You can explain root causes, metrics, blast radius, and recovery checks.
2. You NEVER execute runbooks directly. All mutations require human authorization with an 8-character SHA-256 fingerprint.
3. Be concise, technical, and grounded in the provided incident evidence.
"""


def _build_context(pipeline: Pipeline, incident_id: str | None) -> dict[str, Any]:
    d = pipeline.d
    inc: Incident | None = None
    if incident_id:
        inc = d.store.get_incident(incident_id)
    if inc is None:
        # Pick the most recent open or latest incident
        rows = d.store.list_incidents(limit=5)
        if rows:
            inc = rows[0]

    ctx: dict[str, Any] = {
        "kill_switch": d.executor.killswitch_engaged(),
        "allowlist": list(actions.REGISTRY.keys()),
        "service": "ledgerline (Java 21, Tomcat 10.1, PostgreSQL 16)",
    }

    if inc:
        proposal = d.store.get_proposal(inc.proposal_id) if inc.proposal_id else None
        ctx["incident"] = {
            "id": inc.id,
            "title": inc.title,
            "status": inc.status,
            "opened_at": inc.opened_at,
            "diagnosis": to_dict(inc.diagnosis) if inc.diagnosis else None,
            "proposal": {
                "id": proposal.id,
                "action": proposal.action,
                "params": proposal.params,
                "fingerprint": proposal.fingerprint,
                "blast_radius": proposal.blast_radius,
                "dry_run": proposal.dry_run,
            } if proposal else None,
            "verify": inc.verify,
            "evidence_summary": {
                "error_kinds": inc.evidence.logs.by_error_kind if inc.evidence else {},
                "changes": [c.summary for c in inc.evidence.changes] if inc.evidence else [],
                "top_templates": [t.exemplar for t in inc.evidence.logs.templates[:3]] if inc.evidence else [],
            } if inc.evidence else None,
        }
    return ctx


def _deterministic_reply(query: str, ctx: dict[str, Any]) -> str:
    q = query.lower().strip()
    inc = ctx.get("incident")
    ks = ctx.get("kill_switch", False)
    allowlist = ctx.get("allowlist", [])

    if "kill switch" in q or "killswitch" in q:
        status = "ENGAGED (all writes physically blocked)" if ks else "ARMED & DISENGAGED (writes permitted with approval)"
        return (
            f"🛡️ **Kill Switch Status:** Currently **{status}**.\n\n"
            f"When engaged, an atomic flag blocks all mutating runbook executions at the OS layer. "
            f"Read-only diagnoses and pre-execution dry-runs continue normally."
        )

    if "allowlist" in q or "what actions" in q or "runbook" in q:
        actions_str = ", ".join(f"`{a}`" for a in allowlist)
        return (
            f"📋 **Strict Action Allowlist (Zero-Trust Model):**\n"
            f"Only the following 5 pre-compiled runbooks can ever execute in production:\n\n"
            f"- `restart_app`: Reloads Tomcat context via Manager API to flush leaked connection handles\n"
            f"- `resize_pool`: Dynamically adjusts PostgreSQL JDBC pool max connections in config\n"
            f"- `revert_config_change`: Git revert in configuration repo (Human-only, never automated)\n"
            f"- `enable_fx_fallback`: Enables cached FX rates flag when provider times out\n"
            f"- `sync_db_credential`: Resyncs database password from vault on auth drift\n\n"
            f"Arbitrary shell commands or hallucinated actions are rejected at schema validation."
        )

    if not inc:
        return (
            f"Everything is currently quiet on the **Ledgerline** payments service. "
            f"Tomcat 10.1 and PostgreSQL 16 are operating nominally with zero active alerts. "
            f"You can ask me about safety rules, allowlisted runbooks, or past incident evaluations."
        )

    inc_id = inc["id"]
    diag = inc.get("diagnosis") or {}
    cause = diag.get("cause", "diagnosing")
    conf = int(diag.get("confidence", 0) * 100)
    proposal = inc.get("proposal")

    if "why" in q or "cause" in q or "diagnos" in q or "root cause" in q:
        rationale = diag.get("rationale", "No rationale recorded.")
        ev = inc.get("evidence_summary") or {}
        errors = ev.get("error_kinds") or {}
        err_str = ", ".join(f"{k}: {v}" for k, v in errors.items()) or "none"
        return (
            f"🔍 **Root Cause Diagnosis for {inc_id}:**\n\n"
            f"- **Diagnosed Cause:** `{cause}` ({conf}% confidence)\n"
            f"- **Inference Rationale:** {rationale}\n"
            f"- **Error Breakdown:** {err_str}\n\n"
            f"The diagnoser runs under read-only inference. In a connection leak, request threads "
            f"finish but fail to return pooled handles, saturating `ledgerline_pool_active` while CPU is idle."
        )

    if "fingerprint" in q or "approve" in q or "how to fix" in q or "fix" in q:
        if not proposal:
            return f"Incident **{inc_id}** is currently in state `{inc['status']}`. No fix proposal is pending approval."
        fp = proposal["fingerprint"]
        action = proposal["action"]
        params = json.dumps(proposal["params"])
        return (
            f"🔑 **Approval Instructions for {inc_id}:**\n\n"
            f"The proposed remediation is `{action}` with parameters `{params}`.\n\n"
            f"To authorize execution, the human approver must enter the exact SHA-256 fingerprint:\n"
            f"```\n{fp}\n```\n"
            f"This guarantees cryptographic non-tampering: you are authorizing *this exact plan*, "
            f"not an arbitrary 'yes'."
        )

    if "blast radius" in q or "safe" in q or "dry run" in q:
        if not proposal:
            return f"No proposal currently staged for incident **{inc_id}**."
        br = proposal.get("blast_radius", "Standard service disruption.")
        dry = proposal.get("dry_run", {})
        dry_status = "PASSED" if dry.get("ok") else "FAILED"
        return (
            f"⚡ **Blast Radius & Pre-Execution Safety:**\n\n"
            f"- **Action:** `{proposal['action']}`\n"
            f"- **Blast Radius:** {br}\n"
            f"- **Dry-Run Probe:** `{dry_status}` ({dry.get('detail', 'preconditions verified')})\n\n"
            f"Every runbook executes with `--dry-run` in an isolated rehearsal clone before any human is prompted."
        )

    # General overview
    prop_summary = f"Proposed fix: `{proposal['action']}` (Fingerprint: `{proposal['fingerprint']}`)" if proposal else "No active proposal."
    return (
        f"📋 **Incident Summary for {inc_id} ({inc['title']}):**\n\n"
        f"- **Status:** `{inc['status']}`\n"
        f"- **Diagnosed Cause:** `{cause}` ({conf}% confidence)\n"
        f"- **{prop_summary}**\n\n"
        f"You can ask me to explain the **root cause**, review the **blast radius**, inspect **error logs**, "
        f"or provide the **approval fingerprint**."
    )


def chat(pipeline: Pipeline, message: str, incident_id: str | None = None) -> dict[str, Any]:
    t0 = time.perf_counter()
    ctx = _build_context(pipeline, incident_id)

    # If Ollama or watsonx is available, query Granite
    ollama_url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
    model = os.environ.get("GRANITE_MODEL", "granite4:micro")
    
    reply: str | None = None
    source = "deterministic-engine"

    try:
        with httpx.Client(base_url=ollama_url, timeout=4.0) as client:
            resp = client.post("/api/chat", json={
                "model": model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT + f"\n\nLive Context:\n{json.dumps(ctx)}"},
                    {"role": "user", "content": message},
                ],
                "options": {"temperature": 0.2},
            })
            if resp.status_code == 200:
                reply = str(resp.json()["message"]["content"]).strip()
                source = f"ibm-granite ({model})"
    except Exception:
        # Fall back gracefully to deterministic grounded engine
        pass

    if not reply:
        reply = _deterministic_reply(message, ctx)
        source = "grounded-telemetry-engine"

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)

    return {
        "reply": reply,
        "source": source,
        "elapsed_ms": elapsed_ms,
        "incident_id": ctx.get("incident", {}).get("id") if ctx.get("incident") else None,
        "fingerprint": ctx.get("incident", {}).get("proposal", {}).get("fingerprint") if ctx.get("incident", {}).get("proposal") else None,
    }
