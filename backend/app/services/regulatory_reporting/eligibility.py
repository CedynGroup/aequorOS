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

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
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


def _effective_date_detail(
    definition: ReturnDefinition,
    when: date,
    *,
    effective: date | None,
    effective_ok: bool,
    missing_parameter: str | None,
) -> str:
    """The sentence the ``effective_date`` dimension reports."""
    if missing_parameter is not None:
        return (
            f"missing_parameter {missing_parameter}: return '{definition.code}' takes effect on "
            "a date the regulator sets, which is a governed value that has not been configured "
            "for this institution. The date is never substituted or assumed."
        )
    if effective is None:
        return (
            f"No effective date is established for this return in the registry "
            f"({NOT_ESTABLISHED}); it is treated as in force."
        )
    if effective_ok:
        source = (
            f" (governed value '{definition.effective_from_parameter}')"
            if definition.effective_from is None and definition.effective_from_parameter
            else ""
        )
        return f"In force since {effective.isoformat()}{source}."
    return (
        f"Return '{definition.code}' takes effect on {effective.isoformat()}, "
        f"after {when.isoformat()}."
    )


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
    #: Governed effective dates, pre-resolved once per request for every
    #: definition that names an ``effective_from_parameter`` (founder directive
    #: D-024: a date BoG sets is a governed row, not a literal). A code present
    #: with a ``None`` value is DECLARED BUT UNRESOLVABLE, which fails closed;
    #: a code absent from the map was never resolved for this institution.
    governed_effective_dates: Mapping[str, date | None] = field(default_factory=dict)
    #: The request-scoped parameter resolver the governed dates above came from,
    #: carried so a caller that needs MORE governed values for the same
    #: institution and date (the calendar's deadlines) reuses one scope
    #: resolution and one row load instead of paying for a second.
    parameter_resolver: Any | None = None

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
        effective, missing_parameter = self._effective_date(definition)
        effective_ok = missing_parameter is None and (effective is None or when >= effective)
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
                _effective_date_detail(
                    definition,
                    when,
                    effective=effective,
                    effective_ok=effective_ok,
                    missing_parameter=missing_parameter,
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

    def _effective_date(
        self, definition: ReturnDefinition
    ) -> tuple[date | None, str | None]:
        """``(effective_from, missing_parameter_code)`` for one definition.

        The registry's own literal wins where it has one. Otherwise a named
        ``effective_from_parameter`` is read from the pre-resolved governed
        values — and an unresolvable one is reported as MISSING rather than
        substituted, because inventing the date a directive takes effect is
        exactly the class of number D-024 forbids the platform to hold.
        """
        if definition.effective_from is not None:
            return definition.effective_from, None
        code = definition.effective_from_parameter
        if not code:
            return None, None
        if code not in self.governed_effective_dates:
            return None, code
        resolved = self.governed_effective_dates[code]
        return (resolved, None) if resolved is not None else (None, code)

    def require(
        self,
        definition: ReturnDefinition,
        *,
        reporting_date: date,
        ignore: Collection[str] = (),
    ) -> EligibilityDecision:
        """Gate a mutation. Raises 403 with every failed dimension named.

        This is what makes an ineligible return structurally impossible to
        generate: the package-mint site cannot reach the generator without
        passing through here.

        ``ignore`` names blocking dimensions this CALLER does not gate on, and
        exists for exactly one caller: the ICAAP freeze
        (``generation.generate_frozen_package``) passes ``{"effective_date"}``
        so a bank can rehearse a filing before the return is in force. That is
        the registry's own stated rule — "blocking generation on [commencement
        dates] would stop a bank preparing and dry-running a return before its
        first live filing" — made explicit at the one site that needs it rather
        than weakened for everybody. The generic package-mint site passes
        nothing and is unchanged.
        """
        decision = self.decide(definition, reporting_date=reporting_date)
        ignored = frozenset(ignore)
        failures = tuple(
            criterion.detail
            for criterion in decision.criteria
            if criterion.code in BLOCKING_CRITERIA
            and criterion.code not in ignored
            and not criterion.satisfied
        )
        if not failures:
            return decision
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": "return_not_eligible",
                "message": " ".join(failures),
                "decision": decision.to_dict(),
            },
        )

    def effective_from(self, definition: ReturnDefinition) -> date | None:
        """The resolved first in-force date, or ``None`` when none is established.

        The calendar reads this to decide which anchors are real obligations.
        An unresolvable governed parameter answers ``None`` here and the
        ``effective_date`` criterion refuses separately, so a caller cannot
        mistake "not established" for "resolved as unrestricted".
        """
        effective, _missing = self._effective_date(definition)
        return effective

    def missing_effective_parameter(self, definition: ReturnDefinition) -> str | None:
        """The governed code this return's effective date needs and lacks."""
        _effective, missing = self._effective_date(definition)
        return missing

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
    when = as_of or date.today()
    resolver = parameter_resolver(db, bank, as_of=when)
    return InstitutionEligibility(
        bank_id=str(bank.id),
        institution_class=resolve_institution_class(db, bank),
        jurisdiction_code=resolve_jurisdiction_code(bank),
        regulator=(regulator_short(db, bank) if jurisdiction_row is not None else None),
        as_of=when,
        governed_effective_dates={
            code: _effective_date_value(resolver.try_resolve(code, as_of=when))
            for code in governed_effective_parameter_codes()
        },
        parameter_resolver=resolver,
    )


def governed_effective_parameter_codes(
    *, definitions: Iterable[ReturnDefinition] | None = None
) -> tuple[str, ...]:
    """Every distinct ``effective_from_parameter`` in the registry, sorted."""
    pool = definitions if definitions is not None else REGISTRY.values()
    return tuple(
        sorted({d.effective_from_parameter for d in pool if d.effective_from_parameter})
    )


def resolve_governed_effective_dates(
    db: Session, bank: Bank, *, as_of: date
) -> dict[str, date | None]:
    """Read every governed first-in-force date once, for this institution.

    One pass over the DISTINCT parameter codes rather than one lookup per
    definition. An unresolvable or malformed value maps to ``None``, which the
    decision reports as ``missing_parameter`` — it is never coerced to a date.
    """

    codes = governed_effective_parameter_codes()
    if not codes:
        return {}
    resolver = parameter_resolver(db, bank, as_of=as_of)
    return {
        code: _effective_date_value(resolver.try_resolve(code, as_of=as_of)) for code in codes
    }


def parameter_resolver(db: Session, bank: Bank, *, as_of: date) -> Any:
    """ONE scope resolution and ONE row load for every governed value needed.

    Built here because the reporting calendar's query budget is FIXED and must
    not grow with the registry
    (``test_regulatory_reporting_calendar_query_shape``): a ``try_resolve`` per
    code would re-resolve the policy scope each time. The resolver is carried on
    :class:`InstitutionEligibility` so the calendar's deadline lookups share it.

    **``record=False`` is the plane boundary, not an optimisation.** This
    resolver answers a DISPATCH question — "which returns exist for this
    institution, and when are they due" — by scanning EVERY registered
    definition's ``effective_from_parameter`` across EVERY family. It seals no
    ``RegulatoryRun``, so its reads are not any run's governed-row provenance.
    Recording them put whatever the scan happened to resolve into the session
    ledger that the next engine run drains, which is how an ICAAP commencement
    date moved an already-filed LIQUIDITY package's ``content_digest``. Reading
    without recording makes that structurally impossible for every registry
    entry, including ones not yet written.
    """
    from app.services import regulatory_parameters  # noqa: PLC0415 - avoid an import cycle

    return regulatory_parameters.PrefetchedParameterResolver.load(
        db, bank, as_of_dates=(as_of,), record=False
    )


def _effective_date_value(row: Any) -> date | None:
    """The ISO date inside a governed structural value, or ``None``."""
    if row is None:
        return None
    body = getattr(row, "value_json", None)
    if not isinstance(body, dict):
        return None
    raw = body.get("date")
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


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
