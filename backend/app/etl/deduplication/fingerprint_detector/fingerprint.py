"""Record-fingerprint feature extraction for anomaly detection.

An IsolationForest needs a fixed-width numeric vector per record. A "fingerprint" here
captures the *shape* of a record independent of any single field's semantics: numeric
magnitudes (log-scaled), structural counts (populated fields, name/text lengths), and
stable hash-buckets of categorical combinations. The goal is to surface records that
look structurally unlike their peers (an all-caps free-text blob where a code belongs, a
balance three orders of magnitude off, a row missing every optional field) — candidates
for a human flag, never a silent rewrite.
"""

from __future__ import annotations

import hashlib
import math
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from app.domain.ingestion.contracts import RawRecord

# The fixed feature contract emitted per record, in order.
FINGERPRINT_FEATURES: tuple[str, ...] = (
    "n_fields",
    "n_populated",
    "populated_ratio",
    "n_numeric_fields",
    "log_max_numeric",
    "log_mean_numeric",
    "numeric_span",
    "total_text_len",
    "max_token_len",
    "digit_ratio",
    "alpha_ratio",
    "categorical_bucket",
)

_HASH_BUCKETS = 32


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        text = str(value).replace(",", "").strip()
        if text == "" or text.lower() in {"nan", "none", "null", "n/a"}:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def _categorical_bucket(record: RawRecord) -> float:
    """Stable hash bucket of the record's non-numeric field *names* present.

    Records with the same structural shape (same set of populated categorical columns)
    fall in the same bucket, so a row missing a normally-present code lands elsewhere.
    """
    present = sorted(
        k
        for k, v in record.data.items()
        if v is not None and _to_float(v) is None and str(v).strip()
    )
    digest = hashlib.sha256("|".join(present).encode()).hexdigest()
    return float(int(digest[:8], 16) % _HASH_BUCKETS)


def fingerprint(record: RawRecord) -> np.ndarray:
    """Project one record onto :data:`FINGERPRINT_FEATURES`."""
    data = record.data
    n_fields = len(data)
    populated_values = [v for v in data.values() if v is not None and str(v).strip()]
    n_populated = len(populated_values)

    numerics = [f for v in data.values() if (f := _to_float(v)) is not None]
    n_numeric = len(numerics)
    abs_numerics = [abs(x) for x in numerics]
    log_max = math.log10(max(abs_numerics) + 1.0) if abs_numerics else 0.0
    log_mean = math.log10(sum(abs_numerics) / len(abs_numerics) + 1.0) if abs_numerics else 0.0
    numeric_span = (log_max - (math.log10(min(abs_numerics) + 1.0))) if abs_numerics else 0.0

    text = " ".join(str(v) for v in populated_values)
    total_text_len = float(len(text))
    tokens = text.split()
    max_token_len = float(max((len(t) for t in tokens), default=0))
    n_chars = max(1, len(text))
    digit_ratio = sum(c.isdigit() for c in text) / n_chars
    alpha_ratio = sum(c.isalpha() for c in text) / n_chars

    return np.array(
        [
            float(n_fields),
            float(n_populated),
            float(n_populated) / float(n_fields) if n_fields else 0.0,
            float(n_numeric),
            float(log_max),
            float(log_mean),
            float(numeric_span),
            total_text_len,
            max_token_len,
            float(digit_ratio),
            float(alpha_ratio),
            _categorical_bucket(record),
        ],
        dtype=float,
    )


def fingerprint_matrix(records: list[RawRecord]) -> np.ndarray:
    """Stack per-record fingerprints into an ``(n_records, n_features)`` matrix."""
    if not records:
        return np.empty((0, len(FINGERPRINT_FEATURES)), dtype=float)
    return np.vstack([fingerprint(r) for r in records])
