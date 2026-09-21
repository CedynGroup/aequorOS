"""Governed parameters for the IRRBB Standardised Framework (SF).

Pure: Decimal only, no ``app.services`` / ``app.models`` imports. Every
regulatory or methodological number the SF applies — the time buckets and their
midpoints, the six shock calibrations per currency, the short-rate decay, the
rotation coefficients, the prepayment and term-deposit scalars, the
non-maturing-deposit caps, the materiality threshold, the outlier threshold,
the scenario sets, the earnings horizon and the default cash-flow profiles —
arrives here as a CONTROL-PLANE ROW and is parsed into typed values. Nothing is
written down in code (founder directive D-024): a missing row is a typed
``SfParameterError`` refusal, never a fallback.

Provenance travels with the values. Two facts about every consumed row reach the
result and therefore the reader:

* ``pending_confirmation`` — the control plane says the value is not yet
  confirmed with the supervisor (the SF calibration is an exposure draft);
* ``representative`` — the value is an AequorOS platform methodology with no
  published supervisory source. A representative value is NEVER presented as a
  regulator's number. ``REPRESENTATIVE_CODES`` names the codes that are
  representative by construction, and a row whose own citation carries the
  ``REPRESENTATIVE`` marker is honoured as well, so the control plane can label
  a value the code has not anticipated.

Jurisdiction neutrality: no currency, regulator or country is named here. The
currency keys of the shock tables, including the regulator's printed "Other"
column, are DATA.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Protocol, cast

# Conventions, not regulatory values: identity/emptiness, a pair, the calendar
# and the percent / basis-point scales.
ZERO = Decimal(0)
ONE = Decimal(1)
TWO = Decimal(2)
HUNDRED = Decimal(100)
MONTHS_IN_YEAR = 12
DAYS_IN_YEAR = Decimal(365)
BASIS_POINTS_IN_UNIT = Decimal(10000)

#: The six prescribed shapes. A vocabulary, not a calibration — the numbers for
#: each one are governed rows.
SCENARIOS: tuple[str, ...] = (
    "parallel_up",
    "parallel_down",
    "steepener",
    "flattener",
    "short_up",
    "short_down",
)

#: Production copy. Raw enum codes never reach a bank-facing surface.
SCENARIO_LABELS: Mapping[str, str] = {
    "parallel_up": "Parallel up",
    "parallel_down": "Parallel down",
    "steepener": "Steepener",
    "flattener": "Flattener",
    "short_up": "Short rate up",
    "short_down": "Short rate down",
}

#: Deposit categories the non-maturing-deposit caps are stated for.
NMD_CATEGORIES: tuple[str, ...] = (
    "retail_transactional",
    "retail_non_transactional",
    "wholesale",
)

#: Position families that need a default cash-flow profile when an ingested
#: position carries no amortisation, payment frequency or repricing horizon.
PROFILE_FAMILIES: tuple[str, ...] = (
    "LOAN",
    "SECURITY_HOLDING",
    "INTERBANK_PLACEMENT",
    "INTERBANK_BORROWING",
    "DEPOSIT_TERM",
    "OTHER_LIABILITY",
    "CAPITAL_INSTRUMENT",
)

#: The swap entry of the profile table carries only the fixed leg's frequency.
SWAP_PROFILE_KEY = "INTEREST_RATE_SWAP"
SWAP_FIXED_LEG_FREQUENCY_KEY = "fixed_leg_frequency_months"

#: The regulator prints an "Other" column for currencies it does not calibrate
#: individually. It is a column of the governed table, not a code fallback.
OTHER_CURRENCY_KEY = "OTHER"

type TenorUnit = Literal["D", "M", "Y"]
type NmdCategory = Literal["retail_transactional", "retail_non_transactional", "wholesale"]
type Amortisation = Literal["bullet", "linear", "annuity"]
type CprTimeScaling = Literal["annual_rate_scaled_to_bucket_width", "per_bucket_as_printed"]
type ShockKind = Literal["parallel", "short", "long"]

CPR_TIME_SCALINGS: tuple[str, ...] = (
    "annual_rate_scaled_to_bucket_width",
    "per_bucket_as_printed",
)
AMORTISATIONS: tuple[str, ...] = ("bullet", "linear", "annuity")

# --- parameter codes ---------------------------------------------------------

CODE_TIME_BUCKETS = "irrbb_sf_time_buckets"
CODE_PARALLEL_SHOCK_BP = "irrbb_sf_parallel_shock_bp"
CODE_SHORT_SHOCK_BP = "irrbb_sf_short_shock_bp"
CODE_LONG_SHOCK_BP = "irrbb_sf_long_shock_bp"
CODE_SHORT_DECAY_X = "irrbb_sf_short_decay_x"
CODE_ROTATION = "irrbb_sf_rotation_coefficients"
CODE_CPR_MULTIPLIERS = "irrbb_sf_cpr_multipliers"
CODE_TDRR_SCALARS = "irrbb_sf_tdrr_scalars"
CODE_NMD_CAPS = "irrbb_sf_nmd_caps"
CODE_NMD_HISTORY_YEARS = "irrbb_sf_nmd_history_years"
CODE_MAJOR_CURRENCY_THRESHOLD_PCT = "irrbb_sf_major_currency_threshold_pct"
CODE_OUTLIER_SCENARIO_SET = "irrbb_sf_outlier_scenario_set"
CODE_MANDATORY_SCENARIOS = "irrbb_sf_mandatory_scenarios"
CODE_NII_HORIZON_MONTHS = "irrbb_sf_nii_horizon_months"
CODE_CPR_TIME_SCALING = "irrbb_sf_cpr_time_scaling"
CODE_DEFAULT_CASH_FLOW_PROFILE = "irrbb_sf_default_cash_flow_profile"
CODE_OUTLIER_THRESHOLD_PCT = "irrbb_outlier_threshold_pct_tier1"

#: Every code the SF consumes. The loader resolves exactly these; anything
#: missing refuses the run rather than defaulting.
REQUIRED_CODES: tuple[str, ...] = (
    CODE_TIME_BUCKETS,
    CODE_PARALLEL_SHOCK_BP,
    CODE_SHORT_SHOCK_BP,
    CODE_LONG_SHOCK_BP,
    CODE_SHORT_DECAY_X,
    CODE_ROTATION,
    CODE_CPR_MULTIPLIERS,
    CODE_TDRR_SCALARS,
    CODE_NMD_CAPS,
    CODE_NMD_HISTORY_YEARS,
    CODE_MAJOR_CURRENCY_THRESHOLD_PCT,
    CODE_OUTLIER_SCENARIO_SET,
    CODE_MANDATORY_SCENARIOS,
    CODE_NII_HORIZON_MONTHS,
    CODE_CPR_TIME_SCALING,
    CODE_DEFAULT_CASH_FLOW_PROFILE,
    CODE_OUTLIER_THRESHOLD_PCT,
)

#: Production copy for each governed input, used in provenance statements and
#: by the read model. Never a raw code on a bank-facing surface.
PARAMETER_LABELS: Mapping[str, str] = {
    CODE_TIME_BUCKETS: "Time buckets",
    CODE_PARALLEL_SHOCK_BP: "Parallel rate shocks",
    CODE_SHORT_SHOCK_BP: "Short rate shocks",
    CODE_LONG_SHOCK_BP: "Long rate shocks",
    CODE_SHORT_DECAY_X: "Short-rate decay",
    CODE_ROTATION: "Rotation coefficients",
    CODE_CPR_MULTIPLIERS: "Prepayment multipliers",
    CODE_TDRR_SCALARS: "Term-deposit redemption scalars",
    CODE_NMD_CAPS: "Non-maturing deposit caps",
    CODE_NMD_HISTORY_YEARS: "Deposit observation history",
    CODE_MAJOR_CURRENCY_THRESHOLD_PCT: "Material currency threshold",
    CODE_OUTLIER_SCENARIO_SET: "Outlier test scenarios",
    CODE_MANDATORY_SCENARIOS: "Mandatory reporting scenarios",
    CODE_NII_HORIZON_MONTHS: "Earnings horizon",
    CODE_CPR_TIME_SCALING: "Prepayment time scaling",
    CODE_DEFAULT_CASH_FLOW_PROFILE: "Default cash-flow profiles",
    CODE_OUTLIER_THRESHOLD_PCT: "Outlier threshold",
}

#: Codes whose value is an AequorOS platform methodology, with no published
#: supervisory calibration behind it. Adding a code here is a review decision.
#:
#: ``irrbb_sf_default_cash_flow_profile`` — applied ONLY where an ingested
#: position carries no amortisation, payment frequency or repricing horizon.
#: Every application is tallied and disclosed, so a reader can see how much of
#: the book it touched.
REPRESENTATIVE_CODES: frozenset[str] = frozenset({CODE_DEFAULT_CASH_FLOW_PROFILE})

#: A control-plane citation may declare a row representative itself.
REPRESENTATIVE_MARKER = "REPRESENTATIVE"

_PENDING_STATUS = "pending"


class ResolvedValue(Protocol):
    """The shape of a resolved control-plane row the SF reads.

    Read-only by design: the SF consumes governed values and never edits them.
    ``app.services.regulatory_parameters.ResolvedParameter`` satisfies this
    structurally, which is how the pure layer stays free of service imports.
    """

    @property
    def param_code(self) -> str: ...
    @property
    def value(self) -> Decimal | None: ...
    @property
    def value_json(self) -> Mapping[str, Any] | None: ...
    @property
    def unit(self) -> str: ...
    @property
    def source_citation(self) -> str: ...
    @property
    def confirmation_status(self) -> str: ...


@dataclass(frozen=True)
class GovernedValue:
    """A concrete :class:`ResolvedValue`, so the pure layer is usable alone."""

    param_code: str
    value: Decimal | None = None
    value_json: Mapping[str, Any] | None = None
    unit: str = ""
    source_citation: str = ""
    confirmation_status: str = _PENDING_STATUS


class SfError(ValueError):
    """Base of every typed Standardised Framework refusal."""

    def __init__(self, code: str, message: str, detail: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code: str = code
        self.detail: Mapping[str, Any] = dict(detail or {})


class SfParameterError(SfError):
    """A governed row the SF needs is absent or cannot be read."""

    MISSING = "missing_parameter"
    INVALID = "invalid_parameter"

    def __init__(self, param_code: str, reason: str, *, code: str = INVALID) -> None:
        super().__init__(code, f"{param_code}: {reason}", {"param_code": param_code})
        self.param_code: str = param_code
        self.reason: str = reason


@dataclass(frozen=True)
class Tenor:
    """A calendar tenor such as ``1D``, ``18M`` or ``20Y``."""

    count: int
    unit: TenorUnit

    @property
    def years(self) -> Decimal:
        """Approximate length in years — for ORDERING and width, not slotting.

        Slotting adds the tenor to the as-of date on the calendar
        (``standardised_cash_flows.add_tenor``); this is the scalar the bucket
        widths and the prepayment time scaling use.
        """
        count = Decimal(self.count)
        if self.unit == "D":
            return count / DAYS_IN_YEAR
        if self.unit == "M":
            return count / Decimal(MONTHS_IN_YEAR)
        return count

    def __str__(self) -> str:
        return f"{self.count}{self.unit}"


def parse_tenor(text: str, *, param_code: str) -> Tenor:
    raw = text.strip().upper()
    if len(raw) < TWO or raw[-1] not in ("D", "M", "Y"):
        raise SfParameterError(param_code, f"{text!r} is not a tenor such as '3M' or '20Y'")
    try:
        count = int(raw[:-1])
    except ValueError as exc:
        raise SfParameterError(param_code, f"{text!r} has no tenor count") from exc
    if count <= 0:
        raise SfParameterError(param_code, f"{text!r} must be a positive tenor")
    return Tenor(count=count, unit=cast(TenorUnit, raw[-1]))


@dataclass(frozen=True)
class TimeBucket:
    """One repricing bucket: an upper bound (open-ended when null) and a midpoint."""

    key: str
    label: str
    upper: Tenor | None
    midpoint_years: Decimal


@dataclass(frozen=True)
class Rotation:
    """Weights the rotational scenarios apply to the short and long components."""

    steep_short: Decimal
    steep_long: Decimal
    flat_short: Decimal
    flat_long: Decimal


@dataclass(frozen=True)
class NmdCap:
    core_cap_pct: Decimal
    avg_maturity_cap_years: Decimal


@dataclass(frozen=True)
class CashFlowProfile:
    amortisation: Amortisation
    frequency_months: int
    horizonless_bucket: str


@dataclass(frozen=True)
class ParameterProvenance:
    """What a reader must be told about one governed input."""

    code: str
    label: str
    unit: str
    confirmation_status: str
    source_citation: str
    representative: bool

    @property
    def pending_confirmation(self) -> bool:
        return self.confirmation_status == _PENDING_STATUS

    @property
    def statement(self) -> str:
        """Production copy. A representative value is never called a rule."""
        if self.representative:
            return (
                f"{self.label}: AequorOS platform methodology, representative only — "
                "not a published supervisory value."
            )
        if self.pending_confirmation:
            return f"{self.label}: pending confirmation with the supervisor."
        return f"{self.label}: confirmed."


@dataclass(frozen=True)
class SfParameters:
    """Every governed input the SF engine reads, typed and validated."""

    buckets: tuple[TimeBucket, ...]
    parallel_bp: Mapping[str, Decimal]
    short_bp: Mapping[str, Decimal]
    long_bp: Mapping[str, Decimal]
    short_decay_x: Decimal
    rotation: Rotation
    cpr_multipliers: Mapping[str, Decimal]
    tdrr_scalars: Mapping[str, Decimal]
    nmd_caps: Mapping[str, NmdCap]
    nmd_history_years: Decimal
    major_currency_threshold_pct: Decimal
    outlier_threshold_pct: Decimal
    outlier_scenarios: tuple[str, ...]
    mandatory_scenarios: tuple[str, ...]
    nii_horizon_months: int
    cpr_time_scaling: CprTimeScaling
    profiles: Mapping[str, CashFlowProfile]
    swap_fixed_leg_frequency_months: int
    provenance: tuple[ParameterProvenance, ...]

    # --- bucket geometry ---

    @property
    def bucket_count(self) -> int:
        return len(self.buckets)

    @property
    def bucket_keys(self) -> tuple[str, ...]:
        return tuple(bucket.key for bucket in self.buckets)

    @property
    def midpoints(self) -> tuple[Decimal, ...]:
        return tuple(bucket.midpoint_years for bucket in self.buckets)

    @property
    def upper_years(self) -> tuple[Decimal | None, ...]:
        return tuple(None if b.upper is None else b.upper.years for b in self.buckets)

    @property
    def lower_years(self) -> tuple[Decimal, ...]:
        uppers = self.upper_years
        return (ZERO, *[bound if bound is not None else ZERO for bound in uppers[:-1]])

    @property
    def widths_years(self) -> tuple[Decimal, ...]:
        """Bucket widths in years.

        The final bucket is open-ended, so it has no width: the SF uses twice
        the distance from its lower bound to its midpoint, which is the width
        the midpoint would imply for a closed bucket.
        """
        uppers = self.upper_years
        lowers = self.lower_years
        widths: list[Decimal] = []
        for index, upper in enumerate(uppers):
            lower = lowers[index]
            if upper is None:
                widths.append(TWO * (self.buckets[index].midpoint_years - lower))
            else:
                widths.append(upper - lower)
        return tuple(widths)

    def bucket_index(self, key: str) -> int:
        for index, bucket in enumerate(self.buckets):
            if bucket.key == key:
                return index
        raise SfParameterError(CODE_TIME_BUCKETS, f"no bucket keyed {key!r}")

    def index_for_years(self, years: Decimal) -> int:
        """Upper-INCLUSIVE slotting of a tenor already expressed in years."""
        for index, upper in enumerate(self.upper_years):
            if upper is None or years <= upper:
                return index
        return self.bucket_count - 1

    # --- calibrations ---

    def shock_bp(self, kind: ShockKind, currency: str) -> Decimal:
        table = {
            "parallel": self.parallel_bp,
            "short": self.short_bp,
            "long": self.long_bp,
        }[kind]
        if currency in table:
            return table[currency]
        if OTHER_CURRENCY_KEY in table:
            return table[OTHER_CURRENCY_KEY]
        code = {
            "parallel": CODE_PARALLEL_SHOCK_BP,
            "short": CODE_SHORT_SHOCK_BP,
            "long": CODE_LONG_SHOCK_BP,
        }[kind]
        raise SfParameterError(code, f"no calibration for {currency!r} and no 'Other' column")

    @property
    def pending_codes(self) -> tuple[str, ...]:
        return tuple(row.code for row in self.provenance if row.pending_confirmation)

    @property
    def representative_codes(self) -> tuple[str, ...]:
        return tuple(row.code for row in self.provenance if row.representative)


# --- parsing -----------------------------------------------------------------


def _require(raw: Mapping[str, ResolvedValue], code: str) -> ResolvedValue:
    row = raw.get(code)
    if row is None:
        raise SfParameterError(code, "no approved value", code=SfParameterError.MISSING)
    return row


def _scalar(row: ResolvedValue) -> Decimal:
    if row.value is None:
        raise SfParameterError(row.param_code, "expected a scalar value")
    return row.value


def _mapping(row: ResolvedValue) -> Mapping[str, Any]:
    if row.value_json is None:
        raise SfParameterError(row.param_code, "expected a table value")
    return row.value_json


def _decimal(code: str, field: str, raw: Any) -> Decimal:
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise SfParameterError(code, f"{field} is not a number: {raw!r}") from exc


def _positive_int(code: str, field: str, raw: Any) -> int:
    value = _decimal(code, field, raw)
    if value != value.to_integral_value() or value < ZERO:
        raise SfParameterError(code, f"{field} must be a whole number of months: {raw!r}")
    return int(value)


def _currency_table(row: ResolvedValue) -> Mapping[str, Decimal]:
    code = row.param_code
    raw = _mapping(row)
    table = {
        key: _decimal(code, key, value)
        for key, value in raw.items()
        if key != "schema" and not isinstance(value, Mapping)
    }
    if not table:
        raise SfParameterError(code, "carries no currency column")
    return table


def _scenario_scalars(row: ResolvedValue) -> Mapping[str, Decimal]:
    code = row.param_code
    raw = _mapping(row)
    table = {code_: _decimal(code, code_, raw[code_]) for code_ in SCENARIOS if code_ in raw}
    missing = [scenario for scenario in SCENARIOS if scenario not in table]
    if missing:
        raise SfParameterError(code, f"no value for {', '.join(missing)}")
    return table


def _scenario_list(row: ResolvedValue) -> tuple[str, ...]:
    code = row.param_code
    raw = _mapping(row)
    codes = raw.get("codes")
    if not isinstance(codes, (list, tuple)) or not codes:
        raise SfParameterError(code, "expected a non-empty 'codes' list")
    unknown = [str(item) for item in codes if item not in SCENARIOS]
    if unknown:
        raise SfParameterError(code, f"names scenarios the framework has no shape for: {unknown}")
    ordered = tuple(scenario for scenario in SCENARIOS if scenario in set(codes))
    return ordered


def _buckets(row: ResolvedValue) -> tuple[TimeBucket, ...]:
    code = row.param_code
    entries = _mapping(row).get("buckets")
    if not isinstance(entries, (list, tuple)) or not entries:
        raise SfParameterError(code, "expected a non-empty 'buckets' list")
    buckets = tuple(_one_bucket(code, entry) for entry in entries)
    _validate_bucket_geometry(code, buckets)
    return buckets


def _one_bucket(code: str, entry: Any) -> TimeBucket:
    if not isinstance(entry, Mapping):
        raise SfParameterError(code, f"bucket entry is not an object: {entry!r}")
    key = str(entry.get("key") or "")
    if not key:
        raise SfParameterError(code, "a bucket has no key")
    upper_raw = entry.get("upper")
    upper = None if upper_raw in (None, "") else parse_tenor(str(upper_raw), param_code=code)
    return TimeBucket(
        key=key,
        label=str(entry.get("label") or key),
        upper=upper,
        midpoint_years=_decimal(code, f"{key}.midpoint_years", entry.get("midpoint_years")),
    )


def _validate_bucket_geometry(code: str, buckets: tuple[TimeBucket, ...]) -> None:
    keys = [bucket.key for bucket in buckets]
    if len(set(keys)) != len(keys):
        raise SfParameterError(code, "bucket keys repeat")
    if buckets[-1].upper is not None:
        raise SfParameterError(code, "the last bucket must be open-ended (null upper bound)")
    if any(bucket.upper is None for bucket in buckets[:-1]):
        raise SfParameterError(code, "only the last bucket may be open-ended")
    lower = ZERO
    previous_midpoint: Decimal | None = None
    for bucket in buckets:
        # A midpoint may exceed its own upper bound — the overnight bucket's
        # printed midpoint does — so only the lower bound is checked.
        if bucket.midpoint_years <= lower:
            raise SfParameterError(code, f"{bucket.key} midpoint is not inside the bucket")
        if previous_midpoint is not None and bucket.midpoint_years <= previous_midpoint:
            raise SfParameterError(code, f"{bucket.key} midpoint does not increase")
        previous_midpoint = bucket.midpoint_years
        if bucket.upper is not None:
            if bucket.upper.years <= lower:
                raise SfParameterError(code, f"{bucket.key} upper bound does not increase")
            lower = bucket.upper.years


def _rotation(row: ResolvedValue) -> Rotation:
    code = row.param_code
    raw = _mapping(row)
    steep = raw.get("steepener")
    flat = raw.get("flattener")
    if not isinstance(steep, Mapping) or not isinstance(flat, Mapping):
        raise SfParameterError(code, "expected 'steepener' and 'flattener' objects")
    return Rotation(
        steep_short=_decimal(code, "steepener.short", steep.get("short")),
        steep_long=_decimal(code, "steepener.long", steep.get("long")),
        flat_short=_decimal(code, "flattener.short", flat.get("short")),
        flat_long=_decimal(code, "flattener.long", flat.get("long")),
    )


def _nmd_caps(row: ResolvedValue) -> Mapping[str, NmdCap]:
    code = row.param_code
    raw = _mapping(row)
    caps: dict[str, NmdCap] = {}
    for category in NMD_CATEGORIES:
        entry = raw.get(category)
        if not isinstance(entry, Mapping):
            raise SfParameterError(code, f"no caps for {category}")
        cap = NmdCap(
            core_cap_pct=_decimal(code, f"{category}.core_cap_pct", entry.get("core_cap_pct")),
            avg_maturity_cap_years=_decimal(
                code, f"{category}.avg_maturity_cap_years", entry.get("avg_maturity_cap_years")
            ),
        )
        if not ZERO <= cap.core_cap_pct <= HUNDRED:
            raise SfParameterError(code, f"{category} core cap is not a percentage")
        if cap.avg_maturity_cap_years <= ZERO:
            raise SfParameterError(code, f"{category} average maturity cap must be positive")
        caps[category] = cap
    return caps


def _profiles(row: ResolvedValue) -> tuple[Mapping[str, CashFlowProfile], int]:
    code = row.param_code
    raw = _mapping(row)
    profiles = {family: _one_profile(code, family, raw.get(family)) for family in PROFILE_FAMILIES}
    swap = raw.get(SWAP_PROFILE_KEY)
    if not isinstance(swap, Mapping):
        raise SfParameterError(code, f"no profile for {SWAP_PROFILE_KEY}")
    frequency = _positive_int(
        code, f"{SWAP_PROFILE_KEY}.{SWAP_FIXED_LEG_FREQUENCY_KEY}",
        swap.get(SWAP_FIXED_LEG_FREQUENCY_KEY),
    )
    return profiles, frequency


def _one_profile(code: str, family: str, entry: Any) -> CashFlowProfile:
    if not isinstance(entry, Mapping):
        raise SfParameterError(code, f"no profile for {family}")
    amortisation = str(entry.get("amortisation") or "")
    if amortisation not in AMORTISATIONS:
        raise SfParameterError(code, f"{family} amortisation {amortisation!r} is not supported")
    bucket = str(entry.get("horizonless_bucket") or "")
    if not bucket:
        raise SfParameterError(code, f"{family} has no horizonless bucket")
    return CashFlowProfile(
        amortisation=cast(Amortisation, amortisation),
        frequency_months=_positive_int(code, f"{family}.frequency_months",
                                       entry.get("frequency_months")),
        horizonless_bucket=bucket,
    )


def _cpr_scaling(row: ResolvedValue) -> CprTimeScaling:
    code = row.param_code
    mode = str(_mapping(row).get("mode") or "")
    if mode not in CPR_TIME_SCALINGS:
        raise SfParameterError(code, f"{mode!r} is not a supported time-scaling mode")
    return cast(CprTimeScaling, mode)


def _provenance(row: ResolvedValue) -> ParameterProvenance:
    citation = row.source_citation or ""
    return ParameterProvenance(
        code=row.param_code,
        label=PARAMETER_LABELS.get(row.param_code, row.param_code),
        unit=row.unit or "",
        confirmation_status=row.confirmation_status or "",
        source_citation=citation,
        representative=(
            row.param_code in REPRESENTATIVE_CODES or REPRESENTATIVE_MARKER in citation.upper()
        ),
    )


def parse_parameters(raw: Mapping[str, ResolvedValue]) -> SfParameters:
    """Validate the governed rows the SF needs and type them.

    Raises :class:`SfParameterError` with ``code`` ``missing_parameter`` when a
    row is absent and ``invalid_parameter`` when one cannot be read. There is no
    fallback value anywhere in this function (D-024).
    """
    rows = {code: _require(raw, code) for code in REQUIRED_CODES}
    profiles, swap_frequency = _profiles(rows[CODE_DEFAULT_CASH_FLOW_PROFILE])
    caps = _nmd_caps(rows[CODE_NMD_CAPS])
    threshold = _scalar(rows[CODE_MAJOR_CURRENCY_THRESHOLD_PCT])
    outlier_threshold = _scalar(rows[CODE_OUTLIER_THRESHOLD_PCT])
    if threshold < ZERO or outlier_threshold < ZERO:
        raise SfParameterError(CODE_OUTLIER_THRESHOLD_PCT, "thresholds must not be negative")
    decay = _scalar(rows[CODE_SHORT_DECAY_X])
    if decay <= ZERO:
        raise SfParameterError(CODE_SHORT_DECAY_X, "the short-rate decay must be positive")
    horizon_row = _scalar(rows[CODE_NII_HORIZON_MONTHS])
    horizon = _positive_int(CODE_NII_HORIZON_MONTHS, "months", horizon_row)
    if horizon <= 0:
        raise SfParameterError(CODE_NII_HORIZON_MONTHS, "the earnings horizon must be positive")
    return SfParameters(
        buckets=_buckets(rows[CODE_TIME_BUCKETS]),
        parallel_bp=_currency_table(rows[CODE_PARALLEL_SHOCK_BP]),
        short_bp=_currency_table(rows[CODE_SHORT_SHOCK_BP]),
        long_bp=_currency_table(rows[CODE_LONG_SHOCK_BP]),
        short_decay_x=decay,
        rotation=_rotation(rows[CODE_ROTATION]),
        cpr_multipliers=_scenario_scalars(rows[CODE_CPR_MULTIPLIERS]),
        tdrr_scalars=_scenario_scalars(rows[CODE_TDRR_SCALARS]),
        nmd_caps=caps,
        nmd_history_years=_scalar(rows[CODE_NMD_HISTORY_YEARS]),
        major_currency_threshold_pct=threshold,
        outlier_threshold_pct=outlier_threshold,
        outlier_scenarios=_scenario_list(rows[CODE_OUTLIER_SCENARIO_SET]),
        mandatory_scenarios=_scenario_list(rows[CODE_MANDATORY_SCENARIOS]),
        nii_horizon_months=horizon,
        cpr_time_scaling=_cpr_scaling(rows[CODE_CPR_TIME_SCALING]),
        profiles=profiles,
        swap_fixed_leg_frequency_months=swap_frequency,
        provenance=tuple(_provenance(rows[code]) for code in REQUIRED_CODES),
    )
