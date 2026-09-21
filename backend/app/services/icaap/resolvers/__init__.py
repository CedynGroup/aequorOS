"""Block resolvers: how a figure gets from a sealed engine result into ICAAP prose.

A resolver does two things. ``probe`` answers cheaply "what would bind if this
were refreshed now, and have its inputs been withdrawn?", which is what makes
staleness a read-time computation rather than stored state. ``resolve`` produces
the binding: the pinned source, the payload the card and the exports render, and
the named facts a sentence may quote.

Every resolver reads SEALED state for the cycle's exact as-of date — a succeeded
``RegulatoryRun``, an attested sign-off, an approved plan, a quarterly snapshot,
a register digest. None of them reads ``live_metrics`` or the live fact plane:
those are a monitoring surface the worker rewrites continuously, and a figure in
a filed report has to be the one that was computed, reviewed and approved.
``tests/architecture/test_icaap_boundaries.py`` enforces that by import scan.

Values leave here as STRINGS or ``None``. ``None`` renders "Not available"
everywhere — never 0, which would read as a measured zero.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.domain.icaap.blocks import BLOCK_CATALOGUE, BlockSpec, SourceProbe
from app.models import BankReportingPeriod, RegulatoryRun
from app.models.icaap import IcaapCycle, IcaapDataBlock
from app.services.attestation.digests import digest_of

PAYLOAD_SCHEMA = "icaap-block-payload-v1"


@dataclass(frozen=True)
class ResolveContext:
    db: Session
    access: IcaapAccess
    cycle: IcaapCycle
    block: IcaapDataBlock
    #: The reporting period whose end IS the cycle's as-of date, or None.
    period: BankReportingPeriod | None
    currency: str

    @property
    def spec(self) -> BlockSpec:
        return BLOCK_CATALOGUE[self.block.block_type]


@dataclass(frozen=True)
class Resolution:
    source_kind: str
    source_ref: dict[str, Any]
    source_key: str
    source_as_of: date | None
    payload: dict[str, Any]
    facts: dict[str, dict[str, Any]]
    source_run_ids: tuple[str, ...] = ()


class Unavailable(Exception):  # noqa: N818 - a state the card reports, not a fault
    """No sealed source exists yet. The card says so in plain language."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class BlockResolver(Protocol):
    block_type: str
    version: str

    def probe(self, rc: ResolveContext) -> SourceProbe: ...

    def resolve(self, rc: ResolveContext) -> Resolution: ...


# --- building payloads and facts ------------------------------------------


def no_period(rc: ResolveContext) -> Unavailable:
    return Unavailable(
        f"No computed position as at {rc.cycle.as_of_date.isoformat()}. Ingest the "
        "book as of that date through the Data Engine, then run the engine."
    )


def _string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def fact(spec: BlockSpec, key: str, value: Any, *, currency: str | None = None) -> dict[str, Any]:
    """One named figure, in the shape the wire and the renderers expect."""
    declared = next((entry for entry in spec.facts if entry.key == key), None)
    label = declared.label if declared is not None else key.replace("_", " ").capitalize()
    kind = declared.kind if declared is not None else "text"
    unit = "%" if kind == "ratio_pct" else None
    return {
        "label": label,
        "kind": kind,
        "value": _string(value),
        "unit": unit,
        "currency": currency if kind == "amount" else None,
    }


def dynamic_fact(  # noqa: PLR0913 - a dynamic fact is its five named parts
    key: str, label: str, kind: str, value: Any, currency: str | None
) -> dict[str, Any]:
    """A fact a manual table declares for itself."""
    return {
        "label": label,
        "kind": kind,
        "value": _string(value),
        "unit": "%" if kind == "ratio_pct" else None,
        "currency": currency if kind == "amount" else None,
        "dynamic": True,
    }


@dataclass
class TableBuilder:
    key: str
    title: str
    columns: list[dict[str, str]] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)

    def column(self, key: str, label: str, kind: str) -> TableBuilder:
        self.columns.append({"key": key, "label": label, "kind": kind})
        return self

    def row(self, cells: Mapping[str, Any], *, emphasis: str | None = None) -> TableBuilder:
        self.rows.append(
            {"cells": {key: _string(value) for key, value in cells.items()}, "emphasis": emphasis}
        )
        return self

    def build(self) -> dict[str, Any]:
        return {"key": self.key, "title": self.title, "columns": self.columns, "rows": self.rows}


def payload(  # noqa: PLR0913 - a payload is its six named parts
    *,
    title: str,
    as_of: date | None,
    source_label: str,
    currency: str,
    tables: list[dict[str, Any]] | None = None,
    notes: list[str] | None = None,
    raw: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema": PAYLOAD_SCHEMA,
        "title": title,
        "as_of": None if as_of is None else as_of.isoformat(),
        "source_label": source_label,
        "unit": {"currency": currency, "scale": 1},
        "tables": tables or [],
        "notes": notes or [],
        "raw": raw or {},
    }


def payload_digest(payload_body: Mapping[str, Any], facts: Mapping[str, Any]) -> str:
    return digest_of({"payload": payload_body, "facts": facts})


def run_withdrawn(db: Session, run: RegulatoryRun) -> bool:
    """Have the canonical rows behind this sealed run been withdrawn?"""
    from app.domain.authority.evidence import EvidenceStatus  # noqa: PLC0415
    from app.services import withdrawal_impact  # noqa: PLC0415

    return withdrawal_impact.run_evidence(db, run).status is EvidenceStatus.INPUTS_WITHDRAWN


def registry() -> dict[str, BlockResolver]:
    """The resolver for each P1 block type."""
    from app.services.icaap.resolvers import (  # noqa: PLC0415 - avoid an import cycle
        capital,
        credit,
        fx,
        irrbb,
        irrbb_sf,
        liquidity,
        manual,
        plans,
        profile,
        registers,
        sovereign,
        stress,
    )

    resolvers: tuple[BlockResolver, ...] = (
        capital.CapitalPositionResolver(),
        capital.Pillar1RwaResolver(),
        stress.AppendixIIResolver(),
        stress.StressNarrativesResolver(),
        stress.ReverseStressResolver(),
        plans.CapitalPlanResolver(),
        plans.ManagementActionsResolver(),
        liquidity.IlaapResolver(),
        credit.ConcentrationResolver(),
        irrbb.IrrbbResolver(),
        profile.InstitutionProfileResolver(),
        manual.FinancialsResolver(),
        manual.ManualTableResolver(),
        # P2 registers and the two run-bound blocks the Pillar 2 methods need.
        registers.RiskRegisterResolver(),
        registers.RiskAppetiteResolver(),
        registers.Pillar2SummaryResolver(),
        registers.Table5Pillar2Resolver(),
        registers.CapitalReconciliationResolver(),
        registers.CapitalAllocationResolver(),
        registers.CapitalTriggersResolver(),
        registers.AuditReviewResolver(),
        registers.ChallengeLogResolver(),
        registers.SupervisoryAddonsResolver(),
        fx.FxPositionResolver(),
        sovereign.SovereignExposuresResolver(),
        # P5: the standardised framework, a sibling of the legacy IRRBB block
        # rather than a replacement for it — the two never share a source.
        irrbb_sf.IrrbbSfResolver(),
    )
    return {resolver.block_type: resolver for resolver in resolvers}


__all__ = [
    "PAYLOAD_SCHEMA",
    "BlockResolver",
    "ResolveContext",
    "Resolution",
    "TableBuilder",
    "Unavailable",
    "dynamic_fact",
    "fact",
    "no_period",
    "payload",
    "payload_digest",
    "registry",
    "run_withdrawn",
]
