"""Versioned RandomForest match-probability model (MRM-governed).

This is the ML core the :class:`CounterpartyMatcher` deduplicator consumes. It maps a
*multi-signal* feature vector (fuzzy + phonetic + national-id + address + account
signals — none singly authoritative) to a calibrated probability that two counterparty
records denote the same real-world entity, per the build brief.

Governance (``data_engine.md`` §12.5 / §7.4):
  * **Versioned** — ``MODEL_ID`` / ``MODEL_VERSION`` + a :class:`ModelCard` on the artifact.
  * **Contract** — :data:`SIGNAL_FEATURES` is the fixed, ordered input contract; the
    output is a match probability in ``[0, 1]`` plus a certainty (model confidence).
  * **Confidence** — every prediction carries a certainty derived from class agreement.
  * **Human-overridable** — an :class:`OverrideRegistry` can pin the decision for a pair.
  * **Degrades safely** — with no fitted forest (or sklearn absent) it falls back to a
    documented deterministic weighted blend of the signals, at capped certainty, so the
    deduplicator is never hard-blocked on a trained artifact (mirrors the behavioral
    estimator's baseline fallback).

scikit-learn is imported lazily inside the fit/predict paths so importing this module
never pays for it and a broken install degrades to the heuristic instead of crashing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from app.etl.models._mrm import (
    DEFAULT_ARTIFACT_DIR,
    HumanOverride,
    ModelCard,
    ModelUnavailableError,
    OverrideRegistry,
    load_artifact,
    save_artifact,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sklearn.ensemble import RandomForestClassifier

MODEL_ID = "counterparty_matching_model"
MODEL_VERSION = "1.0.0"

# The ordered input contract. Every signal is a similarity/agreement score; missing
# evidence is encoded as 0.0 (no evidence), and *disagreeing* hard identifiers as -1.0
# (negative evidence) so the forest can separate "unknown" from "contradicted".
SIGNAL_FEATURES: tuple[str, ...] = (
    "token_sort_ratio",
    "token_set_ratio",
    "partial_ratio",
    "jaro_winkler",
    "tfidf_cosine",
    "levenshtein_norm",
    "phonetic_soundex",
    "phonetic_metaphone",
    "phonetic_double_metaphone",
    "national_id",
    "address_similarity",
    "account_overlap",
    "country_match",
    "type_match",
)

OUTPUT_NAME = "match_probability"

# Deterministic fallback weights over the signal contract, used only when no forest is
# fitted. Name/phonetic evidence dominates; hard identifiers (id/account) get high weight
# but are frequently absent in retail data, so the blend never *requires* them.
_FALLBACK_WEIGHTS: dict[str, float] = {
    "token_sort_ratio": 1.4,
    "token_set_ratio": 1.4,
    "partial_ratio": 0.6,
    "jaro_winkler": 1.2,
    "tfidf_cosine": 1.2,
    "levenshtein_norm": 0.8,
    "phonetic_soundex": 0.7,
    "phonetic_metaphone": 0.9,
    "phonetic_double_metaphone": 0.7,
    "national_id": 2.5,
    "address_similarity": 0.8,
    "account_overlap": 2.0,
    "country_match": 0.3,
    "type_match": 0.3,
}
_FALLBACK_CERTAINTY_CAP = 0.85


@dataclass(frozen=True)
class MatchPrediction:
    """One model output: the match probability plus its governance envelope."""

    probability: float
    certainty: float
    method: str  # "random_forest" | "heuristic_blend" | "human_override"
    model_id: str
    model_version: str
    features: dict[str, float]
    override: HumanOverride | None = None


def _vectorize(signals: dict[str, float]) -> np.ndarray:
    """Project a signal dict onto the fixed feature contract (missing -> 0.0)."""
    return np.array([float(signals.get(name, 0.0)) for name in SIGNAL_FEATURES], dtype=float)


class CounterpartyMatchingModel:
    """RandomForest match-probability model with a deterministic fallback."""

    model_id: str = MODEL_ID
    model_version: str = MODEL_VERSION
    feature_names: tuple[str, ...] = SIGNAL_FEATURES

    def __init__(
        self,
        classifier: RandomForestClassifier | None = None,
        *,
        card: ModelCard | None = None,
        overrides: OverrideRegistry | None = None,
    ) -> None:
        self._clf = classifier
        self.card = card
        self.overrides = overrides or OverrideRegistry()

    # -- governance -------------------------------------------------------------
    @property
    def is_fitted(self) -> bool:
        return self._clf is not None

    def set_override(self, key: str, override: HumanOverride) -> None:
        """Register a human decision that supersedes the model for input ``key``."""
        self.overrides.set(key, override)

    # -- inference --------------------------------------------------------------
    def predict(self, signals: dict[str, float], *, key: str | None = None) -> MatchPrediction:
        """Predict match probability for one signal vector.

        A registered human override for ``key`` wins outright (probability pinned to
        1.0/0.0, certainty 1.0) — the model is advisory (``data_engine.md`` §7.4).
        """
        if key is not None:
            override = self.overrides.get(key)
            if override is not None:
                return MatchPrediction(
                    probability=1.0 if override.decision else 0.0,
                    certainty=1.0,
                    method="human_override",
                    model_id=self.model_id,
                    model_version=self.model_version,
                    features=dict(signals),
                    override=override,
                )
        if self._clf is not None:
            return self._predict_forest(signals)
        return self._predict_heuristic(signals)

    def _predict_forest(self, signals: dict[str, float]) -> MatchPrediction:
        assert self._clf is not None
        row = _vectorize(signals).reshape(1, -1)
        proba = self._clf.predict_proba(row)[0]
        classes = list(self._clf.classes_)
        p_match = float(proba[classes.index(1)]) if 1 in classes else 0.0
        # Certainty = distance of the winning class prob from an even split, in [0, 1].
        certainty = float(2.0 * abs(p_match - 0.5))
        return MatchPrediction(
            probability=p_match,
            certainty=certainty,
            method="random_forest",
            model_id=self.model_id,
            model_version=self.model_version,
            features=dict(signals),
        )

    def _predict_heuristic(self, signals: dict[str, float]) -> MatchPrediction:
        num = 0.0
        den = 0.0
        for name, weight in _FALLBACK_WEIGHTS.items():
            value = float(signals.get(name, 0.0))
            # Negative evidence (contradicted hard id) pulls the score down; missing
            # (0.0) is simply weightless.
            num += weight * value
            den += weight * (1.0 if value != 0.0 else 0.0)
        probability = float(np.clip(num / den, 0.0, 1.0)) if den > 0.0 else 0.0
        certainty = min(_FALLBACK_CERTAINTY_CAP, float(2.0 * abs(probability - 0.5)))
        return MatchPrediction(
            probability=probability,
            certainty=certainty,
            method="heuristic_blend",
            model_id=self.model_id,
            model_version=self.model_version,
            features=dict(signals),
        )

    # -- training ---------------------------------------------------------------
    def fit(  # noqa: PLR0913
        self,
        signal_rows: Sequence[dict[str, float]],
        labels: Sequence[int],
        *,
        training_data_ref: str | None = None,
        n_estimators: int = 200,
        max_depth: int | None = None,
        random_state: int = 20260521,
        validation_metrics: dict[str, float] | None = None,
    ) -> CounterpartyMatchingModel:
        """Fit the RandomForest over the signal contract and stamp a model card."""
        try:
            from sklearn.ensemble import RandomForestClassifier  # noqa: PLC0415 - lazy heavy import
        except (ImportError, OSError) as exc:  # pragma: no cover - environment-dependent
            msg = "scikit-learn is required to train the counterparty matching model."
            raise ModelUnavailableError(msg) from exc
        x = (
            np.vstack([_vectorize(row) for row in signal_rows])
            if signal_rows
            else np.empty((0, len(SIGNAL_FEATURES)))
        )
        y = np.asarray(labels, dtype=int)
        clf = RandomForestClassifier(
            n_estimators=n_estimators,
            max_depth=max_depth,
            random_state=random_state,
            class_weight="balanced",
        )
        clf.fit(x, y)
        self._clf = clf
        self.card = ModelCard(
            model_id=self.model_id,
            model_version=self.model_version,
            feature_names=SIGNAL_FEATURES,
            output_name=OUTPUT_NAME,
            trained_at=ModelCard.now_iso(),
            training_rows=int(x.shape[0]),
            validation_metrics=dict(validation_metrics or {}),
            training_data_ref=training_data_ref,
            notes="RandomForest match-probability over multi-signal counterparty vector.",
        )
        return self

    # -- persistence ------------------------------------------------------------
    def save(self, path: Path | None = None) -> Path:
        if self._clf is None or self.card is None:
            msg = "Refusing to persist an unfitted counterparty matching model (no card)."
            raise ModelUnavailableError(msg)
        target = path or DEFAULT_ARTIFACT_DIR / f"{self.model_id}-{self.model_version}.joblib"
        return save_artifact({"classifier": self._clf, "card": self.card}, target)

    @classmethod
    def load(cls, path: Path | None = None) -> CounterpartyMatchingModel:
        target = path or DEFAULT_ARTIFACT_DIR / f"{MODEL_ID}-{MODEL_VERSION}.joblib"
        payload: dict[str, Any] = load_artifact(target)
        return cls(classifier=payload["classifier"], card=payload["card"])
