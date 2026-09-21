"""The governed parameter rows the Standardised Framework tests run against.

WS-B owns ``app/services/regulatory_parameters.py`` and the migration that seeds
these codes; the pure domain owns none of them. So the domain suite builds its
own control-plane rows here, from the P5 design's seed table, and every test
resolves through :func:`app.domain.irr.standardised_params.parse_parameters`
exactly as the service will. That keeps two properties true at once: the domain
tests never reach into a service module, and they still exercise the real
parse-and-validate path rather than hand-built objects.

The values are the design's seed table verbatim. They are DATA in a fixture,
not constants in the engine — changing one here changes the expected numbers,
which is the whole point of D-024.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal

from app.domain.irr import standardised_params as sp
from app.domain.irr.standardised import Ladder, LadderKind
from app.domain.irr.standardised_cash_flows import InstrumentTerms, project

#: Bucket upper bounds and midpoints, nineteen buckets.
_BUCKETS: tuple[tuple[str, str, str | None, str], ...] = (
    ("b01", "Overnight", "1D", "0.0028"),
    ("b02", "Overnight to 1 month", "1M", "0.0417"),
    ("b03", "1 to 3 months", "3M", "0.1667"),
    ("b04", "3 to 6 months", "6M", "0.375"),
    ("b05", "6 to 9 months", "9M", "0.625"),
    ("b06", "9 months to 1 year", "12M", "0.875"),
    ("b07", "1 to 1.5 years", "18M", "1.25"),
    ("b08", "1.5 to 2 years", "24M", "1.75"),
    ("b09", "2 to 3 years", "3Y", "2.5"),
    ("b10", "3 to 4 years", "4Y", "3.5"),
    ("b11", "4 to 5 years", "5Y", "4.5"),
    ("b12", "5 to 6 years", "6Y", "5.5"),
    ("b13", "6 to 7 years", "7Y", "6.5"),
    ("b14", "7 to 8 years", "8Y", "7.5"),
    ("b15", "8 to 9 years", "9Y", "8.5"),
    ("b16", "9 to 10 years", "10Y", "9.5"),
    ("b17", "10 to 15 years", "15Y", "12.5"),
    ("b18", "15 to 20 years", "20Y", "17.5"),
    ("b19", "Over 20 years", None, "25"),
)

BUCKET_COUNT = len(_BUCKETS)

#: Per-currency calibrations, with the regulator's printed "Other" column.
PARALLEL_BP: Mapping[str, str] = {
    "GHS": "450", "USD": "200", "EUR": "225", "GBP": "275", "CNY": "225", "OTHER": "325",
}
SHORT_BP: Mapping[str, str] = {
    "GHS": "500", "USD": "300", "EUR": "350", "GBP": "425", "CNY": "300", "OTHER": "500",
}
LONG_BP: Mapping[str, str] = {
    "GHS": "300", "USD": "225", "EUR": "200", "GBP": "250", "CNY": "150", "OTHER": "300",
}

_PROFILE_SEED: Mapping[str, Mapping[str, str]] = {
    "LOAN": {"amortisation": "annuity", "frequency_months": "1", "horizonless_bucket": "b13"},
    "SECURITY_HOLDING": {
        "amortisation": "bullet", "frequency_months": "6", "horizonless_bucket": "b13",
    },
    "INTERBANK_PLACEMENT": {
        "amortisation": "bullet", "frequency_months": "0", "horizonless_bucket": "b01",
    },
    "INTERBANK_BORROWING": {
        "amortisation": "bullet", "frequency_months": "0", "horizonless_bucket": "b01",
    },
    "DEPOSIT_TERM": {
        "amortisation": "bullet", "frequency_months": "0", "horizonless_bucket": "b01",
    },
    "OTHER_LIABILITY": {
        "amortisation": "bullet", "frequency_months": "0", "horizonless_bucket": "b01",
    },
    "CAPITAL_INSTRUMENT": {
        "amortisation": "bullet", "frequency_months": "6", "horizonless_bucket": "b13",
    },
    "INTEREST_RATE_SWAP": {"fixed_leg_frequency_months": "6"},
}

_CITATION = "IRRBB guideline (exposure draft), test fixture standing in for the seeded row"
_REPRESENTATIVE_CITATION = (
    "AequorOS platform methodology (REPRESENTATIVE): applied only where a position "
    "carries no terms; every use is tallied"
)


def _table(code: str, value_json: Mapping[str, object], unit: str) -> sp.GovernedValue:
    return sp.GovernedValue(
        param_code=code,
        value_json=value_json,
        unit=unit,
        source_citation=_CITATION,
        confirmation_status="pending",
    )


def _scalar(code: str, value: str, unit: str) -> sp.GovernedValue:
    return sp.GovernedValue(
        param_code=code,
        value=Decimal(value),
        unit=unit,
        source_citation=_CITATION,
        confirmation_status="pending",
    )


def seed_rows() -> dict[str, sp.GovernedValue]:
    """Every governed row the Standardised Framework consumes."""
    rows = {
        sp.CODE_TIME_BUCKETS: _table(
            sp.CODE_TIME_BUCKETS,
            {
                "schema": "irrbb-sf-buckets-v1",
                "buckets": [
                    {"key": key, "label": label, "upper": upper, "midpoint_years": midpoint}
                    for key, label, upper, midpoint in _BUCKETS
                ],
            },
            "tenor_table",
        ),
        sp.CODE_PARALLEL_SHOCK_BP: _table(
            sp.CODE_PARALLEL_SHOCK_BP, {"schema": "irrbb-sf-currency-bp-v1", **PARALLEL_BP}, "bps"
        ),
        sp.CODE_SHORT_SHOCK_BP: _table(
            sp.CODE_SHORT_SHOCK_BP, {"schema": "irrbb-sf-currency-bp-v1", **SHORT_BP}, "bps"
        ),
        sp.CODE_LONG_SHOCK_BP: _table(
            sp.CODE_LONG_SHOCK_BP, {"schema": "irrbb-sf-currency-bp-v1", **LONG_BP}, "bps"
        ),
        sp.CODE_SHORT_DECAY_X: _scalar(sp.CODE_SHORT_DECAY_X, "4", "years"),
        sp.CODE_ROTATION: _table(
            sp.CODE_ROTATION,
            {
                "schema": "irrbb-sf-rotation-v1",
                "steepener": {"short": "-0.65", "long": "0.9"},
                "flattener": {"short": "0.8", "long": "-0.6"},
            },
            "multiplier",
        ),
        sp.CODE_CPR_MULTIPLIERS: _table(
            sp.CODE_CPR_MULTIPLIERS,
            {
                "schema": "irrbb-sf-scenario-scalars-v1",
                "parallel_up": "0.8", "parallel_down": "1.2",
                "steepener": "0.8", "flattener": "1.2",
                "short_up": "0.8", "short_down": "1.2",
            },
            "multiplier",
        ),
        sp.CODE_TDRR_SCALARS: _table(
            sp.CODE_TDRR_SCALARS,
            {
                "schema": "irrbb-sf-scenario-scalars-v1",
                "parallel_up": "1.2", "parallel_down": "0.8",
                "steepener": "0.8", "flattener": "1.2",
                "short_up": "1.2", "short_down": "0.8",
            },
            "multiplier",
        ),
        sp.CODE_NMD_CAPS: _table(
            sp.CODE_NMD_CAPS,
            {
                "schema": "irrbb-sf-nmd-caps-v1",
                "retail_transactional": {
                    "core_cap_pct": "90", "avg_maturity_cap_years": "5",
                },
                "retail_non_transactional": {
                    "core_cap_pct": "70", "avg_maturity_cap_years": "4.5",
                },
                "wholesale": {"core_cap_pct": "50", "avg_maturity_cap_years": "4"},
            },
            "percent_years",
        ),
        sp.CODE_NMD_HISTORY_YEARS: _scalar(sp.CODE_NMD_HISTORY_YEARS, "10", "years"),
        sp.CODE_MAJOR_CURRENCY_THRESHOLD_PCT: _scalar(
            sp.CODE_MAJOR_CURRENCY_THRESHOLD_PCT, "5", "percent"
        ),
        # ``required`` is not optional in ``icaap-code-list-v1``: the shape the
        # operator console is held to demands it, and the fixture omitted it
        # (with the wrong unit) until 2026-09-20, because nothing compared the
        # fixture with the seeded row.
        sp.CODE_OUTLIER_SCENARIO_SET: _table(
            sp.CODE_OUTLIER_SCENARIO_SET,
            {
                "schema": "icaap-code-list-v1",
                "codes": list(sp.SCENARIOS),
                "required": [],
            },
            "code_list",
        ),
        sp.CODE_MANDATORY_SCENARIOS: _table(
            sp.CODE_MANDATORY_SCENARIOS,
            {
                "schema": "icaap-code-list-v1",
                "codes": ["parallel_up", "parallel_down"],
                "required": ["parallel_up", "parallel_down"],
            },
            "code_list",
        ),
        sp.CODE_NII_HORIZON_MONTHS: _scalar(sp.CODE_NII_HORIZON_MONTHS, "12", "months"),
        sp.CODE_CPR_TIME_SCALING: _table(
            sp.CODE_CPR_TIME_SCALING,
            {"schema": "irrbb-sf-cpr-scaling-v1", "mode": "annual_rate_scaled_to_bucket_width"},
            "method",
        ),
        sp.CODE_DEFAULT_CASH_FLOW_PROFILE: sp.GovernedValue(
            param_code=sp.CODE_DEFAULT_CASH_FLOW_PROFILE,
            value_json={"schema": "irrbb-sf-cash-flow-profile-v1", **_PROFILE_SEED},
            unit="profile",
            source_citation=_REPRESENTATIVE_CITATION,
            confirmation_status="pending",
        ),
        sp.CODE_OUTLIER_THRESHOLD_PCT: _scalar(
            sp.CODE_OUTLIER_THRESHOLD_PCT, "15", "percent"
        ),
    }
    return rows


def sf_parameters(overrides: Mapping[str, sp.GovernedValue] | None = None) -> sp.SfParameters:
    """Parse the seeded rows, with any row replaced by a console edit."""
    rows = seed_rows()
    rows.update(overrides or {})
    return sp.parse_parameters(rows)


def flat_curve(rate: str, params: sp.SfParameters) -> tuple[Decimal, ...]:
    """A flat continuously-compounded zero curve at every bucket midpoint."""
    return tuple(Decimal(rate) for _ in range(params.bucket_count))


def bucket_amounts(params: sp.SfParameters, amounts: Mapping[int, str]) -> tuple[Decimal, ...]:
    """A per-bucket vector from ``{1-based bucket number: amount}``."""
    values = [Decimal(0)] * params.bucket_count
    for number, amount in amounts.items():
        values[number - 1] += Decimal(amount)
    return tuple(values)


def running_outstanding(principal: Sequence[Decimal]) -> tuple[Decimal, ...]:
    """Scheduled balance after each bucket's principal."""
    remaining = sum(principal, Decimal(0))
    ends: list[Decimal] = []
    for amount in principal:
        remaining -= amount
        ends.append(remaining)
    return tuple(ends)


def simple_ladder(
    params: sp.SfParameters,
    currency: str,
    amounts: Mapping[int, str],
    kind: LadderKind = "fixed",
) -> Ladder:
    """A principal-only ladder, which is how the golden vectors are stated."""
    principal = bucket_amounts(params, amounts)
    return Ladder(
        currency=currency,
        portfolio=f"{currency}|{kind}",
        kind=kind,
        principal=principal,
        interest=tuple(Decimal(0) for _ in principal),
        outstanding_end=running_outstanding(principal),
    )


def ladder_from_instruments(
    params: sp.SfParameters,
    as_of: date,
    instruments: Sequence[InstrumentTerms],
    kind: LadderKind = "fixed",
) -> tuple[Ladder, dict[str, int]]:
    """Project instruments and aggregate them into one signed ladder.

    This is the stage the SERVICE owns in production (WS-B's loader). The
    domain suite does it here so the end-to-end vector can start from
    instrument terms rather than a hand-written ladder.
    """
    size = params.bucket_count
    principal = [Decimal(0)] * size
    interest = [Decimal(0)] * size
    outstanding = [Decimal(0)] * size
    tallies: dict[str, int] = {}
    for terms in instruments:
        flows = project(terms, as_of, params)
        sign = Decimal(1) if terms.side == "asset" else Decimal(-1)
        for index in range(size):
            principal[index] += sign * flows.principal[index]
            interest[index] += sign * flows.interest[index]
            outstanding[index] += sign * flows.outstanding_end[index]
        for marker in flows.defaulted:
            tallies[marker] = tallies.get(marker, 0) + 1
    return (
        Ladder(
            currency=instruments[0].currency,
            portfolio="book",
            kind=kind,
            principal=tuple(principal),
            interest=tuple(interest),
            outstanding_end=tuple(outstanding),
        ),
        tallies,
    )


def test_the_fixture_reproduces_the_seed_table() -> None:
    """The fixture is the seed table, so a drift here is visible immediately.

    Until 2026-09-20 every assertion here compared ``sf_parameters()`` against
    a constant defined **in this same file** (``BUCKET_COUNT`` is
    ``len(_BUCKETS)``; ``450`` is ``PARALLEL_BP["GHS"]``), so both sides moved
    together and the independent audit was right to call it vacuous: there are
    three copies of these numbers — the P5 migration, the service catalogue and
    this fixture — and nothing compared them. Changing the production GHS
    parallel shock left the whole suite green while the filed ΔEVE and the
    outlier verdict moved.

    So the assertion is now against the catalogue the platform actually seeds.
    Importing a service module from a domain test is deliberate and narrow: the
    pure layer still imports nothing, and the point of the test is precisely
    that the fixture is not independent of production.
    """
    from app.services.regulatory_parameters import (  # noqa: PLC0415 - the seam under test
        ICAAP_P2_SEED_PARAMETERS,
        IRRBB_SF_SEED_PARAMETERS,
    )

    #: Seeded in the P5 catalogue but never read by the PURE engine: it gates
    #: when the framework first bites, which is a filing decision.
    not_consumed = {"irrbb_sf_mandatory_from_as_of"}
    #: Read by the engine, seeded under the Pillar 2 heading because the ICAAP
    #: outlier test and the framework share the one threshold.
    shared_with_pillar2 = {"irrbb_outlier_threshold_pct_tier1"}

    production = {
        spec.param_code: spec
        for spec in (*IRRBB_SF_SEED_PARAMETERS, *ICAAP_P2_SEED_PARAMETERS)
        if spec.param_code in shared_with_pillar2
        or (spec in IRRBB_SF_SEED_PARAMETERS and spec.param_code not in not_consumed)
    }
    fixture = seed_rows()

    # Same codes, so a new governed row cannot be added to production without
    # the fixture (and therefore every golden that runs on it) noticing. The two
    # named exceptions are pinned as a SET, so a third divergence fails here.
    assert sorted(fixture) == sorted(production), (
        "the fixture and the seeded catalogue disagree on WHICH rows the "
        "Standardised Framework consumes"
    )

    for code, spec in sorted(production.items()):
        row = fixture[code]
        expected_value = None if spec.value is None else Decimal(spec.value)
        assert row.value == expected_value, f"{code}: scalar differs from the seeded row"
        assert row.unit == spec.unit, f"{code}: unit differs from the seeded row"
        assert row.confirmation_status == spec.confirmation_status, code
        # The citation is deliberately the fixture's own (it stands in for a
        # seeded row and says so); the BODY is production's and must match.
        if spec.value_json is None:
            assert row.value_json is None, f"{code}: fixture invents a structural body"
        else:
            assert row.value_json == dict(spec.value_json), (
                f"{code}: structural body differs from the seeded row"
            )

    # The parsed view the engine sees, still asserted, but now the numbers on
    # the right-hand side have been proven to be production's.
    params = sf_parameters()
    assert params.bucket_count == BUCKET_COUNT
    assert params.bucket_keys[0] == "b01"
    assert params.bucket_keys[-1] == "b19"
    assert params.shock_bp("parallel", "GHS") == Decimal("450")
    assert params.shock_bp("short", "GHS") == Decimal("500")
    assert params.shock_bp("long", "GHS") == Decimal("300")
    assert params.short_decay_x == Decimal("4")
    assert params.outlier_threshold_pct == Decimal("15")
    assert params.major_currency_threshold_pct == Decimal("5")
    assert params.nii_horizon_months == 12
    assert params.mandatory_scenarios == ("parallel_up", "parallel_down")
    assert params.outlier_scenarios == sp.SCENARIOS
