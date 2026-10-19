"""Postmortem draft, generated from the incident record (no model needed, nothing invented).

Every sentence is traceable to a field in the incident: the timeline, the evidence, the
diagnosis, the approval, the verification checks. The "what we still need to do" section
is where a human adds the real fix (a leak is not fixed by a restart).
"""

from __future__ import annotations

from typing import Any

from oncallify.model import Incident

FOLLOW_UPS = {
    "connection_leak": "Find the code path that does not return its connection (pool active stays high while "
                       "request threads are idle). A restart only buys time.",
    "memory_leak": "Take a heap dump on the next occurrence and find what retains memory. A restart only buys time.",
    "pool_undersized": "Add review for pool settings changes; consider a config validation rule for minimum pool "
                       "size.",
    "bad_config": "Validate dependency URLs in CI before config changes ship.",
    "fx_degraded": "Follow up with the FX provider; reconcile transfers flagged fx_fallback=true; turn the flag "
                   "off once the provider is healthy.",
    "credential_drift": "Make the rotation job update every consumer of the secret, or have the app read the "
                        "vault directly.",
    "transient": "None required. If this recurs, raise the alert's for-duration or investigate the provider.",
    "unknown": "Diagnosis was inconclusive; a human investigation is required.",
}


def render(inc: Incident, approvals: list[dict[str, Any]]) -> str:
    d = inc.diagnosis or {}
    cause = str(d.get("cause", "unknown"))
    lines = [
        f"# Postmortem draft: {inc.title}",
        "",
        f"- Incident: `{inc.id}`  ·  service: `{inc.service}`  ·  status: **{inc.status}**",
        f"- Opened: {inc.opened_at}  ·  closed: {inc.closed_at or 'open'}",
        f"- Diagnosis ({d.get('variant', '?')}): **{cause}** at confidence {float(d.get('confidence', 0)):.2f}",
        f"- Why: {d.get('rationale', '')}",
        "",
        "## Timeline",
        "",
    ]
    for t in inc.timeline:
        lines.append(f"- `{t.get('ts', '')}` {t.get('kind', '')}: {t.get('detail', '')}")
    lines += ["", "## What was done", ""]
    if not approvals:
        lines.append("- No change was made to production.")
    for a in approvals:
        result = a.get("execute_result") or {}
        lines.append(
            f"- `{a.get('action')}` {a.get('params')} approved by **{a.get('approver')}** ({a.get('source')}) "
            f"at {a.get('granted_at')}; executed at {a.get('executed_at') or 'never'}: {result.get('detail', '')}"
        )
    if inc.verify:
        lines += ["", "## How recovery was verified", ""]
        for c in inc.verify.get("checks", []):
            lines.append(f"- {'PASS' if c.get('ok') else 'FAIL'} `{c.get('name')}`: {c.get('detail', '')}")
    lines += ["", "## Follow-ups", "", f"- {FOLLOW_UPS.get(cause, FOLLOW_UPS['unknown'])}", ""]
    return "\n".join(lines)
