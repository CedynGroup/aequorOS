"""Shared model-risk-management (MRM) scaffolding for ML-ETL models.

Both ML-ETL models (counterparty matching, anomaly detection) are governed by
``data_engine.md`` §12.5 / §7.4: every model is *versioned*, carries an explicit
*input/output contract*, produces a *confidence*, is *human-overridable*, and
persists a reproducible *artifact* with a model card. This module centralises the
pieces common to both so neither model re-implements governance plumbing.

Nothing here imports a heavy driver at module load: :mod:`joblib` (pulled in with
scikit-learn) is imported lazily inside :func:`save_artifact` / :func:`load_artifact`
so importing a model class never pays for the serialization stack, and a broken
install degrades with a classified error instead of an import-time crash.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping


class ModelUnavailableError(RuntimeError):
    """A model artifact could not be (de)serialised because a driver is missing.

    Raised in place of a raw ``ImportError``/``OSError`` so callers can classify a
    governance failure (no persisted model) distinctly from a modelling failure.
    """


@dataclass(frozen=True)
class ModelCard:
    """The MRM record travelling with every fitted artifact (``data_engine.md`` §12.5).

    An artifact without a card is not loadable as a governed model: the card pins the
    ``model_id``/``model_version``, the ordered feature contract the model was trained
    against, the training provenance, and the held-out validation metrics that were
    reviewed before the version was blessed.
    """

    model_id: str
    model_version: str
    feature_names: tuple[str, ...]
    output_name: str
    trained_at: str
    training_rows: int
    validation_metrics: dict[str, float] = field(default_factory=dict)
    training_data_ref: str | None = None
    notes: str | None = None

    @staticmethod
    def now_iso() -> str:
        return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class HumanOverride:
    """A human decision that supersedes a model output (``data_engine.md`` §7.4).

    Model outputs are advisory: a reviewer may pin the decision for a specific input
    key (a pair id, a record id). Overrides are auditable — they carry the reviewer
    and reason — and are applied *on top of* the model, never by mutating it.
    """

    decided_by: str
    decision: bool
    reason: str
    decided_at: str = field(default_factory=ModelCard.now_iso)


class OverrideRegistry:
    """In-memory registry of human overrides keyed by an opaque input key.

    Kept deliberately simple and storage-agnostic: the ETL pipeline owns durable
    persistence (``storage.md`` §9). This is the seam a review UI writes through so a
    matcher/detector consults human decisions before emitting an auto-confirmed op.
    """

    def __init__(self, overrides: Mapping[str, HumanOverride] | None = None) -> None:
        self._overrides: dict[str, HumanOverride] = dict(overrides or {})

    def set(self, key: str, override: HumanOverride) -> None:
        self._overrides[key] = override

    def get(self, key: str) -> HumanOverride | None:
        return self._overrides.get(key)

    def __contains__(self, key: str) -> bool:
        return key in self._overrides

    def __len__(self) -> int:
        return len(self._overrides)


# Default location for persisted ML-ETL artifacts. Chosen under the repo's untracked
# ``artifacts`` tree (never committed): a persisted RandomForest/IsolationForest is a
# governed deliverable, not source. Callers pass an explicit path; this is only the
# default when they do not.
DEFAULT_ARTIFACT_DIR = Path(__file__).resolve().parents[3] / "artifacts" / "etl_models"


def save_artifact(payload: Any, path: Path) -> Path:
    """Persist a fitted model + its :class:`ModelCard` to ``path`` (joblib)."""
    try:
        import joblib  # noqa: PLC0415 - lazy: serialization stack only when persisting
    except (ImportError, OSError) as exc:  # pragma: no cover - environment-dependent
        msg = "joblib is required to persist ML-ETL model artifacts."
        raise ModelUnavailableError(msg) from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(payload, path)
    return path


def load_artifact(path: Path) -> Any:
    """Load a payload previously written by :func:`save_artifact`."""
    try:
        import joblib  # noqa: PLC0415 - lazy: serialization stack only when loading
    except (ImportError, OSError) as exc:  # pragma: no cover - environment-dependent
        msg = "joblib is required to load ML-ETL model artifacts."
        raise ModelUnavailableError(msg) from exc
    if not path.exists():
        msg = f"No ML-ETL model artifact at {path}."
        raise ModelUnavailableError(msg)
    return joblib.load(path)
