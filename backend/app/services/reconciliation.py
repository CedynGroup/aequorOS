"""Fail-closed data-integrity controls (enterprise audit 2026-08-20, P0-10).

The audit's central finding is *silent substitution*: when an input is missing
the platform supplies a plausible value and proceeds, so a regulatory number can
be filed and signed without anyone learning its inputs were invented. The
balance-sheet identity was the worst instance — any ``assets ≠ liabilities +
equity`` gap was added to ``other_assets`` or ``term_borrowings_gt_1y``, warned
about only above a hardcoded 0.5% of assets, and blocked nothing. RWA, CAR,
leverage, LCR and NSFR were then computed on a manufactured balance sheet.

This module is the control that replaces that behaviour. It is deliberately
small and has one job: decide, with full provenance, whether a bank's ingested
book may be used to produce a FILED number.

Design
------
**The tolerance is governed, not hardcoded.** It resolves through the
regulatory-parameter control plane (``balance_identity_tolerance_pct``,
effective-dated, four-eyed, per institution class/type — see
``app/services/regulatory_parameters.py``). When the control plane holds no row
the module default applies, but it is *stamped as a module default* in every
provenance record it touches, so "which tolerance did this filing use, and who
set it" always has an answer. It is never invisible.

**A material failure blocks.** ``derive_facts`` — the official fact plane that
feeds every immutable ``RegulatoryRun`` — refuses. Everything downstream reaches
:func:`assert_filing_reconciled` through ``app/services/filing_reconciliation.py``,
which is the module that knows how to turn "a bank, a period, a filing act" into
the two totals this gate needs. That wiring is NOT optional decoration: the
independent forensic re-audit of 2026-08-22 (D-2) found this function with zero
production callers while this docstring asserted the integration in the present
tense, and a reviewer who trusted the prose could not see the gap. If you are
adding a new filing surface, call ``filing_reconciliation`` from it; if you are
reading this to check the claim, ``tests/services/test_reconciliation_control.py``
::``test_every_filing_path_reaches_the_reconciliation_gate`` fails the moment any
of those call sites disappears.

**The escape valve is governed too.** ``ReconciliationException`` rows carry
reason, requester, approver, approval timestamp, an effective/expiry window and
a ceiling on the breach they cover; :func:`grant_exception` refuses self-
approval and writes an audit event; every *use* of an exception writes one too.

**A retained plug is visible.** Below tolerance the derivation still balances
the book, but the plug is recorded in the fact's provenance and in the
structured outcome — never applied silently.

**The failure is diagnosed, not merely detected.** The identity control catches
the symptom; :func:`detect_source_overlap` names the cause when the cause is
two source systems each pushing a complete book for the same positions at the
same as-of. It is DIAGNOSTIC and never a second gate: its outcomes are
``advisory=True``, it is reported through the derivation's existing group
warnings and this module's existing audit event, and the identity control's
verdict is unchanged by it.

Outcome vocabulary
------------------
The blocking vocabulary is WS-A's ``app.domain.authority.outcomes``:
``OutcomeState.RECONCILIATION_FAILED`` / ``MISSING_REQUIRED_INPUT``, an
``OutcomeDetail`` carrying the metric, reason, failing items and context, and
``NotComputable`` as the raise shape. :class:`FilingBlockedError` is doubly
typed (``HTTPException`` + ``NotComputable``) exactly like
``sdi_capital.SdiCapitalPolicyUnresolved``, so an API caller gets a precise 409
and a fail-closed boundary handles it identically. No competing vocabulary is
introduced here.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.base import utc_now
from app.domain.authority.outcomes import (
    BLOCKING_STATES,
    NotComputable,
    OutcomeDetail,
    OutcomeState,
)
from app.domain.authority.outcomes import outcome as build_outcome
from app.models import Bank, ReconciliationException
from app.services import live_refresh_triggers, regulatory_parameters
from app.services.audit import record_event

# ---------------------------------------------------------------------------
# Outcome vocabulary (WS-A contract)
# ---------------------------------------------------------------------------

NOT_COMPUTABLE = OutcomeState.NOT_COMPUTABLE
MISSING_REQUIRED_INPUT = OutcomeState.MISSING_REQUIRED_INPUT
POLICY_UNRESOLVED = OutcomeState.POLICY_UNRESOLVED
DATA_QUALITY_BLOCK = OutcomeState.DATA_QUALITY_BLOCK
RECONCILIATION_FAILED = OutcomeState.RECONCILIATION_FAILED


def blocks_filing(state: OutcomeState | None) -> bool:
    """Whether ``state`` bars a filing, per WS-A's ``BLOCKING_STATES``."""
    return state is not None and state in BLOCKING_STATES


# ---------------------------------------------------------------------------
# Controls and governed tolerance
# ---------------------------------------------------------------------------

CONTROL_BALANCE_SHEET_IDENTITY = "balance_sheet_identity"

#: The governed control-plane code carrying the balance-sheet identity
#: tolerance, as a PERCENT of total assets. Seeded in
#: ``regulatory_parameters.SEED_PARAMETERS``; operator-editable under four eyes.
TOLERANCE_PARAM_CODE = "balance_identity_tolerance_pct"

#: Applied only when the control plane holds no approved row for the bank's
#: scope. It is versioned (bump ``MODULE_DEFAULT_VERSION`` when it changes) and
#: is stamped ``source="module_default"`` everywhere it is used, so a filing
#: produced under it is distinguishable from one produced under a governed row.
#:
#: 0.10% of total assets. Deliberately an order of magnitude tighter than the
#: 0.5% warn threshold it replaces: the old number gated a warning nobody read,
#: this one gates a filing.
MODULE_DEFAULT_TOLERANCE_PCT = Decimal("0.10")
MODULE_DEFAULT_VERSION = "2026-08-21.1"

_HUNDRED = Decimal("100")
_ZERO = Decimal("0")
_FRACTION_Q = Decimal("0.00000001")
#: Money is presented to two decimals with no grouping separator: the
#: separator convention belongs to the reader's locale, not to backend prose.
_MONEY_Q = Decimal("0.01")

BALANCE_IDENTITY_BLOCK_CODE = "balance_sheet_identity_unreconciled"

RECONCILIATION_GRANTED_EVENT = "reconciliation_exception.granted"
RECONCILIATION_REVOKED_EVENT = "reconciliation_exception.revoked"
RECONCILIATION_APPLIED_EVENT = "reconciliation_exception.applied"
RECONCILIATION_CHECK_EVENT = "reconciliation.balance_sheet_identity"


class ReconciliationExceptionError(ValueError):
    """A proposed exception fails the governance rules (four eyes, window, …)."""


@dataclass(frozen=True)
class ResolvedTolerance:
    """The tolerance in force for one bank and as-of date, with provenance."""

    fraction: Decimal
    percent: Decimal
    source: str  # 'control_plane' | 'module_default'
    param_code: str
    scope_type: str | None = None
    scope_key: str | None = None
    jurisdiction_code: str | None = None
    effective_from: date | None = None
    parameter_id: str | None = None
    confirmation_status: str | None = None
    module_default_version: str | None = None

    def provenance(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "param_code": self.param_code,
            "tolerance_pct": str(self.percent),
            "source": self.source,
        }
        if self.source == "control_plane":
            record.update(
                {
                    "scope_type": self.scope_type,
                    "scope_key": self.scope_key,
                    "jurisdiction_code": self.jurisdiction_code,
                    "effective_from": (
                        self.effective_from.isoformat() if self.effective_from else None
                    ),
                    "parameter_id": self.parameter_id,
                    "confirmation_status": self.confirmation_status,
                }
            )
        else:
            record["module_default_version"] = self.module_default_version
        return record


@dataclass(frozen=True)
class ExceptionGrant:
    """The live exception covering a check, flattened for provenance."""

    exception_id: str
    reason: str
    approved_by: str
    approval_timestamp: datetime
    effective_from: date
    effective_to: date | None
    max_gap_fraction: Decimal

    def provenance(self) -> dict[str, Any]:
        return {
            "exception_id": self.exception_id,
            "reason": self.reason,
            "approved_by": self.approved_by,
            "approval_timestamp": self.approval_timestamp.isoformat(),
            "effective_from": self.effective_from.isoformat(),
            "effective_to": self.effective_to.isoformat() if self.effective_to else None,
            "max_gap_fraction": str(self.max_gap_fraction),
        }


@dataclass(frozen=True)
class BalanceIdentityOutcome:
    """The verdict of ``Assets = Liabilities + Equity`` for one derivation."""

    control: str
    assets: Decimal
    funding: Decimal
    gap: Decimal
    gap_fraction: Decimal
    tolerance: ResolvedTolerance
    within_tolerance: bool
    #: The live exception for this bank/control, whether or not it covers the
    #: gap. Kept even when it does NOT cover, so the refusal can say so.
    exception: ExceptionGrant | None
    #: True only when the exception's ceiling actually covers this gap.
    exception_applied: bool
    outcome: Any | None  # a blocking state, or None when the check passes
    plug_applied: Decimal
    plug_target: str | None

    @property
    def blocks_filing(self) -> bool:
        return self.outcome is not None and blocks_filing(self.outcome)

    @property
    def status(self) -> str:
        if self.within_tolerance:
            return "within_tolerance"
        if self.exception_applied:
            return "exception_applied"
        return "blocked"

    def provenance(self) -> dict[str, Any]:
        """The record stamped onto plugged facts and into the audit event."""
        record: dict[str, Any] = {
            "control": self.control,
            "status": self.status,
            "assets": str(self.assets),
            "funding": str(self.funding),
            "gap": str(self.gap),
            "gap_fraction": str(self.gap_fraction),
            "tolerance": self.tolerance.provenance(),
        }
        if self.exception is not None:
            record["exception"] = {
                **self.exception.provenance(),
                "covers_gap": self.exception_applied,
            }
        if self.plug_applied != _ZERO:
            record["plug"] = {"amount": str(self.plug_applied), "target": self.plug_target}
        if self.outcome is not None:
            record["outcome"] = str(getattr(self.outcome, "value", self.outcome))
        return record

    def message(self, currency: str) -> str:
        pct = (self.gap_fraction * _HUNDRED).quantize(Decimal("0.0001"))
        head = (
            f"Balance-sheet identity failed: assets {self.assets} {currency} against "
            f"liabilities + equity {self.funding} {currency} — a gap of {abs(self.gap)} "
            f"{currency} ({pct}% of assets) versus a governed tolerance of "
            f"{self.tolerance.percent}% ({self.tolerance.source})."
        )
        if self.exception is not None:
            return (
                f"{head} The gap exceeds the ceiling of the active reconciliation "
                f"exception {self.exception.exception_id} "
                f"({self.exception.max_gap_fraction * _HUNDRED}% of assets)."
            )

        return (
            f"{head} Reconcile the general ledger against the sub-ledgers, or record an "
            "approved reconciliation exception, before producing an official run. No "
            "regulatory number is computed on a plugged balance sheet."
        )


@dataclass(frozen=True)
class ReconciliationPolicy:
    """Everything the (pure) derivation needs to decide, loaded once per run.

    The derivation is pure over its canonical snapshot, so the DB-backed parts —
    the governed tolerance and any live exception — are resolved up front and
    carried in.
    """

    tolerance: ResolvedTolerance
    exception: ExceptionGrant | None

    def evaluate_balance_identity(
        self, assets: Decimal, funding: Decimal, *, plug_when_blocked: bool = False
    ) -> tuple[BalanceIdentityOutcome, Decimal, str | None]:
        """Verdict plus the plug (amount, target) the derivation should apply.

        ``plug_when_blocked`` is the LIVE plane. A blocked book produces no
        official facts at all, so the plug there is moot; the live view still
        materialises (an operator has to see the broken book to fix it) and the
        plug is applied so the live numbers stay arithmetically coherent — but
        it is stamped ``status="blocked"`` in the fact's provenance and the
        outcome still reports ``blocks_filing``. Nothing derived from a blocked
        book may be filed.
        """
        gap = funding - assets
        gap_fraction = (
            (abs(gap) / assets).quantize(_FRACTION_Q, rounding=ROUND_HALF_UP)
            if assets > _ZERO
            else (_ZERO if gap == _ZERO else Decimal("1"))
        )
        within = gap_fraction <= self.tolerance.fraction
        covered = self.exception is not None and gap_fraction <= self.exception.max_gap_fraction
        allowed = within or covered
        plugging = allowed or plug_when_blocked
        plug_target: str | None = None
        if plugging and gap > _ZERO:
            plug_target = "other_assets"
        elif plugging and gap < _ZERO:
            plug_target = "term_borrowings_gt_1y"
        plug = abs(gap) if plugging and gap != _ZERO else _ZERO
        outcome = None if allowed else RECONCILIATION_FAILED
        result = BalanceIdentityOutcome(
            control=CONTROL_BALANCE_SHEET_IDENTITY,
            assets=assets,
            funding=funding,
            gap=gap,
            gap_fraction=gap_fraction,
            tolerance=self.tolerance,
            within_tolerance=within,
            exception=self.exception,
            exception_applied=covered,
            outcome=outcome,
            plug_applied=plug,
            plug_target=plug_target,
        )
        return result, plug, plug_target


# ---------------------------------------------------------------------------
# Source-book overlap (the diagnosis, not a second gate)
# ---------------------------------------------------------------------------

#: The control this diagnosis is filed under. It is DIAGNOSTIC: it explains a
#: balance-sheet identity failure, it never produces one. Every outcome it
#: builds is ``advisory=True``, so ``OutcomeDetail.blocks_filing`` is False and
#: no filing path can start refusing on it by accident.
CONTROL_SOURCE_BOOK_OVERLAP = "source_book_overlap"

SOURCE_OVERLAP_METRIC_ID = "source_book_overlap"

#: Operator-facing names for the ingestion-channel and position-type enums.
#: The enum values are WIRE values (mapping configs, adapter identities, DB
#: check constraints) and must never reach a bank-facing surface — the audit
#: provenance keeps the raw code, the prose gets the label.
SOURCE_SYSTEM_LABELS: dict[str, str] = {
    "EXCEL_CSV": "Excel/CSV upload",
    "T24": "Temenos T24",
    "FINACLE": "Finacle",
    "FLEXCUBE": "FlexCube",
    "DB_DIRECT": "direct database connection",
    "SFTP_DROP": "SFTP drop",
    "API_GENERIC": "generic API feed",
    "API_PUSH": "API push",
    "BLOOMBERG": "Bloomberg",
    # Vendor naming rule (CLAUDE.md): the Refinitiv brand is retired; the wire
    # value stays ``REFINITIV`` for DB/wire stability, the label does not.
    "REFINITIV": "LSEG (formerly Refinitiv)",
    "MANUAL_UPLOAD": "manual upload",
    "MANUAL": "manual entry",
    "AEQUOR_DESK": "AequorOS market desk",
}

POSITION_TYPE_LABELS: dict[str, str] = {
    "LOAN": "loans",
    "DEPOSIT": "deposits",
    "SECURITY_HOLDING": "securities",
    "DERIVATIVE": "derivatives",
    "FX_HEDGE": "foreign-exchange hedges",
    "INTEREST_RATE_SWAP": "interest-rate swaps",
    "CASH": "cash",
    "INTERBANK_PLACEMENT": "interbank placements",
    "INTERBANK_BORROWING": "interbank borrowings",
    "LC_GUARANTEE": "guarantees and letters of credit",
    "COMMITMENT_UNDRAWN": "undrawn commitments",
    "OTHER_ASSET": "other assets",
    "OTHER_LIABILITY": "other liabilities",
}


def source_system_label(code: str) -> str:
    """The operator-facing name of an ingestion channel, never its enum value."""
    return SOURCE_SYSTEM_LABELS.get(code, code.replace("_", " ").lower())


def position_type_label(code: str) -> str:
    """The operator-facing name of a position type, never its enum value."""
    return POSITION_TYPE_LABELS.get(code, code.replace("_", " ").lower())


#: The record-count axis of materiality. A second source carrying at least this
#: share of a position type's positions is a BOOK, not an adjustment feed.
#:
#: It exists because the amount axis alone is blind to a duplicated book whose
#: amounts do not convert into the bank's base currency — off-balance-sheet
#: guarantees ingested in a foreign currency with no stated base-currency
#: amount contribute exactly 0 to the amount test while being a complete second
#: copy of the guarantee book. Dropping such a type for want of a measurable
#: amount would be the same silent substitution this module exists to stop.
MIN_CONTESTED_RECORD_SHARE = Decimal("0.20")
SOURCE_OVERLAP_MODULE_DEFAULT_VERSION = "2026-08-22.1"

#: Why one contested position type cleared materiality. Both are reported; the
#: distinction matters because a record-share-only contest means the platform
#: could not size the second book, not that the second book is small.
REASON_MATERIAL_AMOUNT = "material_amount"
REASON_MATERIAL_RECORD_SHARE = "material_record_share"


@dataclass(frozen=True)
class SourceBook:
    """One source system's book for one position type at one as-of."""

    source_system: str
    position_type: str
    rows: int
    total: Decimal

    def provenance(self) -> dict[str, Any]:
        return {
            "source_system": self.source_system,
            "position_type": self.position_type,
            "rows": self.rows,
            "total": str(self.total),
        }


@dataclass(frozen=True)
class ContestedType:
    """One position type reported by two or more source systems, materially."""

    position_type: str
    #: Largest book first, so ``books[0]`` is the incumbent and the rest are the
    #: candidates for retirement. Ties break on source system for determinism.
    books: tuple[SourceBook, ...]
    reasons: tuple[str, ...]

    @property
    def incumbent(self) -> SourceBook:
        """The largest book for this type — the presumed system of record."""
        return self.books[0]

    @property
    def challengers(self) -> tuple[SourceBook, ...]:
        """Every other system's book for this type: the retirement candidates."""
        return self.books[1:]

    @property
    def combined_total(self) -> Decimal:
        return sum((book.total for book in self.books), _ZERO)

    @property
    def combined_rows(self) -> int:
        return sum(book.rows for book in self.books)

    @property
    def unmeasured(self) -> tuple[SourceBook, ...]:
        """Challenger books holding positions but stating no base-currency amount.

        Their contribution is UNKNOWN, not zero, and the prose says so — a
        second copy of the guarantee book whose amounts never converted is
        exactly what a naive amount test would write off as nothing.
        """
        return tuple(book for book in self.challengers if book.rows > 0 and book.total <= _ZERO)

    def provenance(self) -> dict[str, Any]:
        return {
            "position_type": self.position_type,
            "reasons": list(self.reasons),
            "combined_total": str(self.combined_total),
            "combined_rows": self.combined_rows,
            "books": [book.provenance() for book in self.books],
        }


@dataclass(frozen=True)
class SourceOverlapOutcome:
    """Whether this bank's source systems complement or duplicate each other."""

    control: str
    #: Every source system that carried a current, accepted position at the
    #: as-of, sorted — including the ones that overlap with nobody.
    source_systems: tuple[str, ...]
    books: tuple[SourceBook, ...]
    contested: tuple[ContestedType, ...]
    book_total: Decimal
    amount_floor: Decimal
    record_share_floor: Decimal
    tolerance: ResolvedTolerance
    #: False only when there is no book to assess. A bank with one source and a
    #: full book is *determined and clean*, which is a different answer from
    #: "we could not tell", and the two are never conflated.
    determined: bool
    state: OutcomeState | None

    @property
    def overlapping(self) -> bool:
        return bool(self.contested)

    @property
    def duplicated_total(self) -> Decimal:
        """Σ of the non-largest books across contested types.

        A GROSS size-of-the-problem figure spanning both sides of the balance
        sheet, on the reading that the largest book per type is the incumbent.
        It is NOT the balance-sheet identity gap (assets and liabilities are
        double counted here without netting) and nothing subtracts it from
        anything — it exists so an operator can see the scale before choosing
        which feed to retire.
        """
        return sum(
            (book.total for contest in self.contested for book in contest.books[1:]),
            _ZERO,
        )

    def provenance(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "control": self.control,
            "status": self._status(),
            "source_systems": list(self.source_systems),
            "book_total": str(self.book_total),
            "amount_floor": str(self.amount_floor),
            "record_share_floor": str(self.record_share_floor),
            "module_default_version": SOURCE_OVERLAP_MODULE_DEFAULT_VERSION,
            "tolerance": self.tolerance.provenance(),
            "contested": [contest.provenance() for contest in self.contested],
        }
        if self.overlapping:
            record["duplicated_total"] = str(self.duplicated_total)
        if self.state is not None:
            record["outcome"] = self.state.value
        return record

    def _status(self) -> str:
        if not self.determined:
            return "not_determined"
        return "overlapping" if self.overlapping else "partitioned"

    def message(self, currency: str) -> str | None:
        """The operator-facing diagnosis, or ``None`` when there is nothing to say.

        A clean, single-source or properly partitioned book produces NO message:
        a detector that speaks on a healthy book teaches operators to ignore it.
        """
        if not self.determined:
            return (
                "Whether more than one system is reporting the same positions could not be "
                "checked: no accepted position data exists for this date."
            )
        if not self.contested:
            return None
        systems = _join(tuple(source_system_label(code) for code in self._contesting_systems()))
        clauses = "; ".join(self._clause(contest, currency) for contest in self.contested)
        return (
            f"{systems} are each reporting a position book for this date, so part of this "
            f"balance sheet is counted twice: {clauses}. Each source system supersedes only "
            "its own records, so every one of these books is carried in full and no figure "
            "derived from them is safe to file. Confirm which system is the book of record "
            "for each of these positions, then withdraw the other system's data for this date."
        )

    def detail(
        self, bank_id: str, organization_id: str, as_of: date, currency: str
    ) -> OutcomeDetail | None:
        """The WS-A outcome for this diagnosis, or ``None`` when there is none.

        ALWAYS ``advisory=True``: ``OutcomeDetail.blocks_filing`` is therefore
        False and a boundary that fails closed on outcomes cannot start
        refusing filings because two feeds overlap. The balance-sheet identity
        control is the gate; this is the explanation.

        A partitioned book returns ``None`` rather than a "clean" outcome — a
        detector that files a finding on a healthy book is worse than none.
        """
        state = self.state
        reason = self.message(currency)
        if state is None or reason is None:
            return None
        items = tuple(
            f"position_type:{contest.position_type}:{book.source_system}"
            for contest in self.contested
            for book in contest.books
        )
        return build_outcome(
            state,
            metric_id=SOURCE_OVERLAP_METRIC_ID,
            reason=reason,
            items=items,
            advisory=True,
            context={
                "bank_id": bank_id,
                "organization_id": organization_id,
                "as_of": as_of.isoformat(),
                **self.provenance(),
            },
        )

    def _contesting_systems(self) -> tuple[str, ...]:
        return tuple(sorted({book.source_system for c in self.contested for book in c.books}))

    def _clause(self, contest: ContestedType, currency: str) -> str:
        sides = ", ".join(
            f"{source_system_label(book.source_system)} {_money2(book.total)} {currency} "
            f"over {book.rows} positions"
            for book in contest.books
        )
        clause = f"{position_type_label(contest.position_type)} — {sides}"
        if contest.unmeasured:
            named = _join(
                tuple(source_system_label(book.source_system) for book in contest.unmeasured)
            )
            clause += (
                f" (the {named} book states no {currency} amount for these, so how much it "
                "adds cannot be measured)"
            )
        return clause


def _money2(value: Decimal) -> str:
    """Two decimals, no grouping separator: the locale belongs to the reader."""
    return str(value.quantize(_MONEY_Q, rounding=ROUND_HALF_UP))


def _join(parts: tuple[str, ...]) -> str:
    if len(parts) == 1:
        return parts[0]
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


def tally_source_books(
    rows: Iterable[tuple[str, str, Decimal]],
) -> tuple[SourceBook, ...]:
    """Count and total the current book per (source system, position type).

    ``rows`` are ``(source_system, position_type, base_currency_amount)`` for
    exactly the positions the derivation used — the current generation at the
    as-of, in an included validation status. The amount is the one the balance
    sheet is built from, so the diagnosis is measured in the same units as the
    control it explains.
    """
    tally: dict[tuple[str, str], tuple[int, Decimal]] = {}
    for source_system, position_type, amount in rows:
        key = (source_system, position_type)
        count, total = tally.get(key, (0, _ZERO))
        tally[key] = (count + 1, total + amount)
    return tuple(
        SourceBook(source_system=key[0], position_type=key[1], rows=count, total=total)
        for key, (count, total) in sorted(tally.items())
    )


def detect_source_overlap(
    books: Sequence[SourceBook],
    *,
    tolerance: ResolvedTolerance,
    record_share_floor: Decimal = MIN_CONTESTED_RECORD_SHARE,
) -> SourceOverlapOutcome:
    """Decide whether this bank's source systems complement or duplicate each other.

    **Overlapping** means: at one as-of, two or more source systems each carry
    current, accepted positions of the SAME position type, and the smaller of
    those books is material on at least one axis —

    * **amount** — its base-currency total is at least the governed
      balance-sheet identity tolerance applied to the bank's whole position
      book. The floor is deliberately the SAME governed number the identity
      control uses (0.10% of the book by module default, whatever the control
      plane says otherwise): a contested book too small to move the identity
      past its own tolerance is not the diagnosis an operator is looking for,
      and inventing a second, separately-tuned materiality number would be one
      more ungoverned constant.
    * **records** — it carries at least ``record_share_floor`` of that type's
      positions. This axis is what stops the amount axis from silently
      dismissing a duplicated book whose amounts do not convert into the base
      currency.

    Complementary sources partition the book — core banking owns the loans, a
    treasury system owns the securities — and produce NO contested type at all.
    A source contributing a handful of rows and a rounding-level amount inside
    a type another system owns is a genuine slice (one branch, one desk, a
    correction file), not a second book, and is deliberately not reported.
    """
    if not books:
        # No book at all: the honest answer is "not assessed", never "clean".
        return SourceOverlapOutcome(
            control=CONTROL_SOURCE_BOOK_OVERLAP,
            source_systems=(),
            books=(),
            contested=(),
            book_total=_ZERO,
            amount_floor=_ZERO,
            record_share_floor=record_share_floor,
            tolerance=tolerance,
            determined=False,
            state=MISSING_REQUIRED_INPUT,
        )

    present = tuple(sorted({book.source_system for book in books}))
    book_total = sum((book.total for book in books), _ZERO)
    amount_floor = (
        (book_total * tolerance.fraction).quantize(_MONEY_Q, rounding=ROUND_HALF_UP)
        if book_total > _ZERO
        else _ZERO
    )

    by_type: dict[str, list[SourceBook]] = {}
    for book in books:
        by_type.setdefault(book.position_type, []).append(book)

    contested: list[ContestedType] = []
    for position_type in sorted(by_type):
        sides = sorted(by_type[position_type], key=lambda b: (-b.total, b.source_system))
        if len(sides) < 2:
            continue
        type_rows = sum(side.rows for side in sides)
        # EVERY challenger is tested, not just the smallest: with three or more
        # systems a material middle book would otherwise hide behind one tiny
        # third feed.
        material_amount = any(
            book_total > _ZERO and side.total >= amount_floor and side.total > _ZERO
            for side in sides[1:]
        )
        material_records = any(
            type_rows > 0 and Decimal(side.rows) / Decimal(type_rows) >= record_share_floor
            for side in sides[1:]
        )
        reasons = [
            reason
            for reason, holds in (
                (REASON_MATERIAL_AMOUNT, material_amount),
                (REASON_MATERIAL_RECORD_SHARE, material_records),
            )
            if holds
        ]
        if reasons:
            contested.append(
                ContestedType(
                    position_type=position_type,
                    books=tuple(sides),
                    reasons=tuple(reasons),
                )
            )

    return SourceOverlapOutcome(
        control=CONTROL_SOURCE_BOOK_OVERLAP,
        source_systems=present,
        books=tuple(books),
        contested=tuple(contested),
        book_total=book_total,
        amount_floor=amount_floor,
        record_share_floor=record_share_floor,
        tolerance=tolerance,
        determined=True,
        # Advisory throughout: the state names the condition for a surface that
        # groups by it, and ``detail()`` marks it advisory so it cannot block.
        state=OutcomeState.DATA_QUALITY_BLOCK if contested else None,
    )


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def resolve_tolerance(db: Session, bank: Bank, as_of: date) -> ResolvedTolerance:
    """The governed tolerance for ``bank`` at ``as_of``.

    ``try_resolve`` (not ``resolve``) on purpose: an unseeded control plane must
    not make the platform uncomputable, it must make the platform *say which
    tolerance it used*. The module default is the documented, versioned stand-in
    and is labelled as such in every provenance record.
    """
    resolved = regulatory_parameters.try_resolve(db, bank, TOLERANCE_PARAM_CODE, as_of=as_of)
    if resolved is None or resolved.value is None:
        return ResolvedTolerance(
            fraction=(MODULE_DEFAULT_TOLERANCE_PCT / _HUNDRED),
            percent=MODULE_DEFAULT_TOLERANCE_PCT,
            source="module_default",
            param_code=TOLERANCE_PARAM_CODE,
            module_default_version=MODULE_DEFAULT_VERSION,
        )
    percent = resolved.normalized_value or _ZERO
    return ResolvedTolerance(
        fraction=(percent / _HUNDRED),
        percent=percent,
        source="control_plane",
        param_code=TOLERANCE_PARAM_CODE,
        scope_type=resolved.scope_type,
        scope_key=resolved.scope_key,
        jurisdiction_code=resolved.jurisdiction_code,
        effective_from=resolved.effective_from,
        parameter_id=resolved.parameter_id,
        confirmation_status=resolved.confirmation_status,
    )


def active_exception(
    db: Session,
    organization_id: str,
    bank_id: str,
    as_of: date,
    *,
    control: str = CONTROL_BALANCE_SHEET_IDENTITY,
) -> ExceptionGrant | None:
    """The live, un-revoked exception covering ``as_of``, widest ceiling first."""
    rows = db.scalars(
        select(ReconciliationException)
        .where(
            ReconciliationException.organization_id == organization_id,
            ReconciliationException.bank_id == bank_id,
            ReconciliationException.control == control,
            ReconciliationException.revoked_at.is_(None),
            ReconciliationException.effective_from <= as_of,
            or_(
                ReconciliationException.effective_to.is_(None),
                ReconciliationException.effective_to >= as_of,
            ),
        )
        .order_by(ReconciliationException.max_gap_fraction.desc(), ReconciliationException.id)
    ).all()
    if not rows:
        return None
    row = rows[0]
    return ExceptionGrant(
        exception_id=str(row.id),
        reason=row.reason,
        approved_by=row.approved_by,
        approval_timestamp=row.approval_timestamp,
        effective_from=row.effective_from,
        effective_to=row.effective_to,
        max_gap_fraction=row.max_gap_fraction,
    )


def load_policy(db: Session, organization_id: str, bank: Bank, as_of: date) -> ReconciliationPolicy:
    """The tolerance + exception state for one derivation."""
    return ReconciliationPolicy(
        tolerance=resolve_tolerance(db, bank, as_of),
        exception=active_exception(db, organization_id, bank.id, as_of),
    )


# ---------------------------------------------------------------------------
# Governance: grant / revoke
# ---------------------------------------------------------------------------


def grant_exception(  # noqa: PLR0913 - governance needs every field named
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    *,
    reason: str,
    approved_by: str,
    max_gap_fraction: Decimal,
    effective_from: date,
    effective_to: date | None = None,
    control: str = CONTROL_BALANCE_SHEET_IDENTITY,
    approved_by_user_id: UUID | None = None,
    requested_by: UUID | None = None,
) -> ReconciliationException:
    """Record an approved exception to a fail-closed reconciliation control.

    Refuses a blank reason, a non-positive or absent ceiling, an inverted
    window, and self-approval (the requester may not be the approver — the same
    maker-checker rule the filing path uses). Writes an audit event.
    """
    if not reason or not reason.strip():
        msg = "A reconciliation exception requires a non-empty reason."
        raise ReconciliationExceptionError(msg)
    if max_gap_fraction is None or max_gap_fraction <= _ZERO:
        msg = "A reconciliation exception requires a positive max_gap_fraction ceiling."
        raise ReconciliationExceptionError(msg)
    if effective_to is not None and effective_to < effective_from:
        msg = "A reconciliation exception's effective_to precedes its effective_from."
        raise ReconciliationExceptionError(msg)
    if not approved_by or not approved_by.strip():
        msg = "A reconciliation exception requires a named approver."
        raise ReconciliationExceptionError(msg)
    maker = requested_by if requested_by is not None else ctx.actor_user_id
    if (
        maker is not None
        and approved_by_user_id is not None
        and str(maker) == str(approved_by_user_id)
    ):
        msg = (
            "A reconciliation exception cannot be approved by the officer who requested "
            "it — a second approver is required."
        )
        raise ReconciliationExceptionError(msg)

    now = utc_now()
    row = ReconciliationException(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        control=control,
        max_gap_fraction=max_gap_fraction,
        effective_from=effective_from,
        effective_to=effective_to,
        reason=reason.strip(),
        requested_by=maker,
        requested_at=now,
        approved_by=approved_by.strip(),
        approved_by_user_id=approved_by_user_id,
        approval_timestamp=now,
    )
    db.add(row)
    db.flush()
    record_event(
        db,
        ctx,
        event_type=RECONCILIATION_GRANTED_EVENT,
        entity_type="bank",
        entity_id=bank.id,
        details={
            "exception_id": str(row.id),
            "control": control,
            "reason": row.reason,
            "max_gap_fraction": str(max_gap_fraction),
            "effective_from": effective_from.isoformat(),
            "effective_to": effective_to.isoformat() if effective_to else None,
            "requested_by": str(maker) if maker else None,
            "approved_by": row.approved_by,
            "approval_timestamp": now.isoformat(),
        },
    )
    live_refresh_triggers.enqueue_bank_change(
        db,
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        reason=f"reconciliation exception granted:{row.id}",
    )
    return row


def revoke_exception(  # noqa: PLR0913 - governed revocation evidence is explicit
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    exception_id: UUID | str,
    *,
    revoked_by: str,
    reason: str,
) -> ReconciliationException:
    """Close a live exception without deleting the record, with an audit event."""
    # ``exception_id`` arrives as a string from API payloads and from
    # ``ExceptionGrant.exception_id``; the column is a real UUID.
    try:
        identifier = exception_id if isinstance(exception_id, UUID) else UUID(str(exception_id))
    except ValueError as exc:
        msg = f"Reconciliation exception id {exception_id!r} is not a valid identifier."
        raise ReconciliationExceptionError(msg) from exc
    row = db.scalar(
        select(ReconciliationException).where(
            ReconciliationException.id == identifier,
            ReconciliationException.organization_id == ctx.organization_id,
            ReconciliationException.bank_id == bank.id,
        )
    )
    if row is None:
        msg = f"Reconciliation exception {exception_id} does not exist for bank {bank.id}."
        raise ReconciliationExceptionError(msg)
    if row.revoked_at is not None:
        return row
    row.revoked_at = utc_now()
    row.revoked_by = revoked_by
    db.flush()
    record_event(
        db,
        ctx,
        event_type=RECONCILIATION_REVOKED_EVENT,
        entity_type="bank",
        entity_id=bank.id,
        details={
            "exception_id": str(row.id),
            "control": row.control,
            "revoked_by": revoked_by,
            "reason": reason,
        },
    )
    live_refresh_triggers.enqueue_bank_change(
        db,
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        reason=f"reconciliation exception revoked:{row.id}",
    )
    return row


# ---------------------------------------------------------------------------
# Audit + filing gate
# ---------------------------------------------------------------------------


def record_check(  # noqa: PLR0913 - the audit record needs every field named
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    as_of: date,
    outcome: BalanceIdentityOutcome,
    *,
    source_overlap: SourceOverlapOutcome | None = None,
) -> None:
    """Persist the verdict of one official-plane check to the audit trail.

    Every official derivation writes one — pass, exception, or block — so the
    question "what did the balance-sheet control say on the day this return was
    produced?" is answerable from ``audit_events`` alone.

    ``source_overlap`` rides the SAME event rather than a second one, and only
    when it has something to say: the operator reading "the identity failed by
    x%" needs "…because two systems both pushed the deposit book" in the same
    record, not in another table. It is deliberately kept OUT of
    ``BalanceIdentityOutcome.provenance()`` — that record is stamped onto the
    plugged fact's ``attributes``, which the FTP and FX input snapshots hash
    verbatim, so a diagnosis landing there would move ``input_hash`` for books
    whose numbers had not changed.
    """
    details: dict[str, Any] = {"as_of_date": as_of.isoformat(), **outcome.provenance()}
    if source_overlap is not None and (source_overlap.overlapping or not source_overlap.determined):
        details["source_overlap"] = source_overlap.provenance()
    record_event(
        db,
        ctx,
        event_type=RECONCILIATION_CHECK_EVENT,
        entity_type="bank",
        entity_id=bank.id,
        details=details,
    )
    if outcome.blocks_filing:
        # A refusal must survive the caller's rollback. Every caller of
        # ``derive_facts`` rolls back or 409s on a ``DerivationError``, so
        # without this commit the one record proving the platform refused —
        # and why — would be discarded with the failed attempt. Nothing else
        # is pending at this point: the check runs before any period or fact
        # is written, and every caller enters with a clean unit of work.
        db.commit()
    if outcome.exception is not None:
        record_event(
            db,
            ctx,
            event_type=RECONCILIATION_APPLIED_EVENT,
            entity_type="bank",
            entity_id=bank.id,
            details=details,
        )


def check_balance_identity(  # noqa: PLR0913 - official-run context is explicit
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    as_of: date,
    assets: Decimal,
    funding: Decimal,
) -> BalanceIdentityOutcome:
    """Stand-alone evaluation for callers that already hold the two totals."""
    policy = load_policy(db, ctx.organization_id, bank, as_of)
    outcome, _plug, _target = policy.evaluate_balance_identity(assets, funding)
    return outcome


#: The metric id every balance-sheet identity failure is reported under, so a
#: package/run refusal is greppable and joinable across planes.
BALANCE_IDENTITY_METRIC_ID = "balance_sheet_identity"


def balance_identity_detail(
    bank: Bank, as_of: date, result: BalanceIdentityOutcome
) -> OutcomeDetail:
    """The WS-A ``OutcomeDetail`` for one blocked balance-sheet identity check."""
    items = [f"fact_group:balance_sheet:{result.plug_target or 'unplugged'}"]
    if result.exception is not None and not result.exception_applied:
        items.append(f"reconciliation_exception:{result.exception.exception_id}")
    return build_outcome(
        OutcomeState.RECONCILIATION_FAILED,
        metric_id=BALANCE_IDENTITY_METRIC_ID,
        reason=result.message(bank.currency or ""),
        items=tuple(items),
        context={
            "bank_id": bank.id,
            "organization_id": bank.organization_id,
            "as_of": as_of.isoformat(),
            "code": BALANCE_IDENTITY_BLOCK_CODE,
            **result.provenance(),
        },
    )


class FilingBlockedError(HTTPException, NotComputable):
    """A filing-plane action is refused by a data-integrity control.

    Doubly typed exactly like ``sdi_capital.SdiCapitalPolicyUnresolved``:
    ``HTTPException`` (409, the codebase's configured-state conflict code) so an
    API caller gets a precise, actionable message instead of a 500, and WS-A's
    ``NotComputable`` so any boundary that already handles fail-closed outcomes
    handles this one identically.
    """

    #: Stable, greppable refusal code for the observability layer, which reads
    #: ``error_code`` off a refusal that carries only prose in its ``detail``.
    error_code = BALANCE_IDENTITY_BLOCK_CODE

    def __init__(self, detail: OutcomeDetail, provenance: dict[str, Any]) -> None:
        NotComputable.__init__(self, detail)
        HTTPException.__init__(self, status_code=status.HTTP_409_CONFLICT, detail=detail.message)
        self.provenance = provenance

    @property
    def message(self) -> str:
        return self.details[0].message

    @property
    def outcome(self) -> OutcomeState:
        return self.details[0].state


def assert_filing_reconciled(  # noqa: PLR0913 - filing gate requires explicit context
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    as_of: date,
    assets: Decimal,
    funding: Decimal,
) -> BalanceIdentityOutcome:
    """The filing gate: raise :class:`FilingBlockedError` on a material failure.

    Called for every filing-plane act through
    ``app/services/filing_reconciliation.py`` — package generation, approval,
    certification, transmission, and the per-module official-run mints — because
    those surfaces hold a bank and a period but not this control's inputs.
    ``derive_facts`` enforces the same verdict at the fact plane.

    This function is deliberately pure of side effects (no audit row, no
    commit): it is called from inside caller transactions that already hold
    pending writes, and committing there would commit half a filing act. See
    ``filing_reconciliation``'s module docstring for what evidences a refusal.
    """
    result = check_balance_identity(db, ctx, bank, as_of, assets, funding)
    if result.blocks_filing:
        raise FilingBlockedError(balance_identity_detail(bank, as_of, result), result.provenance())
    return result
