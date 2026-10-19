from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from oncallify.model import Diagnosis, Evidence


class Diagnoser(Protocol):
    name: str

    def diagnose(self, evidence: Evidence) -> Diagnosis: ...


_FACTORIES: dict[str, Callable[..., Diagnoser]] = {}


def register(name: str) -> Callable[[Callable[..., Diagnoser]], Callable[..., Diagnoser]]:
    def deco(factory: Callable[..., Diagnoser]) -> Callable[..., Diagnoser]:
        _FACTORIES[name] = factory
        return factory

    return deco


def _load_all() -> None:
    # imported for their @register side effects
    from oncallify.diagnose import llm, ml, rules  # noqa: F401


def available_variants() -> list[str]:
    _load_all()
    return sorted(_FACTORIES)


def build(name: str, **kwargs: Any) -> Diagnoser:
    _load_all()
    if name not in _FACTORIES:
        raise KeyError(f"unknown diagnoser variant {name!r}; known: {sorted(_FACTORIES)}")
    return _FACTORIES[name](**kwargs)
