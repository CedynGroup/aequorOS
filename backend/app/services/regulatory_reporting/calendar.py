"""Reporting-obligation calendar (docs/regulatory_reporting.md §5, ``calendar.py``).

For every registry entry: the currently-due reporting date plus the upcoming
reporting dates inside the horizon, each with its deadline-rule due date, the
current non-superseded solo package covering it, and a RAG grade —
``overdue`` (deadline passed without a submitted/acknowledged package),
``due_soon`` (deadline within the warning window), else ``on_track``.

Downtime semantics (BoG Notice BG/FMD/2026/07): a package submitted via the
email fallback is NOT complete until re-uploaded through ORASS, so a
``submitted`` package with ``pending_orass_reupload`` still set does not
satisfy its obligation for RAG purposes.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    Bank,
    RegulatoryPackage,
    RegulatoryReportingSettings,
    RegulatorySubmissionEvent,
)
from app.schemas.regulatory_reporting import (
    ObligationAnnexRead,
    ReportingObligationListRead,
    ReportingObligationRead,
    ReportingObligationSummaryRead,
    ReturnAnchorListRead,
    ReturnAnchorRead,
)
from app.services.regulatory_reporting import family_access
from app.services.regulatory_reporting.anchors import (
    DEFAULT_HORIZON_MONTHS,
    DEFAULT_LOOKBACK_MONTHS,
    anchor_dates,
    anchor_window,
    snapshot_coverage,
)
from app.services.regulatory_reporting.common import get_bank_or_404
from app.services.regulatory_reporting.eligibility import (
    BLOCKING_CRITERIA,
    resolve_eligibility,
)
from app.services.regulatory_reporting.registry import (
    REGISTRY,
    ReturnDefinition,
    get_definition,
    monthly_day,
)

DUE_SOON_DAYS = 7
_COMPLETED_STATUSES = ("submitted", "acknowledged")

type _PackageKey = tuple[str, date]


@dataclass(frozen=True)
class _PackageSummary:
    """Package state needed to build a calendar row."""

    id: UUID
    status: str
    version: int


def _calendar_package_state(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    schedule: dict[str, list[date]],
) -> tuple[dict[_PackageKey, _PackageSummary], set[UUID]]:
    """Return package state without queries growing with the calendar horizon.

    Results stay within the caller's organization. Only current, NON-REHEARSAL
    solo packages can satisfy calendar obligations, and a submitted package
    remains pending when its latest submitted event requires ORASS re-upload.
    """
    scheduled_keys = {
        (return_code, reporting_date)
        for return_code, reporting_dates in schedule.items()
        for reporting_date in reporting_dates
    }
    if not scheduled_keys:
        return {}, set()

    reporting_dates = [key[1] for key in scheduled_keys]
    rows = db.execute(
        select(
            RegulatoryPackage.id,
            RegulatoryPackage.return_code,
            RegulatoryPackage.reporting_date,
            RegulatoryPackage.status,
            RegulatoryPackage.version,
        ).where(
            RegulatoryPackage.organization_id == ctx.organization_id,
            RegulatoryPackage.bank_id == bank_id,
            RegulatoryPackage.return_code.in_(tuple(schedule)),
            RegulatoryPackage.reporting_date >= min(reporting_dates),
            RegulatoryPackage.reporting_date <= max(reporting_dates),
            # Calendar obligations use the solo basis. Consolidated package
            # chains are separate and cannot satisfy them.
            RegulatoryPackage.basis == "solo",
            RegulatoryPackage.status != "superseded",
            # A REHEARSAL never satisfies an obligation (D-029 / D-068). A dry
            # run that turned a row green would be worse than no dry run: the
            # bank would believe it had filed.
            RegulatoryPackage.is_rehearsal.is_(False),
        )
    ).all()
    packages = {
        (row.return_code, row.reporting_date): _PackageSummary(
            id=row.id,
            status=row.status,
            version=row.version,
        )
        for row in rows
        if (row.return_code, row.reporting_date) in scheduled_keys
    }

    submitted_ids = [package.id for package in packages.values() if package.status == "submitted"]
    if not submitted_ids:
        return packages, set()

    pending_by_package: dict[UUID, bool] = {}
    event_rows = db.execute(
        select(
            RegulatorySubmissionEvent.package_id,
            RegulatorySubmissionEvent.detail,
        )
        .where(
            RegulatorySubmissionEvent.organization_id == ctx.organization_id,
            RegulatorySubmissionEvent.package_id.in_(submitted_ids),
            RegulatorySubmissionEvent.event == "submitted",
        )
        .order_by(
            RegulatorySubmissionEvent.package_id,
            RegulatorySubmissionEvent.occurred_at,
            RegulatorySubmissionEvent.id,
        )
    ).all()
    for row in event_rows:
        pending_by_package[row.package_id] = bool(row.detail.get("pending_orass_reupload"))
    return packages, {
        package_id for package_id, is_pending in pending_by_package.items() if is_pending
    }


def _rag(
    due_date: date | None,
    as_of: date,
    package_status: str | None,
    *,
    pending_orass_reupload: bool = False,
) -> str:
    if package_status in _COMPLETED_STATUSES and not pending_orass_reupload:
        return "on_track"
    if due_date is None:
        # No configured deadline means no deadline has passed. A missing
        # governed value must never read as a compliance breach.
        return "on_track"
    if as_of > due_date:
        return "overdue"
    if (due_date - as_of).days <= DUE_SOON_DAYS:
        return "due_soon"
    return "on_track"


def _deadline_overrides(db: Session, ctx: TenantContext, bank_id: str) -> dict[str, int]:
    """The per-bank ``{return_code: day_of_month}`` deadline overrides, or {}."""
    settings = db.scalar(
        select(RegulatoryReportingSettings).where(
            RegulatoryReportingSettings.organization_id == ctx.organization_id,
            RegulatoryReportingSettings.bank_id == bank_id,
        )
    )
    if settings is None:
        return {}
    return {
        str(code): int(day)
        for code, day in settings.deadline_overrides.items()
        if isinstance(day, int)
    }


@dataclass(frozen=True)
class _GovernedDeadlines:
    """The governed month counts a bank's deadlines resolve through.

    Pre-resolved once per request, because the alternative is one parameter
    lookup per (return x anchor) and the calendar has tens of those. A code
    present with ``None`` is DECLARED BUT NOT CONFIGURED, which omits the due
    date and says so — it is never replaced by a default.
    """

    months: dict[str, int | None]

    def resolve(self, definition: ReturnDefinition) -> tuple[int | None, str | None]:
        code = definition.deadline_parameter
        if not code:
            return None, None
        value = self.months.get(code)
        return (value, None) if value is not None else (None, code)


def _governed_deadlines(
    db: Session, bank: Bank, *, as_of: date, resolver: Any | None = None
) -> _GovernedDeadlines:
    """Every governed deadline this institution's calendar needs, in ONE load.

    Through :class:`PrefetchedParameterResolver` rather than a ``try_resolve``
    per code: the calendar's query budget is FIXED and must not grow with the
    registry (``test_regulatory_reporting_calendar_query_shape``). The resolver
    resolves the policy scope once and loads every overlapping generation in one
    query, applying the same licence-before-class precedence as the per-code
    path — but ``record=False``, because the calendar is the DISPATCH plane: it
    reads every family's ``deadline_parameter`` and seals no run, so its reads
    are not any run's governed-row provenance (see ``regulatory_parameters``
    ``_CONSUMED``). The resolver handed in by ``InstitutionEligibility`` is
    already non-recording for the same reason.
    """
    from app.services import regulatory_parameters  # noqa: PLC0415 - avoid an import cycle

    codes = sorted(
        {
            definition.deadline_parameter
            for definition in REGISTRY.values()
            if definition.deadline_parameter
        }
    )
    if not codes:
        return _GovernedDeadlines(months={})
    if resolver is None:
        resolver = regulatory_parameters.PrefetchedParameterResolver.load(
            db, bank, as_of_dates=(as_of,), record=False
        )
    months: dict[str, int | None] = {}
    for code in codes:
        row = resolver.try_resolve(code, as_of=as_of)
        value = None if row is None or row.value is None else int(row.value)
        months[code] = value if value is not None and value >= 0 else None
    return _GovernedDeadlines(months=months)


def _months_after(reporting_date: date, months: int) -> date:
    """``months`` after the reporting date, clamped to that month's last day."""
    total = reporting_date.year * 12 + (reporting_date.month - 1) + months
    year, month = total // 12, total % 12 + 1
    return date(year, month, min(reporting_date.day, monthrange(year, month)[1]))


def _due_date(
    definition: ReturnDefinition,
    reporting_date: date,
    overrides: dict[str, int],
    governed: _GovernedDeadlines,
) -> tuple[date | None, str | None]:
    """``(due_date, missing_parameter)`` for one anchor.

    Three sources, in this order:

    1. a per-bank monthly-day override (how the BSD2 day-14 / FX-NOP day-10
       placeholders get corrected at onboarding once ORASS confirms the day);
    2. a GOVERNED deadline — "this many months after the reporting date",
       resolved from the control plane (founder directive D-024). The
       definition's own ``deadline_rule`` is a guard that RAISES for these, so
       reaching it would be a 500 on a tenant-wide surface;
    3. the registry's deadline rule.

    An unconfigured governed deadline returns ``(None, code)``. It must NEVER
    fall back to the rule: the platform does not hold a filing deadline BoG
    sets, and inventing one is worse than not showing one.
    """
    override_day = overrides.get(definition.code)
    if override_day is not None:
        return monthly_day(override_day)(reporting_date), None
    if definition.deadline_parameter:
        months, missing = governed.resolve(definition)
        if months is None:
            return None, missing
        return _months_after(reporting_date, months), None
    return definition.deadline_rule(reporting_date), None


def _parent_in_force(eligibility, parent: ReturnDefinition, reporting_date: date) -> bool:
    """Is the parent return in force on this date, so the annex rides inside it?"""
    effective = eligibility.effective_from(parent)
    return effective is not None and reporting_date >= effective


def _coverage_note(
    base: str | None, deadline_gaps: set[str], effective_gaps: set[str] | None = None
) -> str | None:
    """Why the calendar is short, when it is — never silence.

    ``base`` is the eligibility authority's own sentence, resolved by the
    caller: ``eligibility.coverage_note()`` is called from ``list_obligations``
    itself and that call site is PINNED
    (``test_reconciliation_control._CONTROL_CALL_SITES``), because the control
    it guards is one that was once complete, tested and reachable from nothing.
    """
    parts: list[str] = []
    if base:
        parts.append(base)
    if effective_gaps:
        codes = ", ".join(sorted(effective_gaps))
        parts.append(
            "Some returns are not listed because the date they come into force is a "
            f"governed value that has not been configured for this institution ({codes}). "
            "A commencement date is never assumed."
        )
    if deadline_gaps:
        codes = ", ".join(sorted(deadline_gaps))
        parts.append(
            "Some returns are not listed because their filing deadline is a governed "
            f"value that has not been configured for this institution ({codes}). The "
            "deadline is never assumed."
        )
    return " ".join(parts) if parts else None


def list_obligations(  # noqa: PLR0913 - tenant scope + window bounds + page controls
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    horizon_months: int = DEFAULT_HORIZON_MONTHS,
    *,
    lookback_months: int = DEFAULT_LOOKBACK_MONTHS,
    as_of: date | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> ReportingObligationListRead:
    bank = get_bank_or_404(db, ctx, bank_id)
    today = as_of or date.today()
    window = anchor_window(today, lookback_months=lookback_months, horizon_months=horizon_months)
    overrides = _deadline_overrides(db, ctx, bank.id)
    # A gated family's PACKAGE is itself disclosure: `family_access.can_view`
    # asks "may this principal know that packages of this family exist?". The
    # package LIST already excludes them; the calendar did not, so a scalar
    # analyst holding no binding could read an ICAAP package's id and status
    # here and then drive it through the attestation routes (security audit
    # S-2/S-3). The OBLIGATION row stays — BoG's deadline is public — but the
    # package linkage is withheld.
    hidden = family_access.hidden_families(db, ctx, bank)
    # Return eligibility resolves through the SINGLE authority (audit ARCH-8,
    # ``eligibility.py``) — the same object ``generation.generate_package``
    # gates on, so the calendar and the package-mint site cannot disagree about
    # what this institution may file. SDI scoping (docs/sdi.md §6.2) is one of
    # its dimensions: every return registered so far is a bank/BoG return, so a
    # savings-&-loans tenant resolves to an empty calendar until the SDI/ORASS
    # return pack lands. That is BoG's deferral, not a bug, and it is stated in
    # words on ``ReportingObligationListRead.coverage_note`` below.
    eligibility = resolve_eligibility(db, ctx, bank, as_of=today)

    obligations: list[ReportingObligationRead] = []
    # Anchors come from the registry through the SINGLE authority the Returns
    # workspace also consumes (``anchors.anchor_dates``), so the calendar and
    # the generate screen cannot offer different reporting dates — the same
    # one-authority rule eligibility follows. Event-driven returns yield no
    # anchors there; their packages still appear in the package list/history.
    governed = _governed_deadlines(db, bank, as_of=today, resolver=eligibility.parameter_resolver)
    schedule = {
        definition.code: anchor_dates(definition, window)
        for definition in eligibility.eligible_definitions()
    }
    # Which returns are filed INSIDE another one's submission (D-011), and from
    # which date. Before the parent is in force the annex keeps its own row, so
    # the pre-commencement dry runs are untouched.
    annex_parents = {
        definition.code: definition.annex_of
        for definition in eligibility.eligible_definitions()
        if definition.filing_role in ("annex", "companion") and definition.annex_of
    }
    # Whether the bank has a computed position AS OF each anchor, resolved in ONE
    # pass across every return rather than per definition (33 returns for a
    # universal bank). An anchor with no snapshot is still a real obligation —
    # it is BoG's date, not ours — so it is listed and marked, never hidden.
    coverage = snapshot_coverage(
        db, ctx, bank, sorted({date_ for dates in schedule.values() for date_ in dates})
    )
    packages, pending_reuploads = _calendar_package_state(db, ctx, bank.id, schedule)
    deadline_gaps: set[str] = set()
    nested: dict[tuple[str, date], list[ObligationAnnexRead]] = {}
    effective_gaps: set[str] = set()
    for definition in eligibility.eligible_definitions():
        effective_from = eligibility.effective_from(definition)
        missing_effective = eligibility.missing_effective_parameter(definition)
        if missing_effective is not None:
            # The date this return comes into force is a governed value nobody
            # has configured. FAIL CLOSED: a return whose commencement is
            # unknown cannot be presented as an obligation with a running
            # deadline. The note names the parameter, so the gap is fixable
            # rather than invisible.
            effective_gaps.add(missing_effective)
            continue
        parent_code = annex_parents.get(definition.code)
        for reporting_date in schedule[definition.code]:
            # An obligation exists only where the return is IN FORCE on the
            # reporting date. Listing a pre-commencement anchor as an obligation
            # would assert a filing duty the regulator has not imposed, and its
            # deadline would already have passed - every bank would open the
            # calendar to an "overdue" return nobody owes. The date itself stays
            # selectable in the Returns workspace (``list_return_anchors``), so
            # a dry run before the first live filing is unaffected. D-058.
            if effective_from is not None and reporting_date < effective_from:
                continue
            parent_definition = get_definition(parent_code) if parent_code else None
            package = packages.get((definition.code, reporting_date))
            if definition.family in hidden:
                package = None
            if parent_definition is not None and _parent_in_force(
                eligibility, parent_definition, reporting_date
            ):
                nested.setdefault((parent_definition.code, reporting_date), []).append(
                    ObligationAnnexRead(
                        return_code=definition.code,
                        title=definition.title,
                        filing_role=definition.filing_role,  # type: ignore[arg-type]
                        package_id=package.id if package is not None else None,
                        package_status=(
                            package.status  # type: ignore[arg-type]
                            if package is not None
                            else None
                        ),
                        package_version=package.version if package is not None else None,
                    )
                )
                continue
            due_date, missing_parameter = _due_date(definition, reporting_date, overrides, governed)
            if missing_parameter is not None:
                # The deadline is a governed value nobody has configured. The
                # obligation is omitted rather than shown with an invented due
                # date, and the note below names the parameter so the gap is
                # actionable instead of invisible.
                deadline_gaps.add(missing_parameter)
                continue
            assert due_date is not None  # noqa: S101 - narrowed by the branch above
            pending_reupload = package is not None and package.id in pending_reuploads
            obligations.append(
                ReportingObligationRead(
                    return_code=definition.code,
                    return_family=definition.family,
                    title=definition.title,
                    frequency=definition.frequency,
                    fidelity=definition.fidelity,
                    default_channel=definition.default_channel,
                    reporting_date=reporting_date,
                    due_date=due_date,
                    due_time=definition.due_time,
                    basis="solo",
                    package_id=package.id if package is not None else None,
                    package_status=(
                        package.status if package is not None else None  # type: ignore[arg-type]
                    ),
                    package_version=package.version if package is not None else None,
                    data_status=(
                        "computed" if coverage[reporting_date].covered else "awaiting_data"
                    ),
                    rag=_rag(  # type: ignore[arg-type]
                        due_date,
                        today,
                        package.status if package is not None else None,
                        pending_orass_reupload=pending_reupload,
                    ),
                )
            )
    for obligation in obligations:
        obligation.annexes = nested.get((obligation.return_code, obligation.reporting_date), [])
    obligations.sort(key=lambda item: (item.due_date or date.max, item.return_code))
    summary_counts = {
        "overdue": 0,
        "due_soon": 0,
        "on_track": 0,
        "pending_reupload": 0,
    }
    for obligation in obligations:
        summary_counts[obligation.rag] += 1
        if obligation.package_status == "submitted" and obligation.rag != "on_track":
            summary_counts["pending_reupload"] += 1

    total = len(obligations)
    page_limit = total if limit is None else limit
    page = obligations[offset:] if limit is None else obligations[offset : offset + limit]
    return ReportingObligationListRead(
        bank_id=bank.id,
        as_of=today,
        horizon_months=horizon_months,
        lookback_months=lookback_months,
        obligations=page,
        summary=ReportingObligationSummaryRead(
            overdue=summary_counts["overdue"],
            due_soon=summary_counts["due_soon"],
            on_track=summary_counts["on_track"],
            pending_reupload=summary_counts["pending_reupload"],
        ),
        total=total,
        limit=page_limit,
        offset=offset,
        has_more=offset + len(page) < total,
        # The note the eligibility authority has always been able to write, now
        # carried on the payload (audit 2026-08-22 D-20). It is None whenever the
        # institution has an eligible return set, so this adds a sentence exactly
        # where a reader would otherwise see an unexplained empty calendar.
        coverage_note=_coverage_note(eligibility.coverage_note(), deadline_gaps, effective_gaps),
    )


def list_return_anchors(  # noqa: PLR0913 - tenant + return + window bounds + clock
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    return_code: str,
    horizon_months: int = DEFAULT_HORIZON_MONTHS,
    *,
    lookback_months: int = DEFAULT_LOOKBACK_MONTHS,
    as_of: date | None = None,
) -> ReturnAnchorListRead:
    """The reporting dates ONE return reports on, with data + package state.

    This is what the Returns workspace selects a reporting date from. It used to
    select from ``bank_reporting_periods`` — the snapshots ingestion happens to
    have produced — which made a BoG deadline invisible whenever the bank had
    not yet ingested a book for it, and made the weekly returns effectively
    unfileable (``anchors`` module docstring has the measurement).

    An anchor with ``data_status='awaiting_data'`` is still listed. That is the
    point: the obligation is BoG's and its deadline runs regardless, so the
    honest surface shows the date and says nothing has been computed for it —
    it does not omit the date and it does not silently offer an earlier book.

    ``lookback_months`` reaches BACK over elapsed anchors for the same reason:
    an overdue return is the one the bank still owes. It is the mirror of
    ``horizon_months`` and both resolve through the one window the calendar
    uses (``anchors.anchor_window``), so the two surfaces cannot disagree.
    """
    bank = get_bank_or_404(db, ctx, bank_id)
    today = as_of or date.today()
    definition = get_definition(return_code)
    if definition is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Return {return_code!r} is not registered.",
        )

    eligibility = resolve_eligibility(db, ctx, bank, as_of=today)
    decision = eligibility.decide(definition, reporting_date=today)
    effective_from = eligibility.effective_from(definition)
    # A return that is not in force YET is not the same as a return this
    # institution may never file. The picker keeps its dates and marks them,
    # because the registry's own rule is that a commencement date is not a
    # generation gate: a bank prepares and dry-runs before its first live
    # filing. Every OTHER blocking dimension still empties the list. D-058 /
    # audit C-3 (an unexplained empty anchor list reads as a broken product).
    hard_failures = tuple(
        criterion.detail
        for criterion in decision.criteria
        if criterion.code in BLOCKING_CRITERIA
        and criterion.code != "effective_date"
        and not criterion.satisfied
    )
    if hard_failures:
        return ReturnAnchorListRead(
            bank_id=bank.id,
            return_code=definition.code,
            frequency=definition.frequency,
            as_of=today,
            horizon_months=horizon_months,
            lookback_months=lookback_months,
            anchors=[],
            ineligible_reason=" ".join(hard_failures),
            effective_from=effective_from,
        )

    window = anchor_window(today, lookback_months=lookback_months, horizon_months=horizon_months)
    reporting_dates = anchor_dates(definition, window)
    coverage = snapshot_coverage(db, ctx, bank, reporting_dates)
    overrides = _deadline_overrides(db, ctx, bank.id)
    governed = _governed_deadlines(db, bank, as_of=today, resolver=eligibility.parameter_resolver)
    packages, pending_reuploads = _calendar_package_state(
        db, ctx, bank.id, {definition.code: reporting_dates}
    )

    anchors: list[ReturnAnchorRead] = []
    deadline_note: str | None = None
    for reporting_date in reporting_dates:
        due_date, missing_parameter = _due_date(definition, reporting_date, overrides, governed)
        if missing_parameter is not None:
            deadline_note = (
                "The filing deadline for this return is a governed value that has not "
                f"been configured for this institution ({missing_parameter}). The "
                "reporting dates below are the regulator's; the due date is not assumed."
            )
        package = packages.get((definition.code, reporting_date))
        pending_reupload = package is not None and package.id in pending_reuploads
        covered = coverage[reporting_date]
        anchors.append(
            ReturnAnchorRead(
                reporting_date=reporting_date,
                due_date=due_date,
                due_time=definition.due_time,
                data_status="computed" if covered.covered else "awaiting_data",
                nearest_computed_before=None if covered.covered else covered.nearest_before,
                package_id=package.id if package is not None else None,
                package_status=(
                    package.status if package is not None else None  # type: ignore[arg-type]
                ),
                package_version=package.version if package is not None else None,
                rag=_rag(  # type: ignore[arg-type]
                    due_date,
                    today,
                    package.status if package is not None else None,
                    pending_orass_reupload=pending_reupload,
                ),
                in_force=effective_from is None or reporting_date >= effective_from,
            )
        )
    anchors.sort(key=lambda item: item.reporting_date, reverse=True)
    return ReturnAnchorListRead(
        bank_id=bank.id,
        return_code=definition.code,
        frequency=definition.frequency,
        as_of=today,
        horizon_months=horizon_months,
        lookback_months=lookback_months,
        anchors=anchors,
        effective_from=effective_from,
        deadline_note=deadline_note,
    )
