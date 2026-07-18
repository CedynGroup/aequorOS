"""Train + validate the counterparty matching model on Sample Bank Limited data.

The build brief requires the model be *trained and validated on* ``data/``. Real
duplicate labels are not shipped with the dataset, so this module synthesises a
labelled pair set from the genuine counterparty roster (``data/05_counterparties.csv``):

  * **Positives** — controlled, realistic corruptions of a real name (case changes,
    legal-suffix swaps, token transposition, single-character typos, initialisation),
    paired with the original: the exact "same entity spelled differently" the matcher
    must catch.
  * **Negatives** — pairs of *distinct* real counterparties (optionally hard negatives
    that share a first token), so the forest learns the decision boundary rather than a
    trivial threshold.

Signals are computed with the production :func:`compute_signals`, so the model trains on
exactly the feature contract it serves. Validation is a held-out split reporting
precision / recall / ROC-AUC. Nothing here runs at import; call
:func:`train_and_validate` explicitly (or run the module).
"""

from __future__ import annotations

import csv
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app.domain.ingestion.contracts import RawRecord
from app.etl.deduplication.counterparty_matcher.signals import compute_signals
from app.etl.models.counterparty_matching_model.model import CounterpartyMatchingModel

if TYPE_CHECKING:
    from collections.abc import Sequence

# Default Sample Bank counterparty roster (untracked; see data/README.md).
DEFAULT_COUNTERPARTY_CSV = (
    Path(__file__).resolve().parents[4].parent / "data" / "05_counterparties.csv"
)

_LEGAL_SWAPS = {"ltd": "limited", "limited": "ltd", "co": "company", "plc": "plc"}


@dataclass(frozen=True)
class TrainingReport:
    model: CounterpartyMatchingModel
    n_train: int
    n_test: int
    metrics: dict[str, float]


def _load_counterparties(path: Path, *, limit: int | None) -> list[dict[str, str]]:
    with path.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    return rows[:limit] if limit else rows


def _as_record(cid: str, name: str, row: dict[str, str] | None = None) -> RawRecord:
    data: dict[str, str] = {"counterparty_id": cid, "counterparty_name": name}
    if row:
        for key in ("counterparty_type", "country"):
            if row.get(key):
                data[key] = row[key]
    return RawRecord(entity_type="counterparty", source_locator=f"train#{cid}", data=data)


def _corrupt(name: str, rng: random.Random) -> str:  # noqa: PLR0911
    """Produce a realistic same-entity variant of ``name``."""
    tokens = name.split()
    choice = rng.randint(0, 4)
    if choice == 0:  # legal-suffix swap or append
        low = [t.lower() for t in tokens]
        for i, t in enumerate(low):
            if t in _LEGAL_SWAPS:
                tokens[i] = _LEGAL_SWAPS[t].upper()
                return " ".join(tokens)
        return name.upper() + " LTD"
    if choice == 1 and len(tokens) > 1:  # token transposition
        i = rng.randrange(len(tokens) - 1)
        tokens[i], tokens[i + 1] = tokens[i + 1], tokens[i]
        return " ".join(tokens)
    if choice == 2:  # case fold
        return name.upper() if name != name.upper() else name.lower()
    if choice == 3 and len(name) > 4:  # single-character typo
        pos = rng.randrange(1, len(name) - 1)
        return name[:pos] + name[pos + 1] + name[pos] + name[pos + 2 :]
    # abbreviate an interior token to an initial
    if len(tokens) > 2:
        i = rng.randrange(1, len(tokens) - 1)
        tokens[i] = tokens[i][0] + "."
        return " ".join(tokens)
    return re.sub(r"\s+", "  ", name)  # whitespace noise


def build_labeled_pairs(
    rows: Sequence[dict[str, str]],
    *,
    seed: int = 20260521,
    max_positives: int = 400,
    negatives_per_positive: int = 1,
) -> tuple[list[dict[str, float]], list[int]]:
    """Assemble ``(signal_rows, labels)`` from the real roster."""
    rng = random.Random(seed)
    named = [r for r in rows if r.get("counterparty_name")]
    signals: list[dict[str, float]] = []
    labels: list[int] = []

    sample = named if len(named) <= max_positives else rng.sample(named, max_positives)
    for row in sample:
        cid, name = row["counterparty_id"], row["counterparty_name"]
        variant = _corrupt(name, rng)
        signals.append(
            compute_signals(_as_record(cid, name, row), _as_record(f"{cid}-v", variant, row))
        )
        labels.append(1)
        for _ in range(negatives_per_positive):
            other = rng.choice(named)
            if other["counterparty_id"] == cid:
                continue
            signals.append(
                compute_signals(
                    _as_record(cid, name, row),
                    _as_record(other["counterparty_id"], other["counterparty_name"], other),
                )
            )
            labels.append(0)
    return signals, labels


def train_and_validate(
    csv_path: Path | None = None,
    *,
    seed: int = 20260521,
    max_positives: int = 400,
    test_fraction: float = 0.25,
) -> TrainingReport:
    """Train on real counterparty data with a held-out validation split."""
    try:
        from sklearn.metrics import precision_score, recall_score, roc_auc_score  # noqa: PLC0415
        from sklearn.model_selection import train_test_split  # noqa: PLC0415
    except (ImportError, OSError) as exc:  # pragma: no cover - environment-dependent
        msg = "scikit-learn is required to train/validate the counterparty matching model."
        raise ImportError(msg) from exc

    path = csv_path or DEFAULT_COUNTERPARTY_CSV
    rows = _load_counterparties(path, limit=None)
    signals, labels = build_labeled_pairs(rows, seed=seed, max_positives=max_positives)

    split = train_test_split(
        signals, labels, test_size=test_fraction, random_state=seed, stratify=labels
    )
    x_train: list[dict[str, float]] = list(split[0])
    x_test: list[dict[str, float]] = list(split[1])
    y_train: list[int] = [int(v) for v in split[2]]
    y_test: list[int] = [int(v) for v in split[3]]
    model = CounterpartyMatchingModel().fit(x_train, y_train, training_data_ref=str(path))
    probs = [model.predict(row).probability for row in x_test]
    preds = [1 if p >= 0.5 else 0 for p in probs]
    metrics = {
        "precision": float(precision_score(y_test, preds)),
        "recall": float(recall_score(y_test, preds)),
        "roc_auc": float(roc_auc_score(y_test, probs)) if len(set(y_test)) > 1 else float("nan"),
    }
    if model.card is not None:
        model.card = _with_metrics(model.card, metrics)
    return TrainingReport(model=model, n_train=len(x_train), n_test=len(x_test), metrics=metrics)


def _with_metrics(card, metrics):  # noqa: ANN001, ANN202 - frozen-dataclass copy
    from dataclasses import replace  # noqa: PLC0415

    return replace(card, validation_metrics=dict(metrics))


if __name__ == "__main__":  # pragma: no cover - manual training entry point
    report = train_and_validate()
    print(f"trained on {report.n_train} pairs, validated on {report.n_test}: {report.metrics}")
