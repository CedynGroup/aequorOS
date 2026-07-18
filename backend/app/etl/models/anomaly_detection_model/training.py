"""Train + validate the anomaly detection model on Sample Bank Limited positions.

Per the build brief the IsolationForest is *trained and validated on* ``data/``. The
model trains on record fingerprints of the genuine loan tape
(``data/06_loans.csv``); validation measures detection recall against *injected*
structural corruptions (a balance blown up by orders of magnitude, a code field
replaced by a free-text blob, fields dropped) — the kind of extraction defect the
detector must surface. False-positive rate on the untouched tape is reported alongside.

Nothing runs at import; call :func:`train_and_validate` (or run the module).
"""

from __future__ import annotations

import csv
import random
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from app.domain.ingestion.contracts import RawRecord
from app.etl.deduplication.fingerprint_detector.fingerprint import (
    FINGERPRINT_FEATURES,
    fingerprint,
)
from app.etl.models.anomaly_detection_model.model import AnomalyDetectionModel

if TYPE_CHECKING:
    from collections.abc import Sequence

DEFAULT_LOANS_CSV = Path(__file__).resolve().parents[4].parent / "data" / "06_loans.csv"


@dataclass(frozen=True)
class AnomalyTrainingReport:
    model: AnomalyDetectionModel
    n_train: int
    injected_recall: float
    clean_false_positive_rate: float


def _load_positions(path: Path, *, limit: int | None) -> list[RawRecord]:
    records: list[RawRecord] = []
    with path.open(newline="") as fh:
        for i, row in enumerate(csv.DictReader(fh)):
            if limit is not None and i >= limit:
                break
            records.append(
                RawRecord(
                    entity_type="position",
                    source_locator=row.get("source_reference", f"row{i}"),
                    data=dict(row),
                )
            )
    return records


def _inject_corruption(record: RawRecord, rng: random.Random) -> RawRecord:
    data = dict(record.data)
    kind = rng.randint(0, 2)
    if kind == 0:  # balance blown up by orders of magnitude
        data["balance_ghs"] = str(rng.uniform(1e12, 1e14))
    elif kind == 1:  # code field replaced by a free-text blob
        data["product_code"] = "corrupted free text value " * rng.randint(3, 8)
        data["gl_code"] = "?????????????????"
    else:  # most optional fields dropped
        for key in ("interest_rate", "rate_type", "ifrs9_stage", "ecl_provision_ghs", "branch_id"):
            data.pop(key, None)
    return RawRecord(
        entity_type="position", source_locator=f"{record.source_locator}#corrupt", data=data
    )


def train_and_validate(
    csv_path: Path | None = None,
    *,
    seed: int = 20260521,
    train_limit: int = 2000,
    n_injected: int = 100,
    score_threshold: float = 0.75,
) -> AnomalyTrainingReport:
    """Fit the IsolationForest on real positions; validate on injected corruptions."""
    rng = random.Random(seed)
    path = csv_path or DEFAULT_LOANS_CSV
    clean = _load_positions(path, limit=train_limit)
    if len(clean) < 2:
        msg = f"Not enough position rows to train on at {path}."
        raise ValueError(msg)

    train_matrix = np.vstack([fingerprint(r) for r in clean])
    model = AnomalyDetectionModel().fit(
        train_matrix, feature_names=FINGERPRINT_FEATURES, training_data_ref=str(path)
    )

    injected = [_inject_corruption(rng.choice(clean), rng) for _ in range(n_injected)]
    injected_recall = _detection_rate(model, injected, score_threshold)
    clean_fp = _detection_rate(model, clean, score_threshold)
    return AnomalyTrainingReport(
        model=model,
        n_train=len(clean),
        injected_recall=injected_recall,
        clean_false_positive_rate=clean_fp,
    )


def _detection_rate(
    model: AnomalyDetectionModel, records: Sequence[RawRecord], threshold: float
) -> float:
    if not records:
        return 0.0
    matrix = np.vstack([fingerprint(r) for r in records])
    scores = model.score(matrix)
    flagged = sum(1 for s in scores if s.score >= threshold)
    return flagged / len(records)


if __name__ == "__main__":  # pragma: no cover - manual training entry point
    report = train_and_validate()
    print(
        f"trained on {report.n_train} positions; injected recall="
        f"{report.injected_recall:.3f}, clean FP rate={report.clean_false_positive_rate:.3f}"
    )
