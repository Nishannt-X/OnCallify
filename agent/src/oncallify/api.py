"""HTTP API for the dashboard and the gateway.

Read endpoints are open on localhost; every write needs a credential:
* ``POST /alerts`` needs the gateway's ingest token;
* approve / reject / contracts / kill switch need an approver token (``X-Approver-Token``).
"""

from __future__ import annotations

import hmac
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from oncallify import actions
from oncallify.approvals import ApprovalError
from oncallify.diagnose import available_variants
from oncallify.model import Alert, to_dict
from oncallify.pipeline import Pipeline


class ApproveBody(BaseModel):
    proposal_id: str
    confirm: str = Field(min_length=1, max_length=64)


class RejectBody(BaseModel):
    proposal_id: str
    reason: str = Field(min_length=1, max_length=500)


class ContractBody(BaseModel):
    incident_id: str
    days: int = Field(ge=1, le=30)
    max_uses: int = Field(ge=1, le=20)
    min_confidence: float = Field(default=0.8, ge=0.5, le=1.0)


class KillSwitchBody(BaseModel):
    engaged: bool


def create_app(pipeline: Pipeline, *, web_dist: Path | None = None) -> FastAPI:
    app = FastAPI(title="OnCallify", version="1.0.0")
    d = pipeline.d

    def require_ingest(token: str | None) -> None:
        expected = d.settings.ingest_token
        if not expected or not token or not hmac.compare_digest(expected, token):
            raise HTTPException(401, "bad ingest token")

    def require_approver(token: str | None) -> str:
        who = d.approvers.who(token)
        if who is None:
            raise HTTPException(401, "unknown approver token")
        return who

    @app.exception_handler(ApprovalError)
    async def _approval_error(_: Request, exc: ApprovalError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"error": "approval", "message": str(exc)})

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "kill_switch": d.executor.killswitch_engaged(),
                "default_variant": d.settings.default_variant, "live_experiment": d.settings.live_experiment or None}

    @app.post("/alerts", status_code=202)
    def alerts(body: dict[str, Any], x_ingest_token: str | None = Header(default=None)) -> dict[str, str]:
        require_ingest(x_ingest_token)
        try:
            alert = Alert.from_dict(body)
        except (KeyError, ValueError, TypeError) as e:
            raise HTTPException(400, f"bad alert: {e}") from e
        return {"incident_id": pipeline.on_alert(alert)}

    @app.get("/api/incidents")
    def incidents(limit: int = 100) -> dict[str, Any]:
        rows = d.store.list_incidents(limit=min(500, max(1, limit)))
        return {"incidents": [
            {k: v for k, v in to_dict(i).items() if k not in ("evidence", "postmortem")} for i in rows
        ]}

    @app.get("/api/incidents/{incident_id}")
    def incident(incident_id: str) -> dict[str, Any]:
        inc = d.store.get_incident(incident_id)
        if inc is None:
            raise HTTPException(404, "no such incident")
        proposal = d.store.get_proposal(inc.proposal_id) if inc.proposal_id else None
        body = to_dict(inc)
        body["proposal"] = {**to_dict(proposal), "fingerprint": proposal.fingerprint} if proposal else None
        body["approvals"] = to_dict(d.store.list_approvals(incident_id))
        body["transcripts"] = d.transcripts.for_incident(incident_id)[-5:]
        return dict(body)

    @app.post("/api/incidents/{incident_id}/approve")
    def approve(incident_id: str, body: ApproveBody,
                x_approver_token: str | None = Header(default=None)) -> dict[str, str]:
        require_approver(x_approver_token)
        approval_id = pipeline.approve(incident_id, body.proposal_id, token=x_approver_token, confirm=body.confirm)
        return {"approval_id": approval_id, "status": "executing"}

    @app.post("/api/incidents/{incident_id}/reject")
    def reject(incident_id: str, body: RejectBody,
               x_approver_token: str | None = Header(default=None)) -> dict[str, str]:
        require_approver(x_approver_token)
        pipeline.reject(incident_id, body.proposal_id, token=x_approver_token, reason=body.reason)
        return {"status": "rejected"}

    @app.get("/api/contracts")
    def contracts() -> dict[str, Any]:
        return {"contracts": to_dict(d.store.list_contracts())}

    @app.post("/api/contracts", status_code=201)
    def grant(body: ContractBody, x_approver_token: str | None = Header(default=None)) -> dict[str, str]:
        require_approver(x_approver_token)
        cid = pipeline.grant_contract(body.incident_id, token=x_approver_token, days=body.days,
                                      max_uses=body.max_uses, min_confidence=body.min_confidence)
        return {"contract_id": cid}

    @app.delete("/api/contracts/{contract_id}")
    def revoke(contract_id: str, x_approver_token: str | None = Header(default=None)) -> dict[str, str]:
        who = require_approver(x_approver_token)
        if not d.store.revoke_contract(contract_id):
            raise HTTPException(404, "no active contract with that id")
        d.store.audit(who, "contract.revoked", None, {"contract": contract_id})
        return {"status": "revoked"}

    @app.post("/api/killswitch")
    def killswitch(body: KillSwitchBody, x_approver_token: str | None = Header(default=None)) -> dict[str, Any]:
        who = require_approver(x_approver_token)
        f = d.executor.killswitch_file
        if body.engaged:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(f"engaged by {who}\n", encoding="utf-8")
        elif f.exists():
            f.unlink()
        d.store.audit(who, "killswitch", None, {"engaged": body.engaged})
        return {"kill_switch": d.executor.killswitch_engaged()}

    @app.get("/api/safety")
    def safety() -> dict[str, Any]:
        return {
            "kill_switch": d.executor.killswitch_engaged(),
            "allowlist": [
                {"id": s.id, "description": s.description, "params": sorted(s.params), "contractable": s.contractable,
                 "disruptive": s.disruptive}
                for s in actions.REGISTRY.values()
            ],
            "variants": available_variants(),
            "rules": [
                "Only allowlisted actions with exact, validated params can run.",
                "Every action is dry-run by the same runbook, with the same privileges, before it is proposed.",
                "An approval binds to the params hash, is typed as a fingerprint, expires, and is used once.",
                "Contracts cover one rule + cause + action + exact params, for limited uses and days.",
                "Actions whose params change per incident (reverts) can never be covered by a contract.",
                "Recovery needs three checks: alert resolved after the fix, errors quiet, post-condition holds.",
                "The kill switch blocks execution even for approved actions.",
            ],
            "audit": d.store.list_audit(limit=50),
        }

    @app.get("/api/experiments")
    def experiments() -> dict[str, Any]:
        reports = d.store.list_reports()
        live: dict[str, Any] | None = None
        if d.settings.live_experiment:
            try:
                live = d.gateway.experiment_counts(d.settings.live_experiment)
            except Exception as e:  # noqa: BLE001
                live = {"error": str(e)}
        return {"reports": reports, "live": live}

    if web_dist is not None and (web_dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=web_dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            candidate = (web_dist / path).resolve()
            if path and candidate.is_file() and web_dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(web_dist / "index.html")

    return app
