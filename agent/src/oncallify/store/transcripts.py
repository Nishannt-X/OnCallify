"""Transcript store: the raw record of every diagnosis (prompt, response, timings).

High-volume, append-only, schemaless documents, which is exactly what a document store
is for. Backends:

* memory
* MongoDB (``mongodb://...``)
* IBM Cloudant (``cloudant+https://ACCOUNT.cloudant.com/DB``), via its CouchDB-compatible
  HTTP API with an IAM API key from ``CLOUDANT_APIKEY``. Cloudant's Lite plan is free.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Protocol

import httpx


class TranscriptStore(Protocol):
    def put(self, doc: dict[str, Any]) -> str: ...
    def for_incident(self, incident_id: str) -> list[dict[str, Any]]: ...


class MemoryTranscripts:
    def __init__(self) -> None:
        self._docs: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def put(self, doc: dict[str, Any]) -> str:
        with self._lock:
            doc_id = doc.get("_id") or f"t-{len(self._docs) + 1}"
            self._docs.append({**doc, "_id": doc_id})
            return str(doc_id)

    def for_incident(self, incident_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(d) for d in self._docs if d.get("incident_id") == incident_id]


class MongoTranscripts:
    def __init__(self, url: str, db: str = "oncallify", client: Any | None = None):
        if client is None:
            from pymongo import MongoClient

            client = MongoClient(url, serverSelectionTimeoutMS=5000)
        self._col = client[db]["transcripts"]
        self._col.create_index("incident_id")

    def put(self, doc: dict[str, Any]) -> str:
        res = self._col.insert_one(dict(doc))
        return str(res.inserted_id)

    def for_incident(self, incident_id: str) -> list[dict[str, Any]]:
        return [{**d, "_id": str(d["_id"])} for d in self._col.find({"incident_id": incident_id}).sort("ts", 1)]


class CloudantTranscripts:
    """IBM Cloudant over HTTP. Exchanges the API key for an IAM bearer token and caches
    it until shortly before expiry."""

    IAM_URL = "https://iam.cloud.ibm.com/identity/token"

    def __init__(self, base_url: str, db: str, apikey: str, *, transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(base_url=base_url, timeout=15.0, transport=transport)
        self._iam = httpx.Client(timeout=15.0, transport=transport)
        self._db = db
        self._apikey = apikey
        self._token: str | None = None
        self._token_exp = 0.0

    @classmethod
    def from_url(cls, url: str) -> CloudantTranscripts:
        rest = url.removeprefix("cloudant+")
        base, _, db = rest.rpartition("/")
        apikey = os.environ.get("CLOUDANT_APIKEY", "")
        if not apikey:
            raise ValueError("CLOUDANT_APIKEY is required for the Cloudant transcript store")
        return cls(base, db, apikey)

    def _auth(self) -> dict[str, str]:
        if self._token is None or time.time() > self._token_exp - 60:
            r = self._iam.post(self.IAM_URL, data={
                "grant_type": "urn:ibm:params:oauth:grant-type:apikey", "apikey": self._apikey,
            }, headers={"Accept": "application/json"})
            r.raise_for_status()
            body = r.json()
            self._token = str(body["access_token"])
            self._token_exp = float(body.get("expiration", time.time() + 3600))
        return {"Authorization": f"Bearer {self._token}"}

    def put(self, doc: dict[str, Any]) -> str:
        r = self._http.post(f"/{self._db}", json=doc, headers=self._auth())
        r.raise_for_status()
        return str(r.json()["id"])

    def for_incident(self, incident_id: str) -> list[dict[str, Any]]:
        r = self._http.post(f"/{self._db}/_find", json={"selector": {"incident_id": incident_id}, "limit": 200},
                            headers=self._auth())
        r.raise_for_status()
        return list(r.json().get("docs", []))


def open_transcripts(url: str) -> TranscriptStore:
    if url == "memory":
        return MemoryTranscripts()
    if url.startswith("mongodb"):
        return MongoTranscripts(url)
    if url.startswith("cloudant+"):
        return CloudantTranscripts.from_url(url)
    raise ValueError(f"unsupported transcripts url: {url!r}")
