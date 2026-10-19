"""Ships the service's logs and metrics to the gateway.

* Logs: tails the JSONL file. The event id is derived from (file identity, byte
  offset), so re-sending after a crash or a network error is harmless: the gateway
  drops ids it has already stored. Rotation/truncation is detected by inode or size.
* Metrics: scrapes /metrics every interval; one event per scrape, id from (service, ts).

At-least-once delivery plus idempotent ingest gives effectively-once storage.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from oncallify.clients import GatewayClient, ServiceClient
from oncallify.model import iso, utcnow

logger = logging.getLogger(__name__)


def _eid(*parts: object) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:32]


@dataclass(slots=True)
class _Cursor:
    inode: int = -1
    offset: int = 0


class LogTailer:
    def __init__(self, path: Path, service: str):
        self.path = path
        self.service = service
        self.cursor = _Cursor()
        self._partial = b""

    def read_new(self) -> list[dict[str, Any]]:
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return []
        if st.st_ino != self.cursor.inode or st.st_size < self.cursor.offset:
            # new file or truncated: start from the beginning of the new file
            self.cursor = _Cursor(inode=st.st_ino, offset=0)
            self._partial = b""
        if st.st_size == self.cursor.offset:
            return []
        with self.path.open("rb") as fh:
            fh.seek(self.cursor.offset)
            chunk = fh.read(min(st.st_size - self.cursor.offset, 4 * 1024 * 1024))
        start_offset = self.cursor.offset - len(self._partial)
        data = self._partial + chunk
        lines = data.split(b"\n")
        self._partial = lines.pop()  # incomplete last line waits for the next read
        self.cursor.offset += len(chunk)
        events: list[dict[str, Any]] = []
        pos = start_offset
        for raw in lines:
            line_offset = pos
            pos += len(raw) + 1
            if not raw.strip():
                continue
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                body = {"level": "WARN", "msg": "unparseable log line", "raw": raw[:500].decode(errors="replace")}
            ts = str(body.get("ts") or iso(utcnow()))
            events.append({
                "event_id": _eid(self.service, self.cursor.inode, line_offset),
                "ts": ts,
                "service": self.service,
                "type": "log",
                "body": body,
            })
        return events


class Collector:
    def __init__(self, gateway: GatewayClient, service_client: ServiceClient, log_path: Path,
                 service: str = "ledgerline", interval_s: float = 1.0):
        self.gateway = gateway
        self.service_client = service_client
        self.tailer = LogTailer(log_path, service)
        self.service = service
        self.interval_s = interval_s
        self._pending: list[dict[str, Any]] = []
        self._stop = threading.Event()

    def scrape(self) -> dict[str, Any]:
        """One metrics event per interval. A failed scrape is itself a sample
        (``scrape_failed=1``): a service that dies or hangs stops reporting, and
        silence must be able to page (the Prometheus ``up`` idea)."""
        ts = iso(utcnow())
        try:
            samples = [s.to_dict() for s in self.service_client.metrics()]
            samples.append({"name": "scrape_failed", "labels": {}, "value": 0.0})
        except httpx.HTTPError as e:
            logger.warning("scrape failed: %s", e)
            samples = [{"name": "scrape_failed", "labels": {"reason": type(e).__name__}, "value": 1.0}]
        return {
            "event_id": _eid(self.service, "metrics", ts),
            "ts": ts,
            "service": self.service,
            "type": "metrics",
            "body": {"samples": samples},
        }

    def tick(self) -> dict[str, Any]:
        batch = self.tailer.read_new()
        batch.append(self.scrape())
        self._pending.extend(batch)
        if not self._pending:
            return {"sent": 0}
        sent = 0
        result: dict[str, Any] = {}
        # bounded batches; on failure everything stays pending and is retried (same ids)
        while self._pending:
            chunk = self._pending[:500]
            try:
                result = self.gateway.post_events(chunk)
            except httpx.HTTPError as e:
                logger.warning("gateway post failed, will retry %d events: %s", len(self._pending), e)
                if len(self._pending) > 50_000:
                    dropped = len(self._pending) - 50_000
                    self._pending = self._pending[-50_000:]
                    logger.error("collector backlog full, dropped %d oldest events", dropped)
                break
            del self._pending[: len(chunk)]
            sent += len(chunk)
        return {"sent": sent, "pending": len(self._pending), **result}

    def run(self) -> None:
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(self.interval_s)

    def stop(self) -> None:
        self._stop.set()
