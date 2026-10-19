"""Diagnosers turn Evidence into a Diagnosis (cause + suspect change + confidence).

They never choose the action: the planner does, from the cause, through the allowlist.
That separation is what makes variants comparable in experiments and is a safety
property: a diagnoser that hallucinates cannot invent a new action.
"""

from oncallify.diagnose.base import Diagnoser, available_variants, build

__all__ = ["Diagnoser", "available_variants", "build"]
