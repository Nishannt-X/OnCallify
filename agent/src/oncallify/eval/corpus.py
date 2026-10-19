"""Replay bundles: one captured incident with its ground truth.

A bundle is what the agent saw (Evidence, built by the same code as live) plus what was
really wrong (we injected it, so we know). Bundles are plain JSON so they can be
reviewed, diffed and committed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oncallify.causes import ACCEPTABLE_ACTIONS, Cause
from oncallify.model import Evidence


@dataclass(slots=True)
class Truth:
    cause: str
    #: for revert fixes: the commit that must be reverted
    suspect_change: str | None = None
    acceptable_actions: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.acceptable_actions:
            self.acceptable_actions = sorted(ACCEPTABLE_ACTIONS[Cause(self.cause)])


@dataclass(slots=True)
class Bundle:
    scenario_id: str
    family: str
    group: str  # variation seed: folds for cross-validation never split a group
    knobs: dict[str, Any]
    captured_at: str
    evidence: Evidence
    truth: Truth

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Bundle:
        t = d["truth"]
        return cls(
            scenario_id=str(d["scenario_id"]),
            family=str(d["family"]),
            group=str(d["group"]),
            knobs=dict(d.get("knobs") or {}),
            captured_at=str(d["captured_at"]),
            evidence=Evidence.from_dict(d["evidence"]),
            truth=Truth(cause=str(t["cause"]), suspect_change=t.get("suspect_change"),
                        acceptable_actions=list(t.get("acceptable_actions") or [])),
        )


def load(directory: Path) -> list[Bundle]:
    files = sorted(f for f in directory.glob("*.json") if not f.name.startswith("_"))
    return [Bundle.from_dict(json.loads(f.read_text(encoding="utf-8"))) for f in files]


def save(bundle_dict: dict[str, Any], directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{bundle_dict['scenario_id']}.json"
    path.write_text(json.dumps(bundle_dict, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return path
