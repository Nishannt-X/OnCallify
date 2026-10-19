"""Log reduction: thousands of lines in, a few dozen templates out.

Variable parts (numbers, ids, hex, quoted strings, thread names) are masked so that
lines produced by the same code path collapse into one template with a count. That is
what makes a two-minute window of logs fit in a prompt and, more importantly, what lets
a person read it at 3 AM. It replaces Beacon's ML-based reducer with a deterministic
one: same input, same summary, which the replay experiments depend on.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from oncallify.model import LogSummary, LogTemplate, parse_iso

_MASKS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\[http-nio-[^\]]*\]"), "[<thread>]"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<uuid>"),
    (re.compile(r"\b(?:acc|inc|evt|req)-[0-9a-z]+\b"), "<id>"),
    (re.compile(r"\b[0-9a-f]{12,}\b"), "<hex>"),
    (re.compile(r"\b\d+(?:\.\d+)?\b"), "<n>"),
    (re.compile(r"'[^']*'"), "'<s>'"),
]

_MAX_TEMPLATES = 12
_MAX_EXEMPLAR = 300


def template_of(text: str) -> str:
    for pattern, repl in _MASKS:
        text = pattern.sub(repl, text)
    return text[:_MAX_EXEMPLAR]


@dataclass(slots=True)
class _Acc:
    count: int
    level: str
    error_kind: str | None
    first_ts: str
    last_ts: str
    exemplar: str


def summarize(lines: Iterable[dict[str, Any]], *, recent_since: str | None = None) -> LogSummary:
    """Summarise structured log lines (dicts with ts, level, msg, optional error_kind/detail)."""
    total = 0
    by_level: Counter[str] = Counter()
    by_kind: Counter[str] = Counter()
    recent: Counter[str] = Counter()
    groups: dict[str, _Acc] = {}
    recent_dt = parse_iso(recent_since) if recent_since else None

    for line in lines:
        total += 1
        level = str(line.get("level", "INFO"))
        by_level[level] += 1
        kind = line.get("error_kind")
        kind_s = str(kind) if kind else None
        ts = str(line.get("ts", ""))
        if kind_s:
            by_kind[kind_s] += 1
            if recent_dt is not None and ts and parse_iso(ts) >= recent_dt:
                recent[kind_s] += 1
        text = str(line.get("msg", ""))
        if line.get("detail"):
            text = f"{text}: {line['detail']}"
        key = f"{level}|{kind_s}|{template_of(text)}"
        acc = groups.get(key)
        if acc is None:
            groups[key] = _Acc(1, level, kind_s, ts, ts, text[:_MAX_EXEMPLAR])
        else:
            acc.count += 1
            acc.last_ts = max(acc.last_ts, ts)
            acc.first_ts = min(acc.first_ts, ts) if acc.first_ts else ts

    # errors first, then by frequency; a rare ERROR beats a common INFO
    def rank(item: tuple[str, _Acc]) -> tuple[int, int]:
        _, a = item
        return (0 if a.level in ("ERROR", "WARN") else 1, -a.count)

    templates = [
        LogTemplate(
            template=key.split("|", 2)[2],
            count=a.count,
            level=a.level,
            error_kind=a.error_kind,
            first_ts=a.first_ts,
            last_ts=a.last_ts,
            exemplar=a.exemplar,
        )
        for key, a in sorted(groups.items(), key=rank)[:_MAX_TEMPLATES]
    ]
    return LogSummary(
        total_lines=total,
        by_level=dict(by_level),
        by_error_kind=dict(by_kind),
        templates=templates,
        recent_error_kinds=dict(recent),
    )
