"""Contract tests for the IsolationForest fingerprint anomaly detector."""

from __future__ import annotations

import json

import numpy as np
import pytest

from app.domain.ingestion.contracts import RawRecord
from app.etl.contracts import Disposition, ETLOperationType
from app.etl.deduplication.fingerprint_detector import (
    FINGERPRINT_FEATURES,
    FingerprintAnomalyDetector,
    fingerprint,
)
from tests.etl._factories import position


@pytest.mark.parametrize(
    "amount",
    ["Infinity", "-Infinity", "+NaN", float("inf"), float("-inf"), float("nan"), "1.7e308"],
)
@pytest.mark.parametrize("record_count", [2, 12])
def test_nonfinite_inputs_produce_finite_fingerprints_and_confidence(
    amount: str | float, record_count: int
) -> None:
    """Fingerprint vectors and anomaly confidence are finite JSON persistence contracts."""
    records = [
        RawRecord(
            entity_type="reference",
            source_locator=f"capital_structure#{i}",
            source_table="capital_structure",
            dataset_kind="capital_structure",
            data={"capital_component": f"component_{i}", "amount_ghs": amount, "other": amount},
        )
        for i in range(record_count)
    ]
    for record in records:
        assert np.isfinite(fingerprint(record)).all()
    operations = FingerprintAnomalyDetector(score_threshold=0.0).score(records)
    assert len(operations) == record_count
    json.dumps([op.provenance.confidence for op in operations], allow_nan=False)
    for operation in operations:
        confidence = operation.provenance.confidence
        assert confidence is not None and 0.0 <= confidence <= 1.0
    assert all(record.data["amount_ghs"] is amount for record in records)


def _normal(i: int) -> object:
    return position(
        f"n{i}",
        source_reference=f"ARR/{i}",
        as_of_date="2026-04-30",
        balance_ghs=str(1000 + i),
        ifrs9_stage="1",
        product_code="LN.RET.PERS",
        currency="GHS",
    )


def _outlier() -> object:
    # Structurally unlike its peers: a giant balance and a garbage free-text blob where
    # codes belong, plus far more populated fields.
    return position(
        "OUT",
        source_reference="ARR/OUT",
        as_of_date="2026-04-30",
        balance_ghs="99999999999999",
        ifrs9_stage="XXXXXXXXXXXXXXXXXXXX",
        product_code="???!!!###",
        currency="GHS",
        junk_a="lorem ipsum dolor sit amet consectetur",
        junk_b="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        junk_c="1234567890123456789012345",
    )


def test_fingerprint_matches_feature_contract_width() -> None:
    vec = fingerprint(_normal(1))  # type: ignore[arg-type]
    assert vec.shape == (len(FINGERPRINT_FEATURES),)


def test_outlier_is_flagged_never_modified() -> None:
    records = [_normal(i) for i in range(12)] + [_outlier()]
    ops = FingerprintAnomalyDetector().score(records)  # type: ignore[arg-type]

    assert ops, "expected at least the structural outlier to be flagged"
    flagged_ids = {op.record_id for op in ops}
    assert "OUT" in flagged_ids
    for op in ops:
        # Flag-not-modify invariant: value untouched, disposition FLAGGED.
        assert op.disposition is Disposition.FLAGGED
        assert op.after is None
        assert op.provenance.operation_type is ETLOperationType.ANOMALY_FLAG
        assert op.provenance.confidence is not None
        assert op.reason  # a human-readable justification is always present
        assert op.lineage_input_ids  # lineage retained for reversibility


def test_empty_input_produces_no_operations() -> None:
    assert FingerprintAnomalyDetector().score([]) == []


def test_uniform_batch_flags_nothing() -> None:
    records = [_normal(i) for i in range(10)]
    ops = FingerprintAnomalyDetector().score(records)  # type: ignore[arg-type]
    assert ops == []
