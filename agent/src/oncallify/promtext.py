"""Parser for the Prometheus text exposition format (the subset real exporters emit)."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

_LINE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{(.*)\})?\s+(\S+)(\s+\d+)?$")
_LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')


@dataclass(frozen=True, slots=True)
class Sample:
    name: str
    labels: dict[str, str]
    value: float

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "labels": dict(self.labels), "value": self.value}


def _unescape(v: str) -> str:
    return v.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")


def parse(text: str) -> list[Sample]:
    out: list[Sample] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        name, _, label_text, value_text = m.group(1), m.group(2), m.group(3), m.group(4)
        try:
            value = float(value_text)
        except ValueError:
            continue
        if math.isnan(value):
            continue
        labels = {k: _unescape(v) for k, v in _LABEL.findall(label_text or "")}
        out.append(Sample(name, labels, value))
    return out
