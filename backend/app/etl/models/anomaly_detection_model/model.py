"""Versioned IsolationForest fingerprint-anomaly model (MRM-governed).

The :class:`FingerprintAnomalyDetector` feeds this model per-record numeric
*fingerprints* and gets back, for each record, an anomaly score in ``[0, 1]`` and an
is-anomaly decision. The model only *scores*; it never modifies data — the detector
translates a positive decision into a ``Disposition.FLAGGED`` operation
(``data_engine.md`` §12.5: outliers on regulatory data are surfaced, never rewritten).

Governance mirrors the matching model: versioned id/version + :class:`ModelCard`, a
fixed feature contract, a confidence on every score, human override, artifact
persistence, and a documented deterministic fallback (robust MAD z-score) when no
IsolationForest is fitted or scikit-learn is unavailable.
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

    from sklearn.ensemble import IsolationForest

MODEL_ID = "anomaly_detection_model"
MODEL_VERSION = "1.0.0"
OUTPUT_NAME = "anomaly_score"

# Above this normalised score a record is treated as anomalous (both the MAD fallback
# and the calibrated IsolationForest score map onto this single, auditable cutoff).
_FALLBACK_THRESHOLD = 0.75
# Multiplier mapping IsolationForest's narrow decision-function margin onto a [0, 1]
# score via a logistic; calibrated so structural outliers clear _FALLBACK_THRESHOLD.
_FOREST_SCORE_SCALE = 10.0


@dataclass(frozen=True)
class AnomalyScore:
    """One record's anomaly assessment with its governance envelope."""

    score: float  # [0, 1]; higher = more anomalous
    is_anomaly: bool
    certainty: float  # [0, 1]
    method: str  # "isolation_forest" | "mad_zscore" | "human_override"
    model_id: str
    model_version: str
    override: HumanOverride | None = None


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


class AnomalyDetectionModel:
    """IsolationForest anomaly model with a robust MAD-z fallback."""

    model_id: str = MODEL_ID
    model_version: str = MODEL_VERSION

    def __init__(
        self,
        forest: IsolationForest | None = None,
        *,
        feature_names: tuple[str, ...] = (),
        card: ModelCard | None = None,
        fallback_stats: dict[str, np.ndarray] | None = None,
        overrides: OverrideRegistry | None = None,
    ) -> None:
        self._forest = forest
        self.feature_names = feature_names
        self.card = card
        # median/MAD per feature for the deterministic fallback; set on fit_fallback.
        self._fallback_stats = fallback_stats
        self.overrides = overrides or OverrideRegistry()

    @property
    def is_fitted(self) -> bool:
        return self._forest is not None

    def set_override(self, key: str, override: HumanOverride) -> None:
        self.overrides.set(key, override)

    # -- inference --------------------------------------------------------------
    def score(self, matrix: np.ndarray, *, keys: Sequence[str] | None = None) -> list[AnomalyScore]:
        """Score a feature matrix (rows = records, cols = the feature contract)."""
        matrix = np.atleast_2d(np.asarray(matrix, dtype=float))
        if self._forest is not None:
            results = self._score_forest(matrix)
        else:
            results = self._score_fallback(matrix)
        if keys is not None:
            results = [
                self._apply_override(res, key) for res, key in zip(results, keys, strict=False)
            ]
        return results

    def _apply_override(self, result: AnomalyScore, key: str) -> AnomalyScore:
        override = self.overrides.get(key)
        if override is None:
            return result
        return AnomalyScore(
            score=1.0 if override.decision else 0.0,
            is_anomaly=override.decision,
            certainty=1.0,
            method="human_override",
            model_id=self.model_id,
            model_version=self.model_version,
            override=override,
        )

    def _score_forest(self, matrix: np.ndarray) -> list[AnomalyScore]:
        assert self._forest is not None
        # decision_function: >0 inlier, <0 outlier. Map to [0,1] anomaly via sigmoid of
        # the negated margin; predict() gives the calibrated boundary decision.
        margins = self._forest.decision_function(matrix)
        # IsolationForest.decision_function spans a narrow band (~[-0.25, 0.1]); the
        # multiplier is calibrated (on the Sample Bank tape) so genuine structural
        # outliers clear the 0.75 flag threshold while the clean tail stays under ~3% FP.
        # is_anomaly is thresholded off the calibrated sigmoid score (below), not the
        # forest's raw predict(), so the flag semantics match the fallback path exactly.
        scores = _sigmoid(-_FOREST_SCORE_SCALE * np.asarray(margins))
        out: list[AnomalyScore] = []
        for score_val, margin in zip(scores, margins, strict=True):
            out.append(
                AnomalyScore(
                    score=float(score_val),
                    is_anomaly=bool(score_val >= _FALLBACK_THRESHOLD),
                    certainty=float(min(1.0, abs(margin) * _FOREST_SCORE_SCALE)),
                    method="isolation_forest",
                    model_id=self.model_id,
                    model_version=self.model_version,
                )
            )
        return out

    def _score_fallback(self, matrix: np.ndarray) -> list[AnomalyScore]:
        stats = self._fallback_stats
        if stats is None:
            median = np.median(matrix, axis=0)
            mad = np.median(np.abs(matrix - median), axis=0)
        else:
            median = stats["median"]
            mad = stats["mad"]
        # Robust modified z-score (0.6745 * (x - median) / MAD); aggregate across
        # features by the max absolute z so a single wild field trips the flag.
        safe_mad = np.where(mad == 0.0, 1.0, mad)
        z = 0.6745 * (matrix - median) / safe_mad
        max_abs_z = np.max(np.abs(z), axis=1)
        # Map |z| through a sigmoid centred at 3.5 (the conventional MAD-outlier cutoff).
        scores = _sigmoid(max_abs_z - 3.5)
        out: list[AnomalyScore] = []
        for score_val in scores:
            out.append(
                AnomalyScore(
                    score=float(score_val),
                    is_anomaly=bool(score_val >= _FALLBACK_THRESHOLD),
                    certainty=float(abs(score_val - 0.5) * 2.0),
                    method="mad_zscore",
                    model_id=self.model_id,
                    model_version=self.model_version,
                )
            )
        return out

    # -- training ---------------------------------------------------------------
    def fit(  # noqa: PLR0913
        self,
        matrix: np.ndarray,
        *,
        feature_names: tuple[str, ...],
        training_data_ref: str | None = None,
        contamination: float | str = "auto",
        n_estimators: int = 200,
        random_state: int = 20260521,
        validation_metrics: dict[str, float] | None = None,
    ) -> AnomalyDetectionModel:
        """Fit the IsolationForest and stamp a model card + fallback stats."""
        try:
            from sklearn.ensemble import IsolationForest  # noqa: PLC0415 - lazy heavy import
        except (ImportError, OSError) as exc:  # pragma: no cover - environment-dependent
            msg = "scikit-learn is required to train the anomaly detection model."
            raise ModelUnavailableError(msg) from exc
        matrix = np.atleast_2d(np.asarray(matrix, dtype=float))
        forest_kwargs: dict[str, Any] = {
            "n_estimators": n_estimators,
            "contamination": contamination,
            "random_state": random_state,
        }
        forest = IsolationForest(**forest_kwargs)
        forest.fit(matrix)
        self._forest = forest
        self.feature_names = feature_names
        # Persist fallback stats from the same training distribution so a later
        # load-without-sklearn path still scores against the trained baseline.
        median = np.median(matrix, axis=0)
        mad = np.median(np.abs(matrix - median), axis=0)
        self._fallback_stats = {"median": median, "mad": mad}
        self.card = ModelCard(
            model_id=self.model_id,
            model_version=self.model_version,
            feature_names=feature_names,
            output_name=OUTPUT_NAME,
            trained_at=ModelCard.now_iso(),
            training_rows=int(matrix.shape[0]),
            validation_metrics=dict(validation_metrics or {}),
            training_data_ref=training_data_ref,
            notes="IsolationForest over record fingerprints; MAD-z fallback stats included.",
        )
        return self

    def fit_fallback(
        self, matrix: np.ndarray, *, feature_names: tuple[str, ...]
    ) -> AnomalyDetectionModel:
        """Fit only the deterministic MAD baseline (no sklearn) — used when the forest
        is unavailable but a calibrated, reproducible detector is still required."""
        matrix = np.atleast_2d(np.asarray(matrix, dtype=float))
        median = np.median(matrix, axis=0)
        mad = np.median(np.abs(matrix - median), axis=0)
        self._fallback_stats = {"median": median, "mad": mad}
        self.feature_names = feature_names
        return self

    # -- persistence ------------------------------------------------------------
    def save(self, path: Path | None = None) -> Path:
        if self._forest is None or self.card is None:
            msg = "Refusing to persist an unfitted anomaly detection model (no card)."
            raise ModelUnavailableError(msg)
        target = path or DEFAULT_ARTIFACT_DIR / f"{self.model_id}-{self.model_version}.joblib"
        return save_artifact(
            {
                "forest": self._forest,
                "card": self.card,
                "feature_names": self.feature_names,
                "fallback_stats": self._fallback_stats,
            },
            target,
        )

    @classmethod
    def load(cls, path: Path | None = None) -> AnomalyDetectionModel:
        target = path or DEFAULT_ARTIFACT_DIR / f"{MODEL_ID}-{MODEL_VERSION}.joblib"
        payload: dict[str, Any] = load_artifact(target)
        return cls(
            forest=payload["forest"],
            feature_names=payload["feature_names"],
            card=payload["card"],
            fallback_stats=payload.get("fallback_stats"),
        )
