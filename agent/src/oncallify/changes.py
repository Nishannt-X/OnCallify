"""The change ledger: "what changed?" is the first question in every real incident.

Sources:

* the service's config repository (every config change is a git commit; we read
  ``git log`` and diff the properties file per commit, key by key);
* append-only JSONL feeds for things that are not commits: secret rotations from the
  vault's audit log, and deploys.

The ledger only reports facts. Deciding whether a change is the cause is the
diagnoser's job, and red herrings (unrelated changes in the same window) are expected.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from oncallify.model import Change, iso, parse_iso

_SEP = "\x1f"


def parse_properties(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "!")):
            continue
        for sep in ("=", ":"):
            if sep in line:
                k, v = line.split(sep, 1)
                out[k.strip()] = v.strip()
                break
    return out


def diff_properties(old: dict[str, str], new: dict[str, str]) -> dict[str, list[str | None]]:
    keys = sorted(set(old) | set(new))
    return {k: [old.get(k), new.get(k)] for k in keys if old.get(k) != new.get(k)}


class GitConfigSource:
    """Reads config commits from a git repository holding one properties file."""

    def __init__(self, repo: Path, filename: str = "ledgerline.properties", ignore_authors: Iterable[str] = ()):
        self.repo = repo
        self.filename = filename
        self.ignore_authors = set(ignore_authors)

    def _git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.repo), *args], check=True, capture_output=True, text=True, timeout=10
        ).stdout

    def _content(self, rev: str) -> str:
        try:
            return self._git("show", f"{rev}:{self.filename}")
        except subprocess.CalledProcessError:
            return ""

    def changes(self, since: datetime, until: datetime) -> list[Change]:
        if not (self.repo / ".git").exists():
            return []
        fmt = _SEP.join(["%H", "%P", "%aI", "%an", "%s"])
        log = self._git("log", f"--since={iso(since)}", f"--until={iso(until)}", f"--format={fmt}", "--", self.filename)
        out: list[Change] = []
        for row in log.splitlines():
            parts = row.split(_SEP)
            if len(parts) != 5:
                continue
            sha, parents, ts, author, subject = parts
            if author in self.ignore_authors:
                continue
            parent = parents.split()[0] if parents else None
            old = parse_properties(self._content(parent)) if parent else {}
            new = parse_properties(self._content(sha))
            out.append(
                Change(
                    id=sha[:12],
                    ts=iso(parse_iso(ts)),
                    source="config",
                    author=author,
                    summary=subject,
                    keys=diff_properties(old, new),
                    parent=parent[:12] if parent else None,
                )
            )
        return out


class JsonlSource:
    """Append-only feed: one JSON object per line with ts, id, author, summary, keys."""

    def __init__(self, path: Path, source: str):
        self.path = path
        self.source = source

    def changes(self, since: datetime, until: datetime) -> list[Change]:
        if not self.path.exists():
            return []
        out: list[Change] = []
        for raw in self.path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                d = json.loads(raw)
                ts = parse_iso(str(d["ts"]))
            except (ValueError, KeyError):
                continue
            if since <= ts <= until:
                out.append(
                    Change(
                        id=str(d.get("id", "")),
                        ts=iso(ts),
                        source=self.source,
                        author=str(d.get("author", "")),
                        summary=str(d.get("summary", "")),
                        keys={str(k): list(v) for k, v in (d.get("keys") or {}).items()},
                    )
                )
        return out


def append_jsonl(path: Path, record: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


class ChangeLedger:
    def __init__(self, sources: list[GitConfigSource | JsonlSource]):
        self.sources = sources

    def changes(self, since: datetime, until: datetime) -> list[Change]:
        out: list[Change] = []
        for s in self.sources:
            out.extend(s.changes(since, until))
        return sorted(out, key=lambda c: c.ts)
