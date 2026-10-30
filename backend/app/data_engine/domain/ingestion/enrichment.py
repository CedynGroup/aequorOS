"""Enrichment overlays applied to canonical snapshots, in a fixed order.

Order matters (spec §7.2): currency translation first so later overlays work
in the reporting currency; manual overrides always apply last so an operator
can override anything the pipeline produced. Every enriched value carries a
provenance record naming its source — policy, model, or human — because an
enriched figure without provenance is indistinguishable from raw data in an
audit.

This module is pure: it computes enriched values and provenance from explicit
inputs. Persistence (supersession, lineage nodes) stays in the orchestration
service.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

ProvenanceSource = Literal["POLICY", "ML_MODEL", "MANUAL_OVERRIDE", "REGULATOR_MANDATED"]


@dataclass(frozen=True)
class Provenance:
    """The audit trail entry for one enriched field value."""

    value: Any
    source: ProvenanceSource
    as_of: datetime
    model_id: str | None = None
    confidence: float | None = None
    original_value: Any = None
    override: dict[str, Any] | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "value": _jsonable(self.value),
            "source": self.source,
            "as_of": self.as_of.isoformat(),
            "model_id": self.model_id,
            "confidence": self.confidence,
            "original_value": _jsonable(self.original_value),
            "override": self.override,
        }


@dataclass
class EnrichmentResult:
    """Field updates plus their provenance, ready for the orchestrator to persist."""

    field_updates: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Provenance] = field(default_factory=dict)

    def set(self, field_name: str, value: Any, provenance: Provenance) -> None:
        self.field_updates[field_name] = value
        self.provenance[field_name] = provenance

    def provenance_json(self) -> dict[str, Any]:
        return {name: record.as_json() for name, record in self.provenance.items()}


def translate_currency(
    *,
    balance: Decimal,
    currency: str,
    reporting_currency: str,
    fx_rates: dict[str, Decimal],
    now: datetime,
) -> EnrichmentResult:
    """Express a position's balance in the reporting currency.

    ``fx_rates`` maps currency code -> units of reporting currency per unit,
    sourced from configured market data or manual entry; the rate used is
    recorded in the provenance. A missing rate is a hard error — silently
    keeping the original currency is exactly the quiet tolerance the design
    forbids.
    """
    result = EnrichmentResult()
    if currency == reporting_currency:
        return result
    rate = fx_rates.get(currency)
    if rate is None:
        msg = f"No FX rate for {currency}->{reporting_currency}; cannot translate."
        raise LookupError(msg)
    result.set(
        "reporting_currency_balance",
        balance * rate,
        Provenance(
            value=balance * rate,
            source="POLICY",
            as_of=now,
            model_id=f"fx:{currency}{reporting_currency}={rate}",
            original_value=balance,
        ),
    )
    return result


def assign_behavioral_maturity(
    *,
    position_type: str,
    policy_months: dict[str, int],
    now: datetime,
) -> EnrichmentResult:
    """Assign a policy-based behavioral maturity to non-maturity positions.

    ML-model-backed assignment arrives in Phase 4; the contract stays the
    same — only ``source``/``model_id``/``confidence`` change — so consumers
    never care which path produced the number.
    """
    result = EnrichmentResult()
    months = policy_months.get(position_type)
    if months is not None:
        result.set(
            "behavioral_maturity_months",
            months,
            Provenance(value=months, source="POLICY", as_of=now),
        )
    return result


def apply_manual_override(  # noqa: PLR0913 - who/when/why are each mandatory for audit
    *,
    field_name: str,
    value: Any,
    original_value: Any,
    user_id: str,
    reason: str,
    now: datetime,
) -> EnrichmentResult:
    """Overlay a human decision on an enriched or raw field. Applies last."""
    result = EnrichmentResult()
    result.set(
        field_name,
        value,
        Provenance(
            value=value,
            source="MANUAL_OVERRIDE",
            as_of=now,
            original_value=original_value,
            override={"user_id": user_id, "reason": reason, "timestamp": now.isoformat()},
        ),
    )
    return result


def merge_ordered(*results: EnrichmentResult) -> EnrichmentResult:
    """Combine step results; later steps win, mirroring the §7.2 order."""
    merged = EnrichmentResult()
    for result in results:
        for field_name, value in result.field_updates.items():
            merged.set(field_name, value, result.provenance[field_name])
    return merged


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    return value
