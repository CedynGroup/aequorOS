"""Instrument projection for the IRRBB Standardised Framework (pure).

One instrument in, a per-bucket schedule of principal, interest and scheduled
outstanding out. Pure: Decimal only, calendar arithmetic only, no service or
model imports and no governed number written down — the bucket geometry and
every default profile arrive in :class:`SfParameters`.

Conventions:

* **Slotting is upper-INCLUSIVE.** A flow falls in bucket ``k`` when
  ``as_of ⊕ upper(k-1) < when <= as_of ⊕ upper(k)``. A flow on or before the
  first bound — including a past-due one — falls in the first bucket.
* **Calendar month addition clamps to month end**, so the last day of a month
  plus one month is the last day of the next month, and a year is twelve
  months.
* **Amounts are unsigned magnitudes.** Which side of the balance sheet a
  position sits on is the caller's to apply, so a projection can be read
  without knowing it.
* **Every substitution is tallied**, never silent: a default profile, an
  unknown repricing horizon, an unknown accrual start, a floating position
  with no spread and principal already past due each leave a named marker in
  ``ProjectedFlows.defaulted``.

Past-due principal, stated because it is the one placement rule this module
invented rather than read. A horizon on or before the as-of date would produce
an empty schedule and the principal would vanish, so it keeps its one date and
slots into the shortest bucket. That is the right answer to "where does it go",
and it is the *least* conservative place it could go: the shortest bucket's
midpoint is days, so past-due principal is discounted at the shortest rate and
moves almost nothing when rates move. Modelling a non-performing exposure
properly means expected recovery cash flows and their timing, net of
provisions — years out, not overnight — so for a book with a material past-due
balance this UNDERSTATES ΔEVE. The projection cannot do better, because nothing
upstream carries performing status; what it can do is refuse to be silent, so
:data:`TALLY_PAST_DUE` counts every application and the service turns the count
into a disclosed assumption (audit R-3, 2026-09-20).
"""

from __future__ import annotations

import calendar
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from app.domain.irr.standardised_params import (
    CODE_DEFAULT_CASH_FLOW_PROFILE,
    DAYS_IN_YEAR,
    HUNDRED,
    MONTHS_IN_YEAR,
    ONE,
    ZERO,
    Amortisation,
    CashFlowProfile,
    SfParameterError,
    SfParameters,
    Tenor,
    TimeBucket,
)

#: Markers recorded when the projection had to substitute for missing terms.
TALLY_PROFILE_AMORTISATION = "profile:amortisation"
TALLY_PROFILE_FREQUENCY = "profile:frequency"
TALLY_HORIZONLESS = "horizonless"
TALLY_ACCRUAL_START_UNKNOWN = "accrual_start_unknown"
TALLY_NO_SPREAD_LEG = "no_spread_leg"
TALLY_PAST_DUE = "past_due_overnight"

TALLY_MARKERS: tuple[str, ...] = (
    TALLY_PROFILE_AMORTISATION,
    TALLY_PROFILE_FREQUENCY,
    TALLY_HORIZONLESS,
    TALLY_ACCRUAL_START_UNKNOWN,
    TALLY_NO_SPREAD_LEG,
    TALLY_PAST_DUE,
)

#: Swap legs carry their own family so the engine can tell them apart; they
#: take the swap entry of the governed profile table rather than a family row.
FAMILY_SWAP_FIXED_LEG = "SWAP_FIXED_LEG"
FAMILY_SWAP_FLOAT_LEG = "SWAP_FLOAT_LEG"

type RateType = Literal["FIXED", "FLOATING"]
type Side = Literal["asset", "liability"]


@dataclass(frozen=True)
class InstrumentTerms:
    """One in-scope banking-book position, in its native currency."""

    ref: str
    family: str
    currency: str
    side: Side
    principal: Decimal
    rate_pct: Decimal = ZERO
    rate_type: RateType = "FIXED"
    maturity: date | None = None
    next_reset: date | None = None
    start_date: date | None = None
    spread_pct: Decimal | None = None
    amortisation: str | None = None
    frequency_months: int | None = None


@dataclass(frozen=True)
class ProjectedFlows:
    """A projected schedule, bucket by bucket."""

    principal: tuple[Decimal, ...]
    interest: tuple[Decimal, ...]
    outstanding_end: tuple[Decimal, ...]
    defaulted: tuple[str, ...]

    @property
    def total_principal(self) -> Decimal:
        return sum(self.principal, ZERO)

    @property
    def total_interest(self) -> Decimal:
        return sum(self.interest, ZERO)


# --- calendar ----------------------------------------------------------------


def add_months(when: date, months: int) -> date:
    """Add whole months, clamping to the end of the target month."""
    total = when.month - 1 + months
    year = when.year + total // MONTHS_IN_YEAR
    month = total % MONTHS_IN_YEAR + 1
    day = min(when.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def add_tenor(as_of: date, tenor: Tenor) -> date:
    if tenor.unit == "D":
        return as_of + timedelta(days=tenor.count)
    if tenor.unit == "M":
        return add_months(as_of, tenor.count)
    return add_months(as_of, tenor.count * MONTHS_IN_YEAR)


def year_fraction(start: date, end: date) -> Decimal:
    """ACT/365, floored at zero."""
    days = (end - start).days
    if days <= 0:
        return ZERO
    return Decimal(days) / DAYS_IN_YEAR


def slot(as_of: date, when: date, buckets: Sequence[TimeBucket]) -> int:
    """Index of the bucket a flow dated ``when`` belongs to (upper-inclusive)."""
    for index, bucket in enumerate(buckets):
        if bucket.upper is None:
            return index
        if when <= add_tenor(as_of, bucket.upper):
            return index
    return len(buckets) - 1


# --- projection --------------------------------------------------------------


def _profile_for(terms: InstrumentTerms, params: SfParameters) -> CashFlowProfile:
    if terms.family in (FAMILY_SWAP_FIXED_LEG, FAMILY_SWAP_FLOAT_LEG):
        return CashFlowProfile(
            amortisation="bullet",
            frequency_months=(
                params.swap_fixed_leg_frequency_months
                if terms.family == FAMILY_SWAP_FIXED_LEG
                else 0
            ),
            horizonless_bucket=params.buckets[0].key,
        )
    profile = params.profiles.get(terms.family)
    if profile is None:
        raise SfParameterError(
            CODE_DEFAULT_CASH_FLOW_PROFILE,
            f"no default cash-flow profile is governed for {terms.family}",
        )
    return profile


def _horizon(terms: InstrumentTerms) -> date | None:
    """Where the whole principal reprices: the next reset, else maturity."""
    if terms.rate_type == "FLOATING":
        return terms.next_reset or terms.maturity
    return terms.maturity


def _payment_dates(as_of: date, horizon: date, frequency_months: int) -> list[date]:
    """Payment dates counted BACK from the horizon, keeping those after as-of.

    A horizon on or before the as-of date is a past-due flow: it keeps its one
    date and slots into the first bucket, rather than vanishing. The caller
    tallies that placement — see :data:`TALLY_PAST_DUE` and the module
    docstring for why it must not be silent.
    """
    if frequency_months <= 0 or horizon <= as_of:
        return [horizon]
    dates: list[date] = []
    step = 0
    while True:
        when = add_months(horizon, -frequency_months * step)
        if when <= as_of:
            return sorted(dates)
        dates.append(when)
        step += 1


def _principal_schedule(
    terms: InstrumentTerms, amortisation: Amortisation, rate_per_period: Decimal, periods: int
) -> list[Decimal]:
    """Principal repaid on each payment date."""
    if periods <= 0:
        return []
    if amortisation == "bullet":
        return [ZERO] * (periods - 1) + [terms.principal]
    if amortisation == "linear" or rate_per_period == ZERO:
        each = terms.principal / Decimal(periods)
        repaid = [each] * (periods - 1)
        return [*repaid, terms.principal - sum(repaid, ZERO)]
    return _annuity_schedule(terms.principal, rate_per_period, periods)


def _annuity_schedule(principal: Decimal, rate: Decimal, periods: int) -> list[Decimal]:
    discount = (ONE + rate) ** -periods
    payment = principal * rate / (ONE - discount)
    outstanding = principal
    schedule: list[Decimal] = []
    for index in range(periods):
        interest = outstanding * rate
        repaid = payment - interest
        if index == periods - 1:
            repaid = outstanding
        schedule.append(repaid)
        outstanding -= repaid
    return schedule


def _resolve_terms(
    terms: InstrumentTerms, params: SfParameters, tallies: list[str]
) -> tuple[Amortisation, int, CashFlowProfile]:
    profile = _profile_for(terms, params)
    amortisation = terms.amortisation
    if amortisation not in ("bullet", "linear", "annuity"):
        amortisation = profile.amortisation
        tallies.append(TALLY_PROFILE_AMORTISATION)
    frequency = terms.frequency_months
    if frequency is None or frequency < 0:
        frequency = profile.frequency_months
        tallies.append(TALLY_PROFILE_FREQUENCY)
    return amortisation, frequency, profile


def _horizonless(
    terms: InstrumentTerms, params: SfParameters, profile: CashFlowProfile, tallies: list[str]
) -> ProjectedFlows:
    """No maturity and no reset: the principal takes the profile's bucket."""
    tallies.append(TALLY_HORIZONLESS)
    size = params.bucket_count
    principal = [ZERO] * size
    outstanding = [ZERO] * size
    index = params.bucket_index(profile.horizonless_bucket)
    principal[index] = terms.principal
    for position in range(index):
        outstanding[position] = terms.principal
    return ProjectedFlows(
        principal=tuple(principal),
        interest=tuple([ZERO] * size),
        outstanding_end=tuple(outstanding),
        defaulted=tuple(tallies),
    )


def _single_payment_interest(
    terms: InstrumentTerms, as_of: date, horizon: date, tallies: list[str]
) -> Decimal:
    """Interest paid once, accrued ACT/365 to the horizon."""
    start = terms.start_date
    if start is None:
        tallies.append(TALLY_ACCRUAL_START_UNKNOWN)
        start = as_of
    else:
        start = max(start, as_of)
    return terms.principal * terms.rate_pct / HUNDRED * year_fraction(start, horizon)


def _spread_leg(
    terms: InstrumentTerms, as_of: date, horizon: date, frequency: int, tallies: list[str]
) -> list[tuple[date, Decimal]]:
    """A floating position's spread keeps running to contractual maturity."""
    if terms.rate_type != "FLOATING":
        return []
    if terms.spread_pct is None:
        tallies.append(TALLY_NO_SPREAD_LEG)
        return []
    maturity = terms.maturity
    if maturity is None or maturity <= horizon:
        return []
    rate = terms.spread_pct / HUNDRED
    if frequency <= 0:
        return [(maturity, terms.principal * rate * year_fraction(horizon, maturity))]
    period = Decimal(frequency) / Decimal(MONTHS_IN_YEAR)
    return [
        (when, terms.principal * rate * period)
        for when in _payment_dates(horizon, maturity, frequency)
    ]


def project(terms: InstrumentTerms, as_of: date, params: SfParameters) -> ProjectedFlows:
    """Project one instrument onto the governed bucket ladder."""
    tallies: list[str] = []
    amortisation, frequency, profile = _resolve_terms(terms, params, tallies)
    horizon = _horizon(terms)
    if horizon is None:
        return _horizonless(terms, params, profile, tallies)
    if horizon <= as_of and horizon == terms.maturity:
        # Only a passed MATURITY is past due. A floating position whose next
        # reset is already behind the reporting date also slots overnight, and
        # that placement is CORRECT — it reprices immediately — so tallying it
        # here would print an understatement warning about an exact answer.
        tallies.append(TALLY_PAST_DUE)

    size = params.bucket_count
    principal = [ZERO] * size
    interest = [ZERO] * size
    dates = _payment_dates(as_of, horizon, frequency)
    rate_per_period = terms.rate_pct / HUNDRED * Decimal(frequency) / Decimal(MONTHS_IN_YEAR)
    schedule = _principal_schedule(terms, amortisation, rate_per_period, len(dates))

    outstanding = terms.principal
    for position, when in enumerate(dates):
        index = slot(as_of, when, params.buckets)
        if frequency <= 0:
            interest[index] += _single_payment_interest(terms, as_of, when, tallies)
        else:
            interest[index] += outstanding * rate_per_period
        repaid = schedule[position]
        principal[index] += repaid
        outstanding -= repaid

    for when, amount in _spread_leg(terms, as_of, horizon, frequency, tallies):
        interest[slot(as_of, when, params.buckets)] += amount

    return ProjectedFlows(
        principal=tuple(principal),
        interest=tuple(interest),
        outstanding_end=tuple(_outstanding_end(terms.principal, principal)),
        defaulted=tuple(tallies),
    )


def _outstanding_end(opening: Decimal, principal: Sequence[Decimal]) -> list[Decimal]:
    remaining = opening
    ends: list[Decimal] = []
    for repaid in principal:
        remaining -= repaid
        ends.append(remaining)
    return ends


def swap_legs(
    terms: InstrumentTerms, *, pay_fixed: bool, float_reset: date | None
) -> tuple[InstrumentTerms, InstrumentTerms]:
    """Split an interest-rate swap into its two legs.

    A swap has no balance-sheet principal: each leg carries the notional, and
    the two sides net. The fixed leg pays coupons on the governed frequency and
    returns the notional at maturity; the floating leg returns the notional at
    its next reset.
    """
    paying: Side = "liability" if pay_fixed else "asset"
    receiving: Side = "asset" if pay_fixed else "liability"
    fixed = InstrumentTerms(
        ref=f"{terms.ref}:fixed",
        family=FAMILY_SWAP_FIXED_LEG,
        currency=terms.currency,
        side=paying,
        principal=terms.principal,
        rate_pct=terms.rate_pct,
        rate_type="FIXED",
        maturity=terms.maturity,
        start_date=terms.start_date,
    )
    floating = InstrumentTerms(
        ref=f"{terms.ref}:float",
        family=FAMILY_SWAP_FLOAT_LEG,
        currency=terms.currency,
        side=receiving,
        principal=terms.principal,
        rate_type="FLOATING",
        maturity=terms.maturity,
        next_reset=float_reset,
        start_date=terms.start_date,
    )
    return fixed, floating
