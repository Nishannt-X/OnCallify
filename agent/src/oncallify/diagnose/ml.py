"""A learned diagnoser: multinomial logistic regression over the named features.

Why logistic regression and not a bigger model: ~60 labelled incidents. With that much
data a linear model with standardised inputs is the honest choice, it is calibrated
enough to give a usable confidence, and its coefficients can be read as "which signals
pushed it toward this cause" (see ``explain``).

The trained model is stored as JSON (coefficients, scaler, class order). No pickle:
loading a model file can never execute code.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from oncallify import features
from oncallify.causes import NEEDS_SUSPECT_CHANGE, Cause
from oncallify.diagnose.base import register
from oncallify.model import Diagnosis, Evidence

DEFAULT_MODEL_PATH = Path(__file__).resolve().parent / "models" / "logreg.json"


def vectorize(ev: Evidence) -> np.ndarray:
    f = features.extract(ev)
    return np.array([f[name] for name in features.FEATURE_NAMES], dtype=float)


def train(evidences: list[Evidence], labels: list[str], *, c: float = 1.0, seed: int = 0) -> dict[str, Any]:
    """Fit and return a JSON-serialisable model."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    x = np.vstack([vectorize(e) for e in evidences])
    scaler = StandardScaler().fit(x)
    scale = np.where(scaler.scale_ == 0, 1.0, scaler.scale_)
    clf = LogisticRegression(C=c, max_iter=5000, class_weight="balanced", random_state=seed)
    clf.fit((x - scaler.mean_) / scale, labels)
    coef = clf.coef_
    intercept = clf.intercept_
    classes = [str(k) for k in clf.classes_]
    if len(classes) == 2:  # sklearn stores one row for binary problems
        coef = np.vstack([-coef[0], coef[0]])
        intercept = np.array([-intercept[0], intercept[0]])
    return {
        "kind": "logreg",
        "features": list(features.FEATURE_NAMES),
        "classes": classes,
        "mean": scaler.mean_.tolist(),
        "scale": scale.tolist(),
        "coef": coef.tolist(),
        "intercept": intercept.tolist(),
        "c": c,
        "n_train": len(labels),
    }


class LogRegDiagnoser:
    name = "ml-logreg"

    def __init__(self, model: dict[str, Any]):
        if model.get("features") != list(features.FEATURE_NAMES):
            raise ValueError("model was trained on a different feature set; retrain it")
        self.model = model
        self.classes: list[str] = list(model["classes"])
        self.mean = np.array(model["mean"])
        self.scale = np.array(model["scale"])
        self.coef = np.array(model["coef"])
        self.intercept = np.array(model["intercept"])

    @classmethod
    def from_file(cls, path: Path) -> LogRegDiagnoser:
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def probabilities(self, ev: Evidence) -> dict[str, float]:
        z = (vectorize(ev) - self.mean) / self.scale
        logits = self.coef @ z + self.intercept
        logits -= logits.max()
        p = np.exp(logits)
        p /= p.sum()
        return {c: float(v) for c, v in zip(self.classes, p, strict=True)}

    def explain(self, ev: Evidence, cause: str, top: int = 4) -> list[str]:
        """The features that pushed hardest toward *cause* for this evidence."""
        z = (vectorize(ev) - self.mean) / self.scale
        contrib = self.coef[self.classes.index(cause)] * z
        order = np.argsort(-contrib)[:top]
        names = self.model["features"]
        return [f"{names[i]} (+{contrib[i]:.2f})" for i in order if contrib[i] > 0]

    def diagnose(self, evidence: Evidence) -> Diagnosis:
        t0 = time.perf_counter()
        probs = self.probabilities(evidence)
        cause = max(probs, key=lambda k: probs[k])
        conf = probs[cause]
        suspect = None
        parsed = Cause(cause) if cause in Cause._value2member_map_ else Cause.UNKNOWN
        if parsed in NEEDS_SUSPECT_CHANGE:
            sc = features.suspect_changes(evidence)
            pick = sc.pool_shrink if parsed is Cause.POOL_UNDERSIZED else sc.fx_url
            if pick is None:
                configs = [c for c in evidence.changes if c.source == "config"]
                pick = configs[-1] if configs else None
            suspect = pick.id if pick else None
        why = "; ".join(self.explain(evidence, cause)) or "no single dominant signal"
        return Diagnosis(
            cause=parsed.value, confidence=round(conf, 3), rationale=f"top signals: {why}",
            evidence_refs=[], suspect_change=suspect, variant=self.name,
            latency_ms=(time.perf_counter() - t0) * 1000,
            notes=[f"p({k})={v:.2f}" for k, v in sorted(probs.items(), key=lambda kv: -kv[1])[:3]],
        )


@register("ml-logreg")
def _factory(model: dict[str, Any] | None = None, model_path: str | None = None, **_: Any) -> LogRegDiagnoser:
    if model is not None:
        return LogRegDiagnoser(model)
    path = Path(model_path) if model_path else DEFAULT_MODEL_PATH
    if not path.exists():
        raise FileNotFoundError(f"no trained model at {path}; run `oncallify eval train`")
    return LogRegDiagnoser.from_file(path)
