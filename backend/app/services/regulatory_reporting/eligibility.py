"""Return eligibility — the single authority for "may this institution file this?".

The 2026-08-21 forensic architecture audit (ARCH-8) found the question answered
twice, in two places, on different criteria: the reporting calendar filtered
obligations by the tenant's institution class, and the package-mint site
repeated a similar check inline. Two implementations of one rule is exactly the
shape of defect the audit's headline finding is about, and it means "the
calendar says you have nothing to file" and "the API let you file it" could
disagree.

This module is that rule, once. :func:`resolve_eligibility` returns an
:class:`InstitutionEligibility` for one institution at one date;
``calendar.list_obligations`` builds its obligation list from
:meth:`InstitutionEligibility.eligible_definitions`, and
``generation.generate_package`` gates on
:meth:`InstitutionEligibility.require` before a package row can be minted. They
cannot drift, because there is only one decision function.

The dimensions
--------------

Every dimension the audit named is evaluated and *recorded*, including the ones
the repository cannot yet establish — an unestablished dimension is reported as
such, never silently treated as satisfied by omission:

``registered``          the return code exists in the registry
``institution_class``   'bank' | 'sdi', resolved fail-closed from the licence
``jurisdiction``        the bank's ``jurisdiction_code`` is in scope
``regulator``           the return's regulator is the bank's own supervisor
``frequency``           the reporting date is a valid anchor for the cadence
``effective_date``      the return is in force on the reporting date

``prerequisites`` and ``required_data`` ride on the decision as declared
metadata. They are **not** eligibility gates: whether a baseline run exists, or
whether a sub-ledger register has been ingested, is enforced where it can be
answered honestly (the generators' 409s), and duplicating that here would be a
second implementation of a rule — the very thing this module exists to end.

SDI return coverage is deliberately narrow
------------------------------------------

The registry contains only SDI packets whose public BoG appendix structures are
established. A customer-specific ORASS form is still absent until its own
regulator-issued template is registered; the eligibility layer must never widen
the SDI set by treating a bank/BSD form as a substitute.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank
from app.services.institution_types import institution_class as resolve_institution_class
from app.services.jurisdictions import get_jurisdiction, regulator_short
from app.services.jurisdictions import jurisdiction_code as resolve_jurisdiction_code
from app.services.regulatory_reporting.registry import REGISTRY, ReturnDefinition

__all__ = [
    "CRITERIA",
    "EligibilityCriterion",
    "EligibilityDecision",
    "InstitutionEligibility",
    "NOT_ESTABLISHED",
    "resolve_eligibility",
]

#: Recorded on a dimension the repository cannot establish. It is deliberately
#: *not* a silent pass: the decision says the dimension is unrestricted and why,
#: so a future effective-dating or multi-jurisdiction pass has a named seam.
NOT_ESTABLISHED = "not_established_in_registry"

#: The evaluated dimensions, in report order.
CRITERIA: tuple[str, ...] = (
    "registered",
    "institution_class",
    "jurisdiction",
    "regulator",
    "frequency",
    "effective_date",
)

#: The dimensions that decide whether the institution MAY file the return at
#: all. Failing one is a refusal.
BLOCKING_CRITERIA: frozenset[str] = frozenset(
    {"registered", "institution_class", "jurisdiction", "regulator", "effective_date"}
)

#: Advisory dimensions: recorded on every decision, but they do not refuse.
#:
#: ``frequency`` is advisory on purpose. A return's cadence establishes WHEN an
#: obligation arises — which is why the calendar enumerates anchors and mints
#: obligations only on them — not WHETHER the institution is subject to the
#: return. Banks legitimately generate off-anchor: a dry run before the first
#: live filing, or a re-generation against a corrected period. Refusing those
#: would be a new restriction dressed up as a correctness fix.
#:
#: A third case used to be listed here — "daily returns which by design draw on
#: the latest effective period rather than a period end" — and it is gone
#: (2026-08-23). That fallback was removed: every cadence now resolves its
#: figures EXACTLY as of the reporting date
#: (``common.get_snapshot_for_reporting_date``), because a daily return built
#: from last month's book is not that day's position. Off-anchor generation
#: stays permitted; borrowing another date's figures does not.
ADVISORY_CRITERIA: frozenset[str] = frozenset(CRITERIA) - BLOCKING_CRITERIA

_FREQUENCY_MONTHS: dict[str, int] = {"monthly": 1, "quarterly": 3, "semiannual": 6, "annual": 12}


@dataclass(frozen=True)
class EligibilityCriterion:
    """One evaluated dimension of the eligibility decision."""

    code: str
    satisfied: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "satisfied": self.satisfied, "detail": self.detail}


@dataclass(frozen=True)
class EligibilityDecision:
    """Whether one institution may file one return, and on exactly what basis."""

    return_code: str
    institution_class: str
    jurisdiction_code: str
    as_of: date
    criteria: tuple[EligibilityCriterion, ...]
    prerequisites: tuple[str, ...] = ()
    required_data: tuple[str, ...] = ()

    @property
    def eligible(self) -> bool:
        """True when every BLOCKING dimension is satisfied.

        Advisory dimensions (:data:`ADVISORY_CRITERIA`) are recorded but never
        refuse — see the constant for why cadence is one of them.
        """
        return all(c.satisfied for c in self.criteria if c.code in BLOCKING_CRITERIA)

    @property
    def blocking_reasons(self) -> tuple[str, ...]:
        return tuple(
            c.detail for c in self.criteria if c.code in BLOCKING_CRITERIA and not c.satisfied
        )

    @property
    def advisories(self) -> tuple[str, ...]:
        """Unsatisfied advisory dimensions — surfaced, never enforced."""
        return tuple(
            c.detail for c in self.criteria if c.code in ADVISORY_CRITERIA and not c.satisfied
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "return_code": self.return_code,
            "eligible": self.eligible,
            "institution_class": self.institution_class,
            "jurisdiction_code": self.jurisdiction_code,
            "as_of": self.as_of.isoformat(),
            "criteria": [criterion.to_dict() for criterion in self.criteria],
            "blocking_reasons": list(self.blocking_reasons),
            "advisories": list(self.advisories),
            "prerequisites": list(self.prerequisites),
            "required_data": list(self.required_data),
        }


def _frequency_criterion(
    definition: ReturnDefinition, reporting_date: date
) -> EligibilityCriterion:
    """Is ``reporting_date`` a valid anchor for this return's cadence?

    Daily returns anchor on any business day, weekly returns on the catalogue's
    weekly anchor weekday, periodic returns on a period end. Event-driven packs
    (the LRT corporate family) have no cycle at all and anchor on any date —
    expanding their nominal frequency would fabricate obligations that do not
    exist, which is why the calendar skips them.
    """
    if definition.event_driven:
        return EligibilityCriterion(
            "frequency",
            True,
            "Event-driven return: it is filed because a corporate event happened, so any "
            "reporting date is a valid anchor and no periodic obligation is minted.",
        )
    frequency = definition.frequency
    if frequency == "daily":
        weekday_ok = reporting_date.weekday() < 5  # noqa: PLR2004 — Mon..Fri
        return EligibilityCriterion(
            "frequency",
            weekday_ok,
            (
                f"{reporting_date.isoformat()} is a business day, the anchor for a daily return."
                if weekday_ok
                else f"{reporting_date.isoformat()} is a weekend; a daily return anchors on a "
                "business day."
            ),
        )
    if frequency == "weekly":
        from app.services.regulatory_reporting.bog_forms.catalog import (  # noqa: PLC0415
            WEEKLY_ANCHOR_WEEKDAY,
        )

        anchored = reporting_date.weekday() == WEEKLY_ANCHOR_WEEKDAY
        return EligibilityCriterion(
            "frequency",
            anchored,
            (
                "Weekly return anchored on the documented weekly close "
                f"({reporting_date.isoformat()})."
                if anchored
                else "Weekly returns anchor on the documented weekly close; "
                f"{reporting_date.isoformat()} is not that weekday."
            ),
        )
    step = _FREQUENCY_MONTHS.get(frequency)
    if step is None:  # pragma: no cover - every registered frequency is covered
        return EligibilityCriterion(
            "frequency", True, f"Frequency '{frequency}' declares no anchor rule."
        )
    from calendar import monthrange  # noqa: PLC0415

    is_period_end = reporting_date.day == monthrange(reporting_date.year, reporting_date.month)[1]
    on_cycle = is_period_end and reporting_date.month % step == 0
    return EligibilityCriterion(
        "frequency",
        on_cycle,
        (
            f"{reporting_date.isoformat()} is a {frequency} period end."
            if on_cycle
            else f"{reporting_date.isoformat()} is not a {frequency} period end; this return "
            "reports on the period-end date."
        ),
    )


@dataclass(frozen=True)
class InstitutionEligibility:
    """The resolved eligibility context for one institution at one date.

    Built once per request. Institution class is resolved fail-closed
    (``institution_types.get_type`` raises on an unregistered licence rather
    than substituting the bank regime — P0-12), so an unresolvable licence can
    never fall through to "bank".
    """

    bank_id: str
    institution_class: str
    jurisdiction_code: str
    #: The institution's supervisor, or ``None`` when the jurisdictions registry
    #: has no row for its jurisdiction. ``None`` means *not established* — the
    #: dimension is reported unrestricted with :data:`NOT_ESTABLISHED` rather
    #: than compared against a display fallback string ("Regulator"), which
    #: would make every return ineligible for a mis-registered jurisdiction.
    regulator: str | None
    as_of: date

    # -- the single decision function -------------------------------------

    def decide(
        self, definition: ReturnDefinition, *, reporting_date: date | None = None
    ) -> EligibilityDecision:
        """Evaluate every dimension for one return. The only place this happens."""
        when = reporting_date or self.as_of
        registered = REGISTRY.get(definition.code) is definition
        class_ok = self.institution_class in definition.institution_classes
        jurisdictions = definition.jurisdictions
        jurisdiction_ok = not jurisdictions or self.jurisdiction_code in jurisdictions
        regulator_ok = _regulator_matches(definition.regulator, self.regulator)
        effective = definition.effective_from
        effective_ok = effective is None or when >= effective
        criteria = (
            EligibilityCriterion(
                "registered",
                registered,
                (
                    f"'{definition.code}' is a registered return."
                    if registered
                    else f"'{definition.code}' is not registered in the return registry."
                ),
            ),
            EligibilityCriterion(
                "institution_class",
                class_ok,
                (
                    f"Return applies to institution class '{self.institution_class}'."
                    if class_ok
                    else f"Return '{definition.code}' does not apply to this institution's class "
                    f"({self.institution_class}); it applies to "
                    f"{', '.join(definition.institution_classes)}."
                ),
            ),
            EligibilityCriterion(
                "jurisdiction",
                jurisdiction_ok,
                (
                    f"Return is in scope for jurisdiction '{self.jurisdiction_code}'."
                    if jurisdiction_ok
                    else f"Return '{definition.code}' applies in "
                    f"{', '.join(jurisdictions)}; this institution is licensed in "
                    f"{self.jurisdiction_code}."
                ),
            ),
            EligibilityCriterion(
                "regulator",
                regulator_ok,
                (
                    f"This institution's supervisor is not established: jurisdiction "
                    f"'{self.jurisdiction_code}' has no registry row ({NOT_ESTABLISHED}), so the "
                    "return's regulator is not compared."
                    if self.regulator is None
                    else (
                        f"Return is filed with {definition.regulator}, this institution's "
                        "supervisor."
                        if regulator_ok
                        else f"Return '{definition.code}' is filed with {definition.regulator}; "
                        f"this institution's supervisor is {self.regulator}."
                    )
                ),
            ),
            _frequency_criterion(definition, when),
            EligibilityCriterion(
                "effective_date",
                effective_ok,
                (
                    f"No effective date is established for this return in the registry "
                    f"({NOT_ESTABLISHED}); it is treated as in force."
                    if effective is None
                    else (
                        f"In force since {effective.isoformat()}."
                        if effective_ok
                        else f"Return '{definition.code}' takes effect on "
                        f"{effective.isoformat()}, after {when.isoformat()}."
                    )
                ),
            ),
        )
        return EligibilityDecision(
            return_code=definition.code,
            institution_class=self.institution_class,
            jurisdiction_code=self.jurisdiction_code,
            as_of=when,
            criteria=criteria,
            prerequisites=definition.prerequisites,
            required_data=definition.required_data,
        )

    # -- the two consumers -------------------------------------------------

    def is_eligible(
        self, definition: ReturnDefinition, *, reporting_date: date | None = None
    ) -> bool:
        return self.decide(definition, reporting_date=reporting_date).eligible

    def eligible_definitions(
        self, *, candidates: Iterable[ReturnDefinition] | None = None
    ) -> tuple[ReturnDefinition, ...]:
        """Every return this institution is subject to, class/jurisdiction-wise.

        The calendar's source of truth. Reporting-date dimensions (frequency,
        effective date) are evaluated per candidate reporting date by
        :meth:`decide`, not here — the calendar enumerates the dates itself.
        """
        pool = candidates if candidates is not None else REGISTRY.values()
        return tuple(
            definition
            for definition in pool
            if self.institution_class in definition.institution_classes
            and (not definition.jurisdictions or self.jurisdiction_code in definition.jurisdictions)
            and _regulator_matches(definition.regulator, self.regulator)
        )

    def require(self, definition: ReturnDefinition, *, reporting_date: date) -> EligibilityDecision:
        """Gate a mutation. Raises 403 with every failed dimension named.

        This is what makes an ineligible return structurally impossible to
        generate: the package-mint site cannot reach the generator without
        passing through here.
        """
        decision = self.decide(definition, reporting_date=reporting_date)
        if decision.eligible:
            return decision
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": "return_not_eligible",
                "message": " ".join(decision.blocking_reasons),
                "decision": decision.to_dict(),
            },
        )

    # -- honest reporting of an empty set ---------------------------------

    def coverage_note(self) -> str | None:
        """Why this institution's eligible return set is empty, when it is.

        This remains relevant when a jurisdiction or regulator configuration
        leaves any institution class with no registered obligations.
        """
        if self.eligible_definitions():
            return None
        return (
            f"No registered return applies to institution class '{self.institution_class}' in "
            f"jurisdiction '{self.jurisdiction_code}' under supervisor '{self.regulator}'."
        )


def _regulator_matches(definition_regulator: str, institution_regulator: str | None) -> bool:
    """Compare supervisors tolerantly on form, strictly on identity.

    ``ReturnDefinition.regulator`` carries the registry code ("BOG"); the
    institution's supervisor is resolved from the jurisdictions registry as a
    display short form ("BoG"). Same regulator, different casing convention —
    so the comparison is case-insensitive. It is NOT a substring match: a
    genuinely different supervisor must fail.

    ``None`` (no registry row for the jurisdiction) means the supervisor is not
    established, and an unestablished dimension does not block — it is reported
    as :data:`NOT_ESTABLISHED` on the decision instead.
    """
    if institution_regulator is None:
        return True
    return definition_regulator.strip().casefold() == institution_regulator.strip().casefold()


def resolve_eligibility(
    db: Session, ctx: TenantContext, bank: Bank, *, as_of: date | None = None
) -> InstitutionEligibility:
    """Build the eligibility context for one institution. Fail-closed.

    ``ctx`` is accepted for signature symmetry with the rest of the reporting
    services (and so a future per-tenant eligibility override has a seam); the
    resolution itself reads the already tenant-scoped ``bank`` row.
    """
    _ = ctx
    jurisdiction_row = get_jurisdiction(db, bank)
    return InstitutionEligibility(
        bank_id=str(bank.id),
        institution_class=resolve_institution_class(db, bank),
        jurisdiction_code=resolve_jurisdiction_code(bank),
        regulator=(regulator_short(db, bank) if jurisdiction_row is not None else None),
        as_of=as_of or date.today(),
    )


def registry_class_coverage(
    *, definitions: Sequence[ReturnDefinition] | None = None
) -> dict[str, int]:
    """How many registered returns declare each institution class.

    The measurable form of the audit's ARCH-8 observation. Pinned by test so
    that the day an SDI return is registered, the coverage number moves and the
    deferral note stops being emitted.
    """
    pool = definitions if definitions is not None else tuple(REGISTRY.values())
    counts: dict[str, int] = {}
    for definition in pool:
        for klass in definition.institution_classes:
            counts[klass] = counts.get(klass, 0) + 1
    return counts
