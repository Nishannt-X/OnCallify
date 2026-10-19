"""Closed-loop traffic for Ledgerline: N workers, each sending one request at a time.

Closed-loop (fixed concurrency) rather than fixed rate on purpose: it is what makes a
too-small connection pool hurt, because demand for connections equals worker count.
About 2% of transfers are deliberately unaffordable, so there is always some business
noise (422s) that a good diagnoser must ignore.
"""

from __future__ import annotations

import random
import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field

import httpx


@dataclass(slots=True)
class LoadStats:
    statuses: Counter[int] = field(default_factory=Counter)
    errors: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, status: int | None) -> None:
        with self.lock:
            if status is None:
                self.errors += 1
            else:
                self.statuses[status] += 1


class LoadGenerator:
    def __init__(self, base_url: str, workers: int = 10, think_ms: int = 20, seed: int = 0,
                 accounts: int = 60):
        self.base_url = base_url
        self.workers = workers
        self.think_ms = think_ms
        self.seed = seed
        self.accounts = accounts
        self.stats = LoadStats()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def _worker(self, idx: int) -> None:
        rng = random.Random(self.seed * 1000 + idx)
        with httpx.Client(base_url=self.base_url, timeout=5.0) as http:
            while not self._stop.is_set():
                try:
                    if rng.random() < 0.3:
                        r = http.get(f"/api/accounts/acc-{rng.randint(1, self.accounts):03d}")
                    else:
                        a, b = rng.sample(range(1, self.accounts + 1), 2)
                        amount = "99999999.00" if rng.random() < 0.02 else f"{rng.randint(1, 500)}.00"
                        r = http.post(
                            "/api/transfers",
                            json={"from": f"acc-{a:03d}", "to": f"acc-{b:03d}", "amount": amount},
                            headers={"Idempotency-Key": uuid.uuid4().hex},
                        )
                    self.stats.record(r.status_code)
                except httpx.HTTPError:
                    self.stats.record(None)
                    time.sleep(0.05)
                self._stop.wait(self.think_ms / 1000)

    def start(self) -> LoadGenerator:
        self._stop.clear()
        self._threads = [threading.Thread(target=self._worker, args=(i,), daemon=True) for i in range(self.workers)]
        for t in self._threads:
            t.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=10)
