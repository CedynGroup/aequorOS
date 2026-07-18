"""IsolationForest fingerprint anomaly detector (flags, never modifies).

Scores every record's fingerprint through the MRM-governed
:class:`AnomalyDetectionModel` and emits a ``Disposition.FLAGGED`` :class:`ETLOperation`
for each record the model calls anomalous. Per ``data_engine.md`` §12.5 the detector
**never** rewrites a value: every emitted op has ``after=None`` and a human-readable
``reason``. Flags land on a synthetic ``__record_fingerprint__`` field so they are
attributable to the whole record without colliding with — or mutating — any real
(possibly regulatory-critical) column.

If the model has no fitted IsolationForest it is fit on the batch's own fingerprints
(or falls back to the robust MAD baseline) so the detector always produces a defensible,
reproducible score rather than silently passing everything.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.etl.contracts import (
    AnomalyDetector,
    Disposition,
    ETLOperation,
    ETLOperationType,
    ETLProvenance,
)
from app.etl.deduplication._fields import record_id
from app.etl.deduplication.fingerprint_detector.fingerprint import (
    FINGERPRINT_FEATURES,
    fingerprint_matrix,
)
from app.etl.models._mrm import ModelUnavailableError
from app.etl.models.anomaly_detection_model import AnomalyDetectionModel

if TYPE_CHECKING:
    from app.domain.ingestion.contracts import RawRecord

_ANOMALY_FIELD = "__record_fingerprint__"
_OPERATION_REF = "fingerprint_detector/v1"
_MIN_FIT_ROWS = 8  # below this a per-batch IsolationForest is unreliable; use MAD baseline


class FingerprintAnomalyDetector(AnomalyDetector):
    """Flags structurally anomalous records via record-fingerprint scoring."""

    def __init__(
        self,
        model: AnomalyDetectionModel | None = None,
        *,
        score_threshold: float = 0.75,
    ) -> None:
        self.model = model or AnomalyDetectionModel()
        self.score_threshold = score_threshold

    def score(self, records: list[RawRecord]) -> list[ETLOperation]:
        if not records:
            return []
        matrix = fingerprint_matrix(records)
        model = self._ensure_fitted(matrix)
        keys = [record_id(r) for r in records]
        assessments = model.score(matrix, keys=keys)

        operations: list[ETLOperation] = []
        for record, assessment in zip(records, assessments, strict=True):
            # Flag on a calibrated score cutoff rather than the forest's auto-contamination
            # boundary: on a bank ETL the boundary always marks a tail (false-positive human
            # reviews), whereas the threshold trips only for genuinely extreme fingerprints.
            if assessment.score < self.score_threshold:
                continue
            operations.append(
                ETLOperation(
                    record_id=record_id(record),
                    field_name=_ANOMALY_FIELD,
                    disposition=Disposition.FLAGGED,
                    before=None,
                    after=None,  # FLAGGED must never modify the record
                    provenance=ETLProvenance(
                        operation_type=ETLOperationType.ANOMALY_FLAG,
                        operation_ref=_OPERATION_REF,
                        model_id=assessment.model_id,
                        model_version=assessment.model_version,
                        confidence=assessment.certainty,
                    ),
                    lineage_input_ids=(record.source_locator,),
                    reason=(
                        f"Record fingerprint anomaly (score={assessment.score:.3f}, "
                        f"method={assessment.method}); review before canonicalisation."
                    ),
                )
            )
        return operations

    def _ensure_fitted(self, matrix) -> AnomalyDetectionModel:  # noqa: ANN001 - np.ndarray
        if self.model.is_fitted:
            return self.model
        # No pre-trained forest supplied: fit on the batch itself when there is enough
        # signal, otherwise pin the deterministic MAD baseline. Either way, reproducible.
        if matrix.shape[0] >= _MIN_FIT_ROWS:
            try:
                return self.model.fit(matrix, feature_names=FINGERPRINT_FEATURES)
            except ModelUnavailableError:
                pass
        return self.model.fit_fallback(matrix, feature_names=FINGERPRINT_FEATURES)
