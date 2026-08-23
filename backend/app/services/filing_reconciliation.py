"""The filing-plane wiring of the balance-sheet identity control (audit D-2, D-3).

``app/services/reconciliation.py`` built the control and
:func:`reconciliation.assert_filing_reconciled` — the gate a filing act is
supposed to pass — and the independent forensic re-audit of 2026-08-22 found
that gate had **zero production callers**. The control gated one cold-start code
path (``derive_facts``) and nothing downstream of it: packages, approvals,
certifications, transmissions and the two per-module official-run endpoints all
minted filing evidence without ever asking it. This module is the missing half.
It is deliberately the ONLY place that knows how to turn "a bank, a period, a
filing act" into the two totals the gate needs, so every filing surface asks the
same question in the same way and a future caller cannot invent its own.

Two questions, both asked
=========================

**1. Does the fact set this filing rests on satisfy the identity?**
An official ``RegulatoryRun``, and therefore every package and artifact built on
it, is computed from the period's ``BankFinancialFact`` balance sheet. Summing
that fact set's asset side against its liability + equity side asks the identity
of the exact numbers about to be filed. It is cheap, it needs no canonical data,
and it holds whatever produced the facts.

**2. Does the bank's book still balance NOW?**
Facts are a materialisation of a book at a moment. ``derive_facts`` refuses on a
broken book, so a fact set exists only because the book balanced *when it was
derived* — and a later ingestion at the same as-of (a duplicated source book, a
retired GL code, a missing journal entry) can break it afterwards while the
facts sit there, balanced and stale. That is audit finding D-3(a): the scheduled
official run short-circuited the derivation whenever facts already existed and
minted immutable runs with no verdict anywhere in the chain. Question 2 is
:func:`fact_derivation.evaluate_balance_identity` — read-only, no re-derivation.

Question 2 is asked only when the two are COMPARABLE — that is, when the
period's balance-sheet facts were themselves derived from the canonical book
(``attributes["source"] == "data_engine"``). A fact set that did not come from
the canonical plane has no canonical book to be re-checked against, and
comparing it to whatever positions happen to exist at that as-of would
manufacture a refusal out of an unrelated population. Where the canonical book
cannot answer, this module reports NOT ASSESSED rather than a pass.

Why this gate writes no audit event
===================================
``reconciliation.record_check`` COMMITS its own event when the verdict blocks,
because ``derive_facts`` runs it as the first act of a clean unit of work and
the refusal must survive the caller's rollback. The filing gate has no such
luxury: it runs inside caller transactions that already hold pending writes (an
approval row and its status transition, a certification's signature revision),
and committing there would commit half a filing act. The refusal is evidenced
instead by the ``FilingBlockedError`` itself — a 409 carrying the full control
provenance — by the ``reconciliation.failed`` structured log line emitted here,
and by the derivation-plane ``audit_events`` row that recorded the same verdict
when the facts were produced.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core import observability
from app.models import Bank, BankFinancialFact, RegulatoryPackage
from app.services import fact_derivation, reconciliation, withdrawal_impact

#: The fact group carrying the balance sheet, and the ``attributes["side"]``
#: values that place a line on each side of the identity. These are the
#: derivation's own conventions (``fact_derivation._derive_balance_sheet_block``)
#: read back, not a second opinion about what a balance sheet is.
BALANCE_SHEET_GROUP = "balance_sheet"
ASSET_SIDE = "asset"
FUNDING_SIDES = ("liability", "equity")

#: ``attributes["source"]`` on a fact the Data Engine derived from canonical
#: state. Only such a fact set has a canonical book behind it to re-check.
DATA_ENGINE_SOURCE = fact_derivation.SOURCE_TAG

_ZERO = Decimal("0")


@dataclass(frozen=True)
class FilingReconciliation:
    """What the control said about one filing act, on both planes it can see."""

    #: The identity over the period's official fact set. ``None`` when the
    #: period carries no balance-sheet facts at all.
    facts: reconciliation.BalanceIdentityOutcome | None
    #: The identity over the CURRENT canonical book at the same as-of. ``None``
    #: means NOT ASSESSED — no canonical book, or a fact set the canonical book
    #: did not produce — never "reconciled".
    book: reconciliation.BalanceIdentityOutcome | None

    @property
    def book_assessed(self) -> bool:
        return self.book is not None

    def provenance(self) -> dict[str, Any]:
        return {
            "facts": self.facts.provenance() if self.facts is not None else None,
            "book": self.book.provenance() if self.book is not None else "not_assessed",
        }


def period_balance_totals(
    db: Session, ctx: TenantContext, bank: Bank, period_id: UUID
) -> tuple[Decimal, Decimal] | None:
    """``(assets, liabilities + equity)`` as the period's FILED facts state them.

    ``None`` when the period carries no balance-sheet facts: there is no
    identity to test, which is a different answer from "it balances".
    """
    rows = db.execute(
        select(BankFinancialFact.amount, BankFinancialFact.attributes).where(
            BankFinancialFact.organization_id == ctx.organization_id,
            BankFinancialFact.bank_id == bank.id,
            BankFinancialFact.reporting_period_id == period_id,
            BankFinancialFact.fact_group == BALANCE_SHEET_GROUP,
        )
    ).all()
    if not rows:
        return None
    assets = _ZERO
    funding = _ZERO
    for amount, attributes in rows:
        side = (attributes or {}).get("side")
        if side == ASSET_SIDE:
            assets += amount
        elif side in FUNDING_SIDES:
            funding += amount
    return assets, funding


def period_facts_are_data_engine_derived(
    db: Session, ctx: TenantContext, bank: Bank, period_id: UUID
) -> bool:
    """Whether the period's balance sheet came from the canonical book.

    The canonical re-check is only meaningful against a fact set the canonical
    plane produced; anything else (a fixture, an import, a hand-built period)
    has no canonical book of its own and must not be measured against one.
    """
    sources = db.scalars(
        select(BankFinancialFact.attributes).where(
            BankFinancialFact.organization_id == ctx.organization_id,
            BankFinancialFact.bank_id == bank.id,
            BankFinancialFact.reporting_period_id == period_id,
            BankFinancialFact.fact_group == BALANCE_SHEET_GROUP,
        )
    )
    return any((attributes or {}).get("source") == DATA_ENGINE_SOURCE for attributes in sources)


def assert_filing_reconciled(  # noqa: PLR0913 - the gate names its full filing context
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    *,
    as_of: date,
    period_id: UUID,
    purpose: str,
) -> FilingReconciliation:
    """Refuse a filing-plane act whose book does not reconcile.

    ``purpose`` names the act being gated (``"package_generation"``,
    ``"package_approval"``, ``"package_certification"``, ``"package_submission"``,
    ``"official_run"``) and reaches the structured log line, so an operator can
    tell WHICH filing surface refused without reading the stack.

    Raises :class:`reconciliation.FilingBlockedError` — a 409 for an API caller
    and a ``NotComputable`` for a fail-closed boundary. Writes nothing.
    """
    facts_outcome: reconciliation.BalanceIdentityOutcome | None = None
    totals = period_balance_totals(db, ctx, bank, period_id)
    if totals is not None:
        assets, funding = totals
        facts_outcome = _gate(db, ctx, bank, as_of, assets, funding, purpose=purpose, plane="facts")

    book_outcome: reconciliation.BalanceIdentityOutcome | None = None
    if period_facts_are_data_engine_derived(db, ctx, bank, period_id):
        book_outcome = _book_identity(db, ctx, bank, as_of)
        if book_outcome is not None:
            book_outcome = _gate(
                db,
                ctx,
                bank,
                as_of,
                book_outcome.assets,
                book_outcome.funding,
                purpose=purpose,
                plane="canonical_book",
            )
    return FilingReconciliation(facts=facts_outcome, book=book_outcome)


def assert_package_reconciled(
    db: Session, ctx: TenantContext, package: RegulatoryPackage, *, purpose: str
) -> FilingReconciliation | None:
    """Gate a filing act on an ALREADY-MINTED package (approve, certify, submit).

    Mint-time is the structural gate — nothing can be filed that was not minted
    — but a book can break between mint and filing, which is exactly the window
    a monthly return spends waiting for its approver. This resolves the
    package's reporting period the same way ``generation`` did and re-asks the
    control.

    ``None`` means the period behind the package could not be resolved, so the
    control could not be evaluated. That is reported as an explicit
    ``not_assessed`` log line rather than treated as a pass — but it does not
    refuse the act, because a package whose period has vanished is a lookup
    problem, and mint-time already gated the figures it carries.

    Two dimensions of the one question this gate asks — *is this package still
    fit to file?* — are checked here. The balance identity below is one. The
    other (audit 2026-08-22 D-12) is whether the sealed runs the package binds
    were computed on canonical rows that have since been withdrawn under
    two-officer approval: a governed withdrawal can land in exactly the window
    a monthly return spends waiting for its approver. It is asked FIRST, and
    before the period lookup, so the early ``not_assessed`` return cannot skip
    it. It refuses the act and never touches the runs it judges.
    """
    withdrawal_impact.assert_package_source_runs_current(db, package, purpose=purpose)

    from app.services.regulatory_reporting.common import (  # noqa: PLC0415 - breaks an import cycle
        get_bank_or_404,
        get_effective_period_or_404,
        get_period_for_reporting_date_or_404,
    )
    from app.services.regulatory_reporting.registry import (  # noqa: PLC0415 - same cycle
        get_definition,
    )

    definition = get_definition(package.return_code)
    daily = definition is not None and definition.frequency == "daily"
    try:
        bank = get_bank_or_404(db, ctx, package.bank_id)
        period = (
            get_effective_period_or_404(db, ctx, bank, package.reporting_date)
            if daily
            else get_period_for_reporting_date_or_404(db, ctx, bank, package.reporting_date)
        )
    except HTTPException:
        observability.emit(
            observability.Condition.RECONCILIATION_FAILED,
            "Balance-sheet identity NOT ASSESSED for a filing act",
            severity="warning",
            purpose=purpose,
            plane="not_assessed",
            reason="reporting_period_unresolved",
            bank_id=package.bank_id,
            organization_id=ctx.organization_id,
            return_code=package.return_code,
            reporting_date=package.reporting_date.isoformat(),
        )
        return None
    return assert_filing_reconciled(
        db, ctx, bank, as_of=period.period_end, period_id=period.id, purpose=purpose
    )


#: Session-scoped memo for the canonical re-check. One filing act can gate
#: several times (a return sweep mints many packages in one unit of work), and
#: the re-check loads the tenant's whole position book — on a real tenant that
#: is six figures of rows. The book cannot change inside a read-only gate, and a
#: ``Session`` is one unit of work, so caching it there is safe and bounds the
#: cost to one load per (bank, as-of) per request.
_BOOK_IDENTITY_CACHE_KEY = "filing_reconciliation.book_identity"


def _book_identity(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> reconciliation.BalanceIdentityOutcome | None:
    cache = db.info.setdefault(_BOOK_IDENTITY_CACHE_KEY, {})
    key = (ctx.organization_id, bank.id, as_of)
    if key not in cache:
        cache[key] = fact_derivation.evaluate_balance_identity(db, ctx, bank, as_of)
    return cache[key]


def _gate(  # noqa: PLR0913 - the refusal record names its full context
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    as_of: date,
    assets: Decimal,
    funding: Decimal,
    *,
    purpose: str,
    plane: str,
) -> reconciliation.BalanceIdentityOutcome:
    """One call to the built gate, with the refusal made operationally visible."""
    try:
        return reconciliation.assert_filing_reconciled(db, ctx, bank, as_of, assets, funding)
    except reconciliation.FilingBlockedError as blocked:
        observability.emit(
            observability.Condition.RECONCILIATION_FAILED,
            "Filing refused: the balance-sheet identity does not hold",
            severity="error",
            purpose=purpose,
            plane=plane,
            bank_id=bank.id,
            organization_id=ctx.organization_id,
            as_of_date=as_of.isoformat(),
            gap=blocked.provenance.get("gap"),
            gap_fraction=blocked.provenance.get("gap_fraction"),
            tolerance=blocked.provenance.get("tolerance"),
        )
        raise
