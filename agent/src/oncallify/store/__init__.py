"""Persistence. ``open_store`` picks the backend from a URL:

* ``memory`` - in-process, for tests and the zero-dependency demo
* ``postgresql://...`` - PostgreSQL (incidents, proposals, approvals, contracts, audit)

Transcripts (raw model prompts/responses, high-volume and schemaless) go to a separate
store, see ``transcripts``: MongoDB, IBM Cloudant, or memory.
"""

from oncallify.store.base import Store
from oncallify.store.memory import MemoryStore


def open_store(url: str) -> Store:
    if url == "memory":
        return MemoryStore()
    if url.startswith(("postgresql://", "postgres://")):
        from oncallify.store.postgres import PostgresStore

        return PostgresStore(url)
    raise ValueError(f"unsupported store url: {url!r}")


__all__ = ["MemoryStore", "Store", "open_store"]
