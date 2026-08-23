"""Standing of a sealed run's evidence after a governed withdrawal (D-12).

The gap this closes
-------------------
``app/services/canonical_withdrawal.py`` retires a source system's book for a
business date under maker-checker. It is the remedy the platform's own overlap
detector instructs an operator to perform, and its refusal message states the
stake in words: a withdrawal *"removes data from every filed number derived for
this date."* Nothing guarded that statement.

A :class:`~app.models.regulatory_run.RegulatoryRun` sealed BEFORE the withdrawal
keeps its immutable ``inputs`` snapshot and its ``input_hash``. The next
derivation excludes the withdrawn rows and produces a different hash. The sealed
run is neither wrong nor deletable — it is a faithful record of what the book
said when it was computed — but until this module it carried nothing to
distinguish it from a run whose evidence still stands, and it would bind into a
filing package exactly as if it did.

So the rule this module encodes is narrow and deliberate:

    An immutable run whose inputs were withdrawn is not wrong to retain.
    It must not silently present as CURRENT evidence.

Why this is derived, never stored
---------------------------------
Three properties of the platform make a *derived* status the only honest
mechanism:

1. **Sealed runs are append-only evidence.** Stamping a status column onto
   ``regulatory_runs`` would rewrite the record whose immutability is the whole
   point — the exact failure the platform exists to prevent.
2. **Withdrawal is reversible** (``status='reversed'``, another governed act).
   A stored flag would have to be un-stamped, which is a second write to sealed
   evidence, and any missed reversal leaves a permanently mis-marked run.
   Derivation is automatically correct in both directions: a reversed
   withdrawal simply stops matching.
3. **Both operands are already retained immutably.** ``canonical_withdrawals``
   is the governed, append-only register of the act; the withdrawn canonical
   rows survive with ``withdrawn_at`` / ``withdrawn_by_batch_id`` stamped. The
   status is a pure function of records the platform never deletes, so it is
   reproducible for an examiner years later without a backfill having been run
   at the right moment.

The intersection rule
---------------------
A withdrawal ``W`` orphans a run ``R`` when ALL of the following hold. Each
clause mirrors a real predicate in ``app/services/fact_derivation.py`` rather
than a guess, and every unknown resolves toward "affected" (fail-closed).

``W.status == 'applied'``
    ``pending`` stamped nothing; ``reversed`` put the rows back.

``W`` and ``R`` are the same tenant and bank
    Withdrawal scope never crosses either.

``W``'s date is inside ``R``'s derivation window
    Per entity, because the derivation's own predicates differ:

    * ``position`` — ``_load_position_rows`` filters
      ``CanonicalPositionSnapshot.as_of_date == as_of``, so ONLY an equal date
      can have entered the run. Widening this to ``<=`` would refuse filings
      that are genuinely unaffected.
    * ``gl_account`` — ``_load_canonical`` filters
      ``CanonicalGlAccount.as_of_date <= as_of`` (the whole current chart
      history), so any earlier date is in scope.
    * ``counterparty`` / ``product`` — dimension rows joined off the snapshots
      with no independent date filter; treated as ``<=`` because that is the
      conservative reading.

``R`` was sealed while those rows were current
    Two halves, both required:

    * ``R.computed_at < W.approved_at`` — the rows were still live when the run
      sealed. A run created after the withdrawal already excluded them.
    * ``R.computed_at >= W.first_ingested_at`` — the rows existed at all.
      ``first_ingested_at`` is the earliest ``ingested_at`` among the rows the
      withdrawal retired; a run that predates the duplicated book never saw it.
      ``None`` (not established) resolves to *affected*.

This module is pure: no SQLAlchemy, no FastAPI, no ``app.services`` import.
Resolving the withdrawal register from the database is
``app/services/withdrawal_impact.py``'s job; deciding what the register means is
this one's.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from app.domain.authority.outcomes import OutcomeDetail, OutcomeState, outcome

__all__ = [
    "DATE_INCLUSIVE_ENTITIES",
    "EVIDENCE_METRIC_ID",
    "EvidenceStatus",
    "RunEvidence",
    "WithdrawalImpact",
    "WithdrawalRecord",
    "assess_run_evidence",
]

#: The metric identifier every evidence refusal is raised against. Not a
#: financial metric — the *standing of the run itself* — so it is named once
#: here and never spelled inline.
EVIDENCE_METRIC_ID = "run_evidence"

#: Entities whose derivation predicate is ``as_of_date <= run as-of`` rather
#: than an equality. See the module docstring for the per-entity derivation
#: predicates this mirrors.
DATE_INCLUSIVE_ENTITIES: frozenset[str] = frozenset({"gl_account", "counterparty", "product"})


class EvidenceStatus(StrEnum):
    """Whether a sealed run's inputs still stand as the bank's current book."""

    #: No applied withdrawal intersects this run's derivation.
    CURRENT = "current"
    #: At least one governed withdrawal retired rows this run computed on.
    INPUTS_WITHDRAWN = "inputs_withdrawn"


@dataclass(frozen=True, slots=True)
class WithdrawalRecord:
    """One governed withdrawal, flattened to what the decision needs.

    Deliberately a plain value object rather than a Protocol over the ORM row:
    ``first_ingested_at`` is not a column on ``canonical_withdrawals`` — it is
    measured over the rows the withdrawal retired — so the service layer has to
    assemble this shape anyway, and making that explicit keeps the pure rule
    honest about every input it consumes.
    """

    withdrawal_id: str
    organization_id: str
    bank_id: str
    entity: str
    source_system: str
    as_of_date: date
    status: str
    approved_at: datetime | None
    #: Earliest ``ingested_at`` among the rows this withdrawal retired.
    #: ``None`` means "could not be established" and resolves to AFFECTED.
    first_ingested_at: datetime | None = None
    position_type: str | None = None
    rows_withdrawn: int = 0
    reason: str = ""

    def covers_as_of(self, run_as_of: date) -> bool:
        """Does this withdrawal's date fall inside a run's derivation window?"""
        if self.entity in DATE_INCLUSIVE_ENTITIES:
            return self.as_of_date <= run_as_of
        return self.as_of_date == run_as_of

    def was_live_at(self, sealed_at: datetime | None) -> bool:
        """Were the retired rows current in the book when the run sealed?

        Fail-closed on every unknown: a run with no timestamp, or a withdrawal
        with no approval time, or rows whose first ingestion could not be
        established, all resolve to *affected* rather than to *clean*.
        """
        if sealed_at is None or self.approved_at is None:
            return True
        if sealed_at >= self.approved_at:
            # Sealed after the rows were retired: the derivation excluded them.
            return False
        if self.first_ingested_at is None:
            return True
        return sealed_at >= self.first_ingested_at

    def descriptor(self) -> str:
        """Stable ``kind:name`` item for :class:`OutcomeDetail.items`."""
        scope = f"{self.source_system}/{self.entity}"
        if self.position_type:
            scope = f"{scope}[{self.position_type}]"
        return f"withdrawal:{self.withdrawal_id}:{scope}@{self.as_of_date.isoformat()}"


@dataclass(frozen=True, slots=True)
class WithdrawalImpact:
    """Why one withdrawal bears on one run. Carried for the audit trail."""

    withdrawal_id: str
    entity: str
    source_system: str
    as_of_date: date
    position_type: str | None
    rows_withdrawn: int
    approved_at: datetime | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form. Keys are wire contract — do not rename."""
        return {
            "withdrawal_id": self.withdrawal_id,
            "entity": self.entity,
            "source_system": self.source_system,
            "as_of_date": self.as_of_date.isoformat(),
            "position_type": self.position_type,
            "rows_withdrawn": self.rows_withdrawn,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class RunEvidence:
    """The standing of one sealed run's inputs. Derived, never stored."""

    run_id: str
    status: EvidenceStatus
    impacts: tuple[WithdrawalImpact, ...] = field(default=())

    @property
    def is_current(self) -> bool:
        return self.status is EvidenceStatus.CURRENT

    @property
    def blocks_filing(self) -> bool:
        """Fail-closed: withdrawn inputs must not reach a regulatory filing."""
        return not self.is_current

    @property
    def rows_withdrawn(self) -> int:
        return sum(impact.rows_withdrawn for impact in self.impacts)

    def reason(self) -> str | None:
        """Bank-facing single-line explanation, or ``None`` when current."""
        if self.is_current:
            return None
        dates = sorted({impact.as_of_date.isoformat() for impact in self.impacts})
        systems = sorted({impact.source_system for impact in self.impacts})
        return (
            "This run was sealed against canonical data that has since been "
            f"withdrawn under two-officer approval ({', '.join(systems)}; "
            f"{', '.join(dates)}; {self.rows_withdrawn:,} rows). Its figures remain "
            "a faithful record of the book as it stood, and are retained "
            "unchanged, but they are no longer the institution's current "
            "position and must not be filed. Re-run the module to compute on "
            "the current book."
        )

    def as_outcome(self) -> OutcomeDetail | None:
        """The fail-closed refusal, or ``None`` when the evidence stands.

        ``DATA_QUALITY_BLOCK`` is the declared state that fits: the inputs
        exist and are readable, but they no longer pass the institution's own
        gate on what counts as its book. It is deliberately NOT a new sixth
        state — the vocabulary in ``outcomes`` is closed on purpose.
        """
        if self.is_current:
            return None
        reason = self.reason()
        assert reason is not None  # noqa: S101 - narrowed by is_current above
        return outcome(
            OutcomeState.DATA_QUALITY_BLOCK,
            metric_id=EVIDENCE_METRIC_ID,
            reason=reason,
            items=tuple(
                f"withdrawal:{impact.withdrawal_id}:{impact.source_system}/{impact.entity}"
                f"@{impact.as_of_date.isoformat()}"
                for impact in self.impacts
            ),
            context={"run_id": self.run_id, "rows_withdrawn": self.rows_withdrawn},
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form. Keys are wire contract — do not rename."""
        return {
            "status": self.status.value,
            "is_current": self.is_current,
            "blocks_filing": self.blocks_filing,
            "reason": self.reason(),
            "rows_withdrawn": self.rows_withdrawn,
            "withdrawals": [impact.to_dict() for impact in self.impacts],
        }


def _run_as_of(as_of: date | str | None) -> date | None:
    if as_of is None or isinstance(as_of, date):
        return as_of
    try:
        return date.fromisoformat(str(as_of))
    except ValueError:
        return None


def assess_run_evidence(  # noqa: PLR0913 - keyword-only; the rule names every input it consumes
    *,
    run_id: str,
    organization_id: str,
    bank_id: str,
    as_of_date: date | str | None,
    sealed_at: datetime | None,
    withdrawals: Iterable[WithdrawalRecord],
) -> RunEvidence:
    """Decide whether a sealed run's inputs still stand. Pure.

    ``as_of_date`` accepts the ISO string the run's ``inputs`` snapshot carries
    as well as a real ``date``. An ``as_of_date`` that cannot be resolved is
    fail-closed: every applied withdrawal for the bank is treated as covering
    it, because a run whose business date is unreadable cannot be certified
    clean.
    """
    run_as_of = _run_as_of(as_of_date)
    impacts: list[WithdrawalImpact] = []
    for record in withdrawals:
        if record.status != "applied":
            continue
        if record.organization_id != organization_id or record.bank_id != bank_id:
            continue
        if run_as_of is not None and not record.covers_as_of(run_as_of):
            continue
        if not record.was_live_at(sealed_at):
            continue
        impacts.append(
            WithdrawalImpact(
                withdrawal_id=record.withdrawal_id,
                entity=record.entity,
                source_system=record.source_system,
                as_of_date=record.as_of_date,
                position_type=record.position_type,
                rows_withdrawn=record.rows_withdrawn,
                approved_at=record.approved_at,
                reason=record.reason,
            )
        )
    if not impacts:
        return RunEvidence(run_id=run_id, status=EvidenceStatus.CURRENT)
    impacts.sort(key=lambda impact: (impact.as_of_date, impact.source_system, impact.withdrawal_id))
    return RunEvidence(
        run_id=run_id,
        status=EvidenceStatus.INPUTS_WITHDRAWN,
        impacts=tuple(impacts),
    )
