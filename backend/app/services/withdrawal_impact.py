"""Resolve the governed withdrawal register and apply it to sealed runs (D-12).

The DECISION lives in :mod:`app.domain.authority.evidence` and is pure. This
module does the two things the pure rule cannot: read
``canonical_withdrawals``, and measure the one input that is not a column on it
— ``first_ingested_at``, the earliest ``ingested_at`` among the rows each
withdrawal actually retired. Without that measurement the rule cannot tell a run
sealed *before* a duplicated book was ingested (unaffected) from one sealed
*after* it (affected), and would over-refuse every historical run for the date.

Nothing here writes. A sealed run is append-only evidence; its standing is
derived on every read from two records the platform retains immutably.

The three surfaces that consume it
----------------------------------
* **Every run read.** ``regulatory_liquidity._read_run`` and ``_read_summary``
  are the shared readers behind the cross-module run history
  (``GET /banks/{id}/regulatory-runs`` and ``.../{run_id}``, whichever module
  produced the run), so an ``evidence`` block on those two functions is an
  ``evidence`` block on every run any API surface returns.
* **The package mint site.** ``regulatory_reporting.generation._generate_package``
  is the only place a ``RegulatoryPackage`` comes into existence; refusing there
  makes a return built on withdrawn evidence structurally impossible rather than
  merely discouraged — the same reasoning that puts the eligibility and
  balance-identity gates on that line.
* **Every post-mint filing act.** ``filing_reconciliation.assert_package_reconciled``
  is the single funnel through which approval, certification, signing and
  transmission re-ask "is this package still fit to file?". A withdrawal
  approved *after* a package was minted lands in exactly that window.

Tenant scope comes from the rows themselves — a ``RegulatoryRun`` carries its
own ``organization_id`` and ``bank_id``, and it was already fetched under a
tenant-scoped query — so this module takes no ``TenantContext`` and cannot
widen anyone's scope by being called with the wrong one.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import observability
from app.domain.authority.evidence import (
    EVIDENCE_METRIC_ID,
    EvidenceStatus,
    RunEvidence,
    WithdrawalRecord,
    assess_run_evidence,
)
from app.domain.authority.outcomes import NotComputable, OutcomeDetail, OutcomeState
from app.models.canonical import (
    CanonicalCounterparty,
    CanonicalGlAccount,
    CanonicalPositionSnapshot,
    CanonicalProduct,
)
from app.models.canonical_withdrawal import CanonicalWithdrawal
from app.models.regulatory_reporting import RegulatoryPackage
from app.models.regulatory_run import RegulatoryRun

__all__ = [
    "WITHDRAWN_EVIDENCE_BLOCK_CODE",
    "WithdrawnEvidenceError",
    "assert_package_source_runs_current",
    "assert_source_runs_current",
    "invalidate_register",
    "load_withdrawal_register",
    "run_evidence",
]

#: Stable, greppable refusal code, mirroring
#: ``reconciliation.BALANCE_IDENTITY_BLOCK_CODE``. The observability layer reads
#: ``error_code`` off a refusal that carries only prose in its ``detail``.
WITHDRAWN_EVIDENCE_BLOCK_CODE = f"{OutcomeState.DATA_QUALITY_BLOCK.value}:{EVIDENCE_METRIC_ID}"

#: Session-scoped memo of the register. One request can assess many runs (a run
#: list page, or a package mint binding several modules), the register cannot
#: change inside a read-only assessment, and a ``Session`` is one unit of work —
#: the same reasoning ``filing_reconciliation._BOOK_IDENTITY_CACHE_KEY`` uses.
#:
#: "Cannot change inside a read-only assessment" is the whole warrant, so the two
#: acts that DO change it — ``approve_withdrawal`` and ``reverse_withdrawal`` —
#: drop the memo through :func:`invalidate_register`. Without that, a session
#: that read a run before approving a withdrawal would keep answering from the
#: pre-approval register, and the gate would pass an orphaned run.
_REGISTER_CACHE_KEY = "withdrawal_impact.register"

#: Which canonical table each withdrawable entity retires, for the
#: ``first_ingested_at`` measurement. Mirrors
#: ``canonical_withdrawal._model_for``; kept as its own map so a widening of
#: ``WITHDRAWABLE_ENTITIES`` that forgets this file fails loudly (KeyError)
#: rather than silently measuring nothing.
_ENTITY_MODELS: dict[str, Any] = {
    "position": CanonicalPositionSnapshot,
    "gl_account": CanonicalGlAccount,
    "counterparty": CanonicalCounterparty,
    "product": CanonicalProduct,
}


class WithdrawnEvidenceError(HTTPException, NotComputable):
    """A filing act is refused because a bound run was sealed on withdrawn data.

    Doubly typed exactly like ``reconciliation.FilingBlockedError``:
    ``HTTPException`` (409, the codebase's configured-state conflict code) so an
    API caller gets a precise, actionable message instead of a 500, and
    ``NotComputable`` so any boundary that already handles fail-closed outcomes
    handles this one identically.

    It refuses the ACT. It never touches the runs it is judging — they remain
    exactly the immutable evidence they were, which is the whole point.
    """

    error_code = WITHDRAWN_EVIDENCE_BLOCK_CODE

    def __init__(self, detail: OutcomeDetail, evidence: Sequence[RunEvidence]) -> None:
        NotComputable.__init__(self, detail)
        HTTPException.__init__(self, status_code=status.HTTP_409_CONFLICT, detail=detail.message)
        self.evidence = tuple(evidence)

    @property
    def message(self) -> str:
        return self.details[0].message


def _aware(stamp: datetime | None) -> datetime | None:
    """Normalise to UTC-aware.

    SQLite round-trips ``DateTime(timezone=True)`` as naive, so a comparison
    between two stamps read from different tables raises rather than answering.
    Every timestamp entering the pure rule passes through here.
    """
    if stamp is not None and stamp.tzinfo is None:
        return stamp.replace(tzinfo=UTC)
    return stamp


def _sealed_at(run: RegulatoryRun) -> datetime | None:
    """When the run's figures were fixed. Falls back down the timestamps.

    The fallback order is deliberately earliest-available: an earlier stamp can
    only make ``was_live_at`` answer "affected", never "clean".
    """
    return _aware(run.completed_at or run.started_at or run.created_at)


def _run_as_of(run: RegulatoryRun) -> date | str | None:
    """The run's business date, from the snapshot it sealed."""
    inputs = run.inputs or {}
    as_of = inputs.get("as_of_date")
    return as_of if isinstance(as_of, str | date) else None


def _first_ingested_at(db: Session, withdrawal: CanonicalWithdrawal) -> datetime | None:
    """Earliest ``ingested_at`` among the rows this withdrawal actually retired.

    Measured over ``withdrawn_by_batch_id``, which every retired row carries,
    rather than re-deriving the scope predicates — the batch IS the record of
    what was retired, so the two can never disagree.
    """
    if withdrawal.withdrawal_batch_id is None:
        return None
    model = _ENTITY_MODELS[withdrawal.entity]
    return _aware(
        db.scalar(
            select(func.min(model.ingested_at)).where(
                model.organization_id == withdrawal.organization_id,
                model.bank_id == withdrawal.bank_id,
                model.withdrawn_by_batch_id == withdrawal.withdrawal_batch_id,
            )
        )
    )


def load_withdrawal_register(
    db: Session, organization_id: str, bank_id: str
) -> tuple[WithdrawalRecord, ...]:
    """Every APPLIED withdrawal for one bank, with its first-ingestion stamp.

    Memoised on the session. ``pending`` stamped nothing and ``reversed`` put
    the rows back, so neither can orphan a run; filtering them here keeps the
    per-withdrawal measurement query off rows that cannot matter.
    """
    cache: dict[tuple[str, str], tuple[WithdrawalRecord, ...]] = db.info.setdefault(
        _REGISTER_CACHE_KEY, {}
    )
    key = (organization_id, bank_id)
    if key in cache:
        return cache[key]
    rows = list(
        db.scalars(
            select(CanonicalWithdrawal)
            .where(
                CanonicalWithdrawal.organization_id == organization_id,
                CanonicalWithdrawal.bank_id == bank_id,
                CanonicalWithdrawal.status == "applied",
            )
            .order_by(CanonicalWithdrawal.as_of_date, CanonicalWithdrawal.id)
        )
    )
    register = tuple(
        WithdrawalRecord(
            withdrawal_id=str(row.id),
            organization_id=row.organization_id,
            bank_id=row.bank_id,
            entity=row.entity,
            source_system=row.source_system,
            as_of_date=row.as_of_date,
            status=row.status,
            approved_at=_aware(row.approved_at),
            first_ingested_at=_first_ingested_at(db, row),
            position_type=row.position_type,
            rows_withdrawn=row.rows_withdrawn,
            reason=row.reason,
        )
        for row in rows
    )
    cache[key] = register
    return register


def invalidate_register(db: Session) -> None:
    """Drop the session memo. Called by the two acts that change the register.

    Deliberately unconditional and whole-session rather than per-bank: a
    withdrawal is rare, the memo costs one query to rebuild, and a narrower
    invalidation is one more thing to get wrong in the direction of passing an
    orphaned run.
    """
    db.info.pop(_REGISTER_CACHE_KEY, None)


def run_evidence(db: Session, run: RegulatoryRun) -> RunEvidence:
    """The derived standing of one sealed run's inputs. Writes nothing."""
    return assess_run_evidence(
        run_id=str(run.id),
        organization_id=str(run.organization_id),
        bank_id=str(run.bank_id),
        as_of_date=_run_as_of(run),
        sealed_at=_sealed_at(run),
        withdrawals=load_withdrawal_register(db, str(run.organization_id), str(run.bank_id)),
    )


def assert_source_runs_current(db: Session, runs: Sequence[RegulatoryRun], *, purpose: str) -> None:
    """Refuse a filing act that would bind a run whose inputs were withdrawn.

    Raises :class:`WithdrawnEvidenceError` — a 409 for an API caller and a
    ``NotComputable`` for a fail-closed boundary — carrying a
    ``DATA_QUALITY_BLOCK`` detail that names every offending withdrawal. Writes
    nothing, and — crucially — does not touch the sealed runs it is judging.

    An empty ``runs`` (a master-data return binds none) is a pass: there is no
    engine evidence to have been orphaned.
    """
    blocked = [
        (run, evidence)
        for run in runs
        if (evidence := run_evidence(db, run)).status is EvidenceStatus.INPUTS_WITHDRAWN
    ]
    if not blocked:
        return
    first_run, first_evidence = blocked[0]
    detail = first_evidence.as_outcome()
    assert detail is not None  # noqa: S101 - narrowed by the INPUTS_WITHDRAWN filter
    observability.emit(
        observability.Condition.CALCULATION_BLOCKED,
        "Filing act REFUSED: a source run was sealed on withdrawn canonical data",
        severity="error",
        purpose=purpose,
        code=WITHDRAWN_EVIDENCE_BLOCK_CODE,
        organization_id=str(first_run.organization_id),
        bank_id=str(first_run.bank_id),
        run_ids=[str(run.id) for run, _ in blocked],
        modules=sorted({str(run.module) for run, _ in blocked}),
        withdrawal_ids=sorted(
            {impact.withdrawal_id for _, evidence in blocked for impact in evidence.impacts}
        ),
    )
    raise WithdrawnEvidenceError(detail, [evidence for _, evidence in blocked])


def assert_package_source_runs_current(
    db: Session, package: RegulatoryPackage, *, purpose: str
) -> None:
    """The same gate, re-asked for an ALREADY-MINTED package.

    Mint-time is the structural gate, but a withdrawal is a governed act that
    can land while a monthly return waits for its approver — the same window
    ``filing_reconciliation.assert_package_reconciled`` exists to close for the
    balance identity. The package's ``source_runs`` column is the binding, so
    the runs are re-read from it rather than re-derived.

    A ``source_runs`` entry naming a run that no longer resolves is skipped
    rather than refused: that is a lookup problem, and the package's own
    provenance already records what it bound.
    """
    runs: list[RegulatoryRun] = []
    for entry in package.source_runs or []:
        raw = entry.get("run_id") if isinstance(entry, dict) else None
        if not raw:
            continue
        try:
            run_id = raw if isinstance(raw, UUID) else UUID(str(raw))
        except ValueError:  # pragma: no cover - a malformed lineage entry
            continue
        run = db.scalar(
            select(RegulatoryRun).where(
                RegulatoryRun.id == run_id,
                RegulatoryRun.organization_id == package.organization_id,
                RegulatoryRun.bank_id == package.bank_id,
            )
        )
        if run is not None:
            runs.append(run)
    assert_source_runs_current(db, runs, purpose=purpose)
