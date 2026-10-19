"""HTTP clients for the gateway and the watched service. Thin, typed, and bounded:
every call has a timeout, so a sick dependency cannot hang the agent."""

from __future__ import annotations

from typing import Any

import httpx

from oncallify import promtext
from oncallify.model import Alert


class GatewayClient:
    def __init__(self, base_url: str, ingest_token: str, *, timeout: float = 5.0,
                 transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(base_url=base_url, timeout=timeout, transport=transport)
        self._token = ingest_token

    def close(self) -> None:
        self._http.close()

    def _auth(self) -> dict[str, str]:
        return {"x-ingest-token": self._token}

    def post_events(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        r = self._http.post("/v1/events", json={"events": events}, headers=self._auth())
        r.raise_for_status()
        return dict(r.json())

    def events(self, service: str, type_: str, since: str, until: str, limit: int = 5000) -> list[dict[str, Any]]:
        r = self._http.get(
            "/v1/events", params={"service": service, "type": type_, "since": since, "until": until, "limit": limit}
        )
        r.raise_for_status()
        return list(r.json()["events"])

    def alert(self, fingerprint: str) -> Alert | None:
        r = self._http.get(f"/v1/alerts/{fingerprint}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return Alert.from_dict(r.json())

    def alerts(self, status: str | None = None) -> list[Alert]:
        r = self._http.get("/v1/alerts", params={"status": status} if status else None)
        r.raise_for_status()
        return [Alert.from_dict(a) for a in r.json()["alerts"]]

    def put_experiment(self, body: dict[str, Any]) -> dict[str, Any]:
        r = self._http.post("/v1/experiments", json=body, headers=self._auth())
        r.raise_for_status()
        return dict(r.json())

    def assign(self, experiment: str, unit: str) -> str:
        r = self._http.get("/v1/assign", params={"experiment": experiment, "unit": unit})
        r.raise_for_status()
        return str(r.json()["variant"])

    def expose(self, experiment: str, unit: str, variant: str) -> None:
        r = self._http.post(
            "/v1/exposures", json={"experiment": experiment, "unit": unit, "variant": variant}, headers=self._auth()
        )
        r.raise_for_status()

    def experiment_counts(self, experiment: str) -> dict[str, Any]:
        r = self._http.get(f"/v1/experiments/{experiment}/counts")
        r.raise_for_status()
        return dict(r.json())


class ServiceClient:
    """The watched service's own health and metrics endpoints."""

    def __init__(self, base_url: str, *, timeout: float = 3.0, transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(base_url=base_url, timeout=timeout, transport=transport)

    def close(self) -> None:
        self._http.close()

    def metrics(self) -> list[promtext.Sample]:
        r = self._http.get("/metrics")
        r.raise_for_status()
        return promtext.parse(r.text)

    def health(self) -> dict[str, Any]:
        r = self._http.get("/api/health")
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        return {"http_status": r.status_code, **body}
