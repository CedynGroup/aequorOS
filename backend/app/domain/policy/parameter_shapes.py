"""What a governed parameter's value must LOOK like, for the codes that carry a table.

Founder directive D-024 says every regulatory number reaches the engines from
the operator console. Most are scalars, and the console has always been able to
edit those. The ICAAP Pillar 2 engine introduced the other kind: benchmark band
tables, an FX shock table, an operational severity map, a sovereign haircut
grid. Those are structural bodies, and a console that can write one it has not
checked is worse than one that cannot write it at all — a band table with a gap
in it silently returns *no* add-on for the exposures that fall in the gap, and
nothing downstream can tell that from a genuine zero.

So this module is the single description of each structural body's form, used in
three places that must agree:

* the operator API validates a proposal AND its approval against it (D-037), so
  a malformed table is a 422 naming the exact path, never an approved row;
* the seed catalogue is held to it by test, so a seeded table and a
  staff-proposed one cannot differ in form;
* the Pillar 2 domain parsers reuse the same validators, so a row that somehow
  reached the database malformed becomes a typed refusal rather than a crash
  inside an engine.

It is pure: it knows nothing about the database, HTTP or the ORM. Callers map
:class:`ParameterShapeError` onto their own error contract.

Nothing here is a regulatory value. The bounds are structural — a percentage
lies between 0 and 100, a ratio between 0 and 1, a band table covers its scale
without gaps — and say nothing about what any particular threshold should be.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from app.domain.irr import standardised_params as sfp

__all__ = [
    "SHAPES",
    "ParameterShapeError",
    "is_structural",
    "shape_for",
    "validate",
]

#: The add-on basis vocabulary a band table may name. Mirrors
#: ``app.models.icaap_risk_capital.ICAAP_BASES``; a test pins the two equal
#: (the model may not be imported here — ``app/domain`` is pure).
BAND_TABLE_BASES: Final[tuple[str, ...]] = (
    "pct_total_rwa",
    "pct_credit_rwa",
    "pct_pillar1_credit_capital",
    "absolute",
)
#: Band tables are read on a 0-1 scale; the alternative (basis points, 0-10,000)
#: is not implemented and must be refused rather than silently misread.
_BAND_SCALES: Final[tuple[str, ...]] = ("unit_interval",)
#: ``step`` takes the band's add-on; ``linear`` interpolates within the band.
_BAND_MODES: Final[tuple[str, ...]] = ("step", "linear")
_CONCENTRATION_DIMENSIONS: Final[tuple[str, ...]] = ("single_name", "sector")
_CURRENCY_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Z]{3}$")
_ISO_DATE_RE: Final[re.Pattern[str]] = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: A bucket bound, as the framework prints it: a count and a unit.
_TENOR_RE: Final[re.Pattern[str]] = re.compile(r"^\d+[DMY]$")
_KEY_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]*$")
_DEFAULT_KEY: Final[str] = "default"

_PERCENT_MAX: Final[Decimal] = Decimal(100)
_ZERO: Final[Decimal] = Decimal(0)
_ONE: Final[Decimal] = Decimal(1)


class ParameterShapeError(ValueError):
    """A governed value does not match the shape its code requires."""

    def __init__(self, path: str, message: str) -> None:
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message}")


def _decimal(value: Any, path: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, str | int | float | Decimal):
        raise ParameterShapeError(path, "must be a number (a decimal string is preferred)")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ParameterShapeError(path, "must be a number") from exc


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ParameterShapeError(path, "must be an object")
    for key in value:
        if not isinstance(key, str):
            raise ParameterShapeError(path, "keys must be strings")
    return value


def _sequence(value: Any, path: str) -> Sequence[Any]:
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        raise ParameterShapeError(path, "must be a list")
    return value


def _required(body: Mapping[str, Any], key: str, path: str) -> Any:
    if key not in body:
        raise ParameterShapeError(f"{path}.{key}", "is required")
    return body[key]


def _schema(body: Mapping[str, Any], expected: str) -> None:
    declared = _required(body, "schema", "value_json")
    if declared != expected:
        raise ParameterShapeError("value_json.schema", f"must be {expected!r}")


def _in(value: Any, allowed: Sequence[str], path: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ParameterShapeError(path, f"must be one of {', '.join(allowed)}")
    return value


def _bounded(value: Any, path: str, *, low: Decimal, high: Decimal | None) -> Decimal:
    number = _decimal(value, path)
    if number < low or (high is not None and number > high):
        bound = f"between {low} and {high}" if high is not None else f"at least {low}"
        raise ParameterShapeError(path, f"must be {bound}")
    return number


# --- scalar shapes --------------------------------------------------------


def _scalar_bool(numeric: Decimal | None, _json: Mapping[str, Any] | None) -> None:
    value = _scalar_required(numeric)
    if value not in {_ZERO, _ONE}:
        raise ParameterShapeError("value_numeric", "must be 0 (no) or 1 (yes)")


def _scalar_required(numeric: Decimal | None) -> Decimal:
    if numeric is None:  # pragma: no cover - validate() checks this first
        raise ParameterShapeError("value_numeric", "is required")
    return numeric


def _scalar_int_min1(numeric: Decimal | None, _json: Mapping[str, Any] | None) -> None:
    value = _scalar_required(numeric)
    if value != value.to_integral_value() or value < _ONE:
        raise ParameterShapeError("value_numeric", "must be a whole number of at least 1")


def _scalar_pct(numeric: Decimal | None, _json: Mapping[str, Any] | None) -> None:
    value = _scalar_required(numeric)
    if value < _ZERO or value > _PERCENT_MAX:
        raise ParameterShapeError("value_numeric", "must be a percentage between 0 and 100")


def _scalar_ratio(numeric: Decimal | None, _json: Mapping[str, Any] | None) -> None:
    value = _scalar_required(numeric)
    if value < _ZERO or value > _ONE:
        raise ParameterShapeError("value_numeric", "must be a ratio between 0 and 1")


def _scalar_positive(numeric: Decimal | None, _json: Mapping[str, Any] | None) -> None:
    """A strictly positive number, whole or fractional.

    Distinct from :func:`_scalar_int_min1`: a decay constant or a multiplier is
    meaningfully 4.5, while a count of months is not.
    """
    value = _scalar_required(numeric)
    if value <= _ZERO:
        raise ParameterShapeError("value_numeric", "must be greater than zero")


def _scalar_score(numeric: Decimal | None, _json: Mapping[str, Any] | None) -> None:
    """A likelihood x impact score: a whole number of at least 1, no upper bound.

    The matrix's size is framework data, not a parameter, so this checks the
    form and leaves "does 26 fit a 5x5 matrix" to the materiality domain, which
    knows the scale it was given.
    """
    _scalar_int_min1(numeric, None)


# --- structural shapes ----------------------------------------------------


def _band_table(code: str) -> Callable[[Decimal | None, Mapping[str, Any] | None], None]:
    metric, dimension = _BAND_TABLE_CODES[code]

    def _validate(_numeric: Decimal | None, body: Mapping[str, Any] | None) -> None:
        table = _structural_required(body)
        _schema(table, "icaap-band-table-v1")
        if _required(table, "metric", "value_json") != metric:
            raise ParameterShapeError("value_json.metric", f"must be {metric!r} for {code}")
        if _required(table, "dimension", "value_json") != dimension:
            raise ParameterShapeError("value_json.dimension", f"must be {dimension!r} for {code}")
        _in(_required(table, "scale", "value_json"), _BAND_SCALES, "value_json.scale")
        _in(_required(table, "mode", "value_json"), _BAND_MODES, "value_json.mode")
        _in(_required(table, "basis", "value_json"), BAND_TABLE_BASES, "value_json.basis")
        _bands(_sequence(_required(table, "bands", "value_json"), "value_json.bands"))

    return _validate


def _bands(bands: Sequence[Any]) -> None:
    """A band table covers its whole scale, once, with a non-decreasing add-on.

    A gap returns no add-on for the exposures inside it and an overlap returns
    two; both read downstream as an ordinary answer. The cover is therefore a
    shape rule, not a methodology choice.
    """
    if not bands:
        raise ParameterShapeError("value_json.bands", "must contain at least one band")
    previous_upper: Decimal | None = None
    previous_addon: Decimal | None = None
    last = len(bands) - 1
    for index, raw in enumerate(bands):
        path = f"value_json.bands[{index}]"
        band = _mapping(raw, path)
        lower = _bounded(_required(band, "lower", path), f"{path}.lower", low=_ZERO, high=None)
        if index == 0 and lower != _ZERO:
            raise ParameterShapeError(f"{path}.lower", "the first band must start at 0")
        if previous_upper is not None and lower != previous_upper:
            raise ParameterShapeError(
                f"{path}.lower",
                f"must continue the previous band exactly (expected {previous_upper})",
            )
        upper_raw = _required(band, "upper", path)
        if upper_raw is None:
            if index != last:
                raise ParameterShapeError(f"{path}.upper", "only the last band may be open-ended")
            previous_upper = None
        else:
            upper = _bounded(upper_raw, f"{path}.upper", low=_ZERO, high=None)
            if upper <= lower:
                raise ParameterShapeError(f"{path}.upper", "must be greater than lower")
            if index == last:
                raise ParameterShapeError(
                    f"{path}.upper", "the last band must be open-ended (null)"
                )
            previous_upper = upper
        addon = _bounded(_required(band, "addon", path), f"{path}.addon", low=_ZERO, high=None)
        if previous_addon is not None and addon < previous_addon:
            raise ParameterShapeError(
                f"{path}.addon", "must not fall as the metric rises (bands are ordered)"
            )
        previous_addon = addon


def _score_bands(_numeric: Decimal | None, body: Mapping[str, Any] | None) -> None:
    table = _structural_required(body)
    _schema(table, "icaap-score-bands-v1")
    bands = _sequence(_required(table, "bands", "value_json"), "value_json.bands")
    if not bands:
        raise ParameterShapeError("value_json.bands", "must contain at least one band")
    keys: set[str] = set()
    expected_min = 1
    for index, raw in enumerate(bands):
        path = f"value_json.bands[{index}]"
        band = _mapping(raw, path)
        key = _required(band, "key", path)
        if not isinstance(key, str) or not _KEY_RE.match(key):
            raise ParameterShapeError(f"{path}.key", "must be a lowercase identifier")
        if key in keys:
            raise ParameterShapeError(f"{path}.key", f"{key!r} is used twice")
        keys.add(key)
        label = _required(band, "label", path)
        if not isinstance(label, str) or not label.strip():
            raise ParameterShapeError(f"{path}.label", "must be a non-empty label")
        minimum = _integer(_required(band, "min_score", path), f"{path}.min_score")
        maximum = _integer(_required(band, "max_score", path), f"{path}.max_score")
        if minimum != expected_min:
            raise ParameterShapeError(
                f"{path}.min_score",
                f"must be {expected_min} so the bands cover every score without a gap",
            )
        if maximum < minimum:
            raise ParameterShapeError(f"{path}.max_score", "must not be below min_score")
        expected_min = maximum + 1


def _integer(value: Any, path: str) -> int:
    number = _decimal(value, path)
    if number != number.to_integral_value():
        raise ParameterShapeError(path, "must be a whole number")
    return int(number)


def _metric_set(_numeric: Decimal | None, body: Mapping[str, Any] | None) -> None:
    table = _structural_required(body)
    _schema(table, "icaap-ccr-metric-set-v1")
    for dimension in _CONCENTRATION_DIMENSIONS:
        path = f"value_json.{dimension}"
        metrics = _sequence(_required(table, dimension, "value_json"), path)
        if not metrics:
            raise ParameterShapeError(path, "must name at least one metric")
        seen: set[str] = set()
        for index, metric in enumerate(metrics):
            name = _in(metric, _CONCENTRATION_METRICS, f"{path}[{index}]")
            if name in seen:
                raise ParameterShapeError(f"{path}[{index}]", f"{name!r} is listed twice")
            seen.add(name)


def _code_list(_numeric: Decimal | None, body: Mapping[str, Any] | None) -> None:
    table = _structural_required(body)
    _schema(table, "icaap-code-list-v1")
    codes = _sequence(_required(table, "codes", "value_json"), "value_json.codes")
    if not codes:
        raise ParameterShapeError("value_json.codes", "must contain at least one code")
    seen: set[str] = set()
    for index, code in enumerate(codes):
        path = f"value_json.codes[{index}]"
        if not isinstance(code, str) or not _KEY_RE.match(code):
            raise ParameterShapeError(path, "must be a lowercase identifier")
        if code in seen:
            raise ParameterShapeError(path, f"{code!r} is listed twice")
        seen.add(code)
    required = _sequence(_required(table, "required", "value_json"), "value_json.required")
    for index, code in enumerate(required):
        if code not in seen:
            raise ParameterShapeError(f"value_json.required[{index}]", f"{code!r} is not in codes")


def _effective_date(_numeric: Decimal | None, body: Mapping[str, Any] | None) -> None:
    """A governed DATE, carried structurally because a date is not a number.

    The alternative — a day count from some epoch — would be a number nobody
    could read in the console, and the console is where this is meant to be
    corrected. So it is one ISO ``YYYY-MM-DD`` string with a declared schema,
    checked here for form only: which date is right is BoG's answer, not the
    platform's.
    """
    table = _structural_required(body)
    _schema(table, "icaap-effective-date-v1")
    value = _required(table, "date", "value_json")
    if not isinstance(value, str) or not _ISO_DATE_RE.match(value):
        raise ParameterShapeError("value_json.date", "must be an ISO date, YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ParameterShapeError("value_json.date", "is not a real calendar date") from exc


def _fx_shocks(_numeric: Decimal | None, body: Mapping[str, Any] | None) -> None:
    table = _structural_required(body)
    _schema(table, "icaap-fx-shocks-v1")
    for direction in ("depreciation", "appreciation"):
        path = f"value_json.{direction}"
        shocks = _mapping(_required(table, direction, "value_json"), path)
        if _DEFAULT_KEY not in shocks:
            raise ParameterShapeError(
                path,
                "must carry a 'default' shock; a currency with no entry would otherwise "
                "be shocked by nothing",
            )
        for currency, shock in shocks.items():
            if currency != _DEFAULT_KEY and not _CURRENCY_RE.match(currency):
                raise ParameterShapeError(
                    f"{path}.{currency}", "must be 'default' or an ISO 4217 code"
                )
            _bounded(shock, f"{path}.{currency}", low=_ZERO, high=_PERCENT_MAX)


def _severity_map(_numeric: Decimal | None, body: Mapping[str, Any] | None) -> None:
    table = _structural_required(body)
    _schema(table, "icaap-severity-map-v1")
    _in(_required(table, "basis", "value_json"), _SEVERITY_BASES, "value_json.basis")
    scenarios = _mapping(_required(table, "scenarios", "value_json"), "value_json.scenarios")
    if not scenarios:
        raise ParameterShapeError("value_json.scenarios", "must name at least one scenario")
    for name, severity in scenarios.items():
        path = f"value_json.scenarios.{name}"
        if not _KEY_RE.match(name):
            raise ParameterShapeError(path, "scenario keys must be lowercase identifiers")
        _bounded(severity, path, low=_ZERO, high=_PERCENT_MAX)


def _tenor_buckets(table: Mapping[str, Any]) -> list[str]:
    """The declared tenor buckets, ordered and open-ended at the top."""
    buckets = _sequence(_required(table, "tenor_buckets", "value_json"), "value_json.tenor_buckets")
    if not buckets:
        raise ParameterShapeError("value_json.tenor_buckets", "must contain at least one bucket")
    bucket_keys: list[str] = []
    previous_max: Decimal | None = None
    last = len(buckets) - 1
    for index, raw in enumerate(buckets):
        path = f"value_json.tenor_buckets[{index}]"
        bucket = _mapping(raw, path)
        key = _required(bucket, "key", path)
        if not isinstance(key, str) or not key.strip():
            raise ParameterShapeError(f"{path}.key", "must be a non-empty key")
        if key in bucket_keys:
            raise ParameterShapeError(f"{path}.key", f"{key!r} is used twice")
        bucket_keys.append(key)
        previous_max = _bucket_ceiling(bucket, path, index=index, last=last, previous=previous_max)
    return bucket_keys


def _bucket_ceiling(
    bucket: Mapping[str, Any], path: str, *, index: int, last: int, previous: Decimal | None
) -> Decimal | None:
    maximum = _required(bucket, "max_years", path)
    if maximum is None:
        if index != last:
            raise ParameterShapeError(f"{path}.max_years", "only the last bucket may be open-ended")
        return None
    years = _bounded(maximum, f"{path}.max_years", low=_ZERO, high=None)
    if previous is not None and years <= previous:
        raise ParameterShapeError(f"{path}.max_years", "buckets must be ordered by tenor")
    if index == last:
        raise ParameterShapeError(f"{path}.max_years", "the last bucket must be open-ended (null)")
    return years


def _haircut_grid(_numeric: Decimal | None, body: Mapping[str, Any] | None) -> None:
    table = _structural_required(body)
    _schema(table, "icaap-haircut-grid-v1")
    bucket_keys = _tenor_buckets(table)
    kinds = _sequence(_required(table, "currency_kinds", "value_json"), "value_json.currency_kinds")
    if not kinds:
        raise ParameterShapeError("value_json.currency_kinds", "must name at least one kind")
    haircuts = _mapping(_required(table, "haircut_pct", "value_json"), "value_json.haircut_pct")
    for kind_index, kind in enumerate(kinds):
        _in(kind, _CURRENCY_KINDS, f"value_json.currency_kinds[{kind_index}]")
        path = f"value_json.haircut_pct.{kind}"
        if kind not in haircuts:
            raise ParameterShapeError(path, f"is required: {kind!r} is a declared currency kind")
        row = _mapping(haircuts[kind], path)
        for bucket_key in bucket_keys:
            cell = f"{path}.{bucket_key}"
            if bucket_key not in row:
                raise ParameterShapeError(
                    cell, "is required: every declared tenor bucket needs a haircut"
                )
            _bounded(row[bucket_key], cell, low=_ZERO, high=_PERCENT_MAX)


# --- IRRBB Standardised Framework -----------------------------------------
#
# The vocabularies below are imported from the pure SF parameter module rather
# than restated, so a console body and the engine that parses it cannot come to
# disagree about which scenarios or deposit categories exist. None of them is a
# regulatory NUMBER — they are the names the framework prints.


def _sf_buckets(_numeric: Decimal | None, body: Mapping[str, Any] | None) -> None:
    """The time ladder: distinct keys, increasing midpoints, one open end.

    A duplicate key silently drops a bucket's flows and a non-increasing
    midpoint discounts a later flow at an earlier tenor; both read downstream
    as an ordinary answer, so they are shape rules.
    """
    table = _structural_required(body)
    _schema(table, "irrbb-sf-buckets-v1")
    buckets = _sequence(_required(table, "buckets", "value_json"), "value_json.buckets")
    if not buckets:
        raise ParameterShapeError("value_json.buckets", "must contain at least one bucket")
    seen: set[str] = set()
    previous: Decimal | None = None
    open_ended = False
    for index, entry in enumerate(buckets):
        path = f"value_json.buckets[{index}]"
        bucket = _mapping(entry, path)
        key = _required(bucket, "key", path)
        if not isinstance(key, str) or not key:
            raise ParameterShapeError(f"{path}.key", "must be a non-empty identifier")
        if key in seen:
            raise ParameterShapeError(f"{path}.key", f"{key!r} is listed twice")
        seen.add(key)
        label = _required(bucket, "label", path)
        if not isinstance(label, str) or not label:
            raise ParameterShapeError(f"{path}.label", "must be the printed bucket name")
        upper = _required(bucket, "upper", path)
        last = index == len(buckets) - 1
        if upper is None and not last:
            raise ParameterShapeError(f"{path}.upper", "only the final bucket is open-ended")
        if upper is None and last:
            open_ended = True
        elif not isinstance(upper, str) or not _TENOR_RE.match(upper):
            raise ParameterShapeError(
                f"{path}.upper", "must be a tenor such as 1D, 3M or 5Y, or null"
            )
        midpoint = _bounded(
            _required(bucket, "midpoint_years", path),
            f"{path}.midpoint_years",
            low=_ZERO,
            high=None,
        )
        if previous is not None and midpoint <= previous:
            raise ParameterShapeError(
                f"{path}.midpoint_years", "must be greater than the previous bucket's midpoint"
            )
        previous = midpoint
    if not open_ended:
        raise ParameterShapeError(
            "value_json.buckets", "the final bucket must be open-ended (upper: null)"
        )


def _sf_currency_bp(_numeric: Decimal | None, body: Mapping[str, Any] | None) -> None:
    """A shock in basis points per currency, plus the regulator's Other column."""
    table = _structural_required(body)
    _schema(table, "irrbb-sf-currency-bp-v1")
    columns = {key: value for key, value in table.items() if key != "schema"}
    if not columns:
        raise ParameterShapeError("value_json", "must carry at least one currency column")
    for key in sorted(columns):
        path = f"value_json.{key}"
        if key != sfp.OTHER_CURRENCY_KEY and not _CURRENCY_RE.match(key):
            raise ParameterShapeError(
                path, f"must be a three-letter currency code or {sfp.OTHER_CURRENCY_KEY!r}"
            )
        _bounded(columns[key], path, low=_ZERO, high=None)


def _sf_rotation(_numeric: Decimal | None, body: Mapping[str, Any] | None) -> None:
    table = _structural_required(body)
    _schema(table, "irrbb-sf-rotation-v1")
    for rotation in ("steepener", "flattener"):
        path = f"value_json.{rotation}"
        weights = _mapping(_required(table, rotation, "value_json"), path)
        for leg in ("short", "long"):
            # Signed on purpose: a rotation subtracts one end of the curve.
            _decimal(_required(weights, leg, path), f"{path}.{leg}")


def _sf_scenario_scalars(_numeric: Decimal | None, body: Mapping[str, Any] | None) -> None:
    table = _structural_required(body)
    _schema(table, "irrbb-sf-scenario-scalars-v1")
    for scenario in sfp.SCENARIOS:
        _bounded(
            _required(table, scenario, "value_json"),
            f"value_json.{scenario}",
            low=_ZERO,
            high=None,
        )


def _sf_nmd_caps(_numeric: Decimal | None, body: Mapping[str, Any] | None) -> None:
    table = _structural_required(body)
    _schema(table, "irrbb-sf-nmd-caps-v1")
    for category in sfp.NMD_CATEGORIES:
        path = f"value_json.{category}"
        caps = _mapping(_required(table, category, "value_json"), path)
        _bounded(
            _required(caps, "core_cap_pct", path),
            f"{path}.core_cap_pct",
            low=_ZERO,
            high=_PERCENT_MAX,
        )
        maturity = _bounded(
            _required(caps, "avg_maturity_cap_years", path),
            f"{path}.avg_maturity_cap_years",
            low=_ZERO,
            high=None,
        )
        if maturity == _ZERO:
            raise ParameterShapeError(
                f"{path}.avg_maturity_cap_years", "must be greater than zero"
            )


def _sf_cpr_scaling(_numeric: Decimal | None, body: Mapping[str, Any] | None) -> None:
    table = _structural_required(body)
    _schema(table, "irrbb-sf-cpr-scaling-v1")
    _in(_required(table, "mode", "value_json"), sfp.CPR_TIME_SCALINGS, "value_json.mode")


def _sf_cash_flow_profile(_numeric: Decimal | None, body: Mapping[str, Any] | None) -> None:
    """A default profile for every family the engine may need one for.

    A missing family is a typed refusal at run time; catching it here means the
    console cannot approve a table that would stop a run days later.
    """
    table = _structural_required(body)
    _schema(table, "irrbb-sf-cash-flow-profile-v1")
    for family in sfp.PROFILE_FAMILIES:
        path = f"value_json.{family}"
        profile = _mapping(_required(table, family, "value_json"), path)
        _in(
            _required(profile, "amortisation", path),
            sfp.AMORTISATIONS,
            f"{path}.amortisation",
        )
        _bounded(
            _required(profile, "frequency_months", path),
            f"{path}.frequency_months",
            low=_ZERO,
            high=None,
        )
        bucket = _required(profile, "horizonless_bucket", path)
        if not isinstance(bucket, str) or not bucket:
            raise ParameterShapeError(f"{path}.horizonless_bucket", "must name a bucket key")
    swap_path = f"value_json.{sfp.SWAP_PROFILE_KEY}"
    swap = _mapping(_required(table, sfp.SWAP_PROFILE_KEY, "value_json"), swap_path)
    _bounded(
        _required(swap, sfp.SWAP_FIXED_LEG_FREQUENCY_KEY, swap_path),
        f"{swap_path}.{sfp.SWAP_FIXED_LEG_FREQUENCY_KEY}",
        low=_ZERO,
        high=None,
    )


# --- granularity adjustment -------------------------------------------------


def _ga_asset_correlation(_numeric: Decimal | None, body: Mapping[str, Any] | None) -> None:
    table = _structural_required(body)
    _schema(table, "ga-asset-correlation-v1")
    segments = [key for key in table if key != "schema"]
    if not segments:
        raise ParameterShapeError("value_json", "must carry at least one segment")
    for segment in sorted(segments):
        path = f"value_json.{segment}"
        entry = _mapping(table[segment], path)
        r_min = _bounded(_required(entry, "r_min", path), f"{path}.r_min", low=_ZERO, high=_ONE)
        r_max = _bounded(_required(entry, "r_max", path), f"{path}.r_max", low=_ZERO, high=_ONE)
        if r_max < r_min:
            raise ParameterShapeError(f"{path}.r_max", "must not be below r_min")
        _bounded(_required(entry, "k", path), f"{path}.k", low=_ZERO, high=None)


def _ga_maturity_adjustment(_numeric: Decimal | None, body: Mapping[str, Any] | None) -> None:
    table = _structural_required(body)
    _schema(table, "ga-maturity-adjustment-v1")
    apply = _required(table, "apply", "value_json")
    if not isinstance(apply, bool):
        raise ParameterShapeError("value_json.apply", "must be true or false")
    for key in ("b_intercept", "b_slope", "m_centre", "m_scale"):
        _decimal(_required(table, key, "value_json"), f"value_json.{key}")


def _ga_proxy_pd(_numeric: Decimal | None, body: Mapping[str, Any] | None) -> None:
    table = _structural_required(body)
    _schema(table, "ga-proxy-pd-v1")
    entries = [key for key in table if key != "schema"]
    if not entries:
        raise ParameterShapeError("value_json", "must carry at least one risk-weight band")
    for key in sorted(entries):
        _bounded(table[key], f"value_json.{key}", low=_ZERO, high=_PERCENT_MAX)


def _ga_segment_map(_numeric: Decimal | None, body: Mapping[str, Any] | None) -> None:
    table = _structural_required(body)
    _schema(table, "ga-segment-map-v1")
    entries = [key for key in table if key != "schema"]
    if not entries:
        raise ParameterShapeError("value_json", "must map at least one counterparty type")
    for key in sorted(entries):
        value = table[key]
        if not isinstance(value, str) or not value:
            raise ParameterShapeError(f"value_json.{key}", "must name a segment or 'excluded'")


def _structural_required(body: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if body is None:  # pragma: no cover - validate() checks this first
        raise ParameterShapeError("value_json", "is required")
    return _mapping(body, "value_json")


_CONCENTRATION_METRICS: Final[tuple[str, ...]] = ("hhi", "gini", "crn")
_SEVERITY_BASES: Final[tuple[str, ...]] = ("pct_annual_gross_income",)
_CURRENCY_KINDS: Final[tuple[str, ...]] = ("reporting", "foreign")

#: (metric, dimension) the band table for each code must declare, so a sector
#: table cannot be pasted into the name-concentration slot.
_BAND_TABLE_CODES: Final[dict[str, tuple[str, str]]] = {
    "ccr_name_bands_hhi": ("hhi", "single_name"),
    "ccr_name_bands_gini": ("gini", "single_name"),
    "ccr_name_bands_crn": ("crn", "single_name"),
    "ccr_sector_bands_hhi": ("hhi", "sector"),
}

_Validator = Callable[[Decimal | None, Mapping[str, Any] | None], None]


class Shape:
    """One code's declared value form."""

    __slots__ = ("kind", "name", "validator")

    def __init__(self, name: str, *, kind: str, validator: _Validator) -> None:
        self.name = name
        self.kind = kind  # 'scalar' | 'structural'
        self.validator = validator

    @property
    def structural(self) -> bool:
        return self.kind == "structural"


def _scalar(name: str, validator: _Validator) -> Shape:
    return Shape(name, kind="scalar", validator=validator)


def _structural(name: str, validator: _Validator) -> Shape:
    return Shape(name, kind="structural", validator=validator)


#: Every ICAAP governed code, with the form its value must take. Codes NOT
#: listed here are unconstrained, exactly as before — registering a code is a
#: deliberate act that makes the operator API enforce its shape.
SHAPES: Final[Mapping[str, Shape]] = {
    # --- workspace (seeded by 202609190055) --------------------------------
    "icaap_submission_months": _scalar("months", _scalar_int_min1),
    "icaap_disclosure_submission_months": _scalar("months", _scalar_int_min1),
    "icaap_deadline_amber_days": _scalar("days", _scalar_int_min1),
    "icaap_materiality_material_min_score": _scalar("score", _scalar_score),
    "icaap_materiality_material_min_impact": _scalar("score", _scalar_score),
    "icaap_materiality_rating_bands": _structural("score_bands", _score_bands),
    "icaap_stress_horizon_years_min": _scalar("years", _scalar_int_min1),
    "icaap_capital_planning_horizon_years_min": _scalar("years", _scalar_int_min1),
    # --- Pillar 2 engine (seeded by 202609190056) --------------------------
    "icaap_independent_review_max_months": _scalar("months", _scalar_int_min1),
    "icaap_review_max_months": _scalar("months", _scalar_int_min1),
    "icaap_diversification_benefit_allowed": _scalar("boolean", _scalar_bool),
    "icaap_pillar2_source_tolerance_pct": _scalar("percent", _scalar_pct),
    "icaap_car_min_includes_ccb1": _scalar("boolean", _scalar_bool),
    "ccr_min_dimension_coverage_pct": _scalar("percent", _scalar_pct),
    "ccr_metric_set": _structural("metric_set", _metric_set),
    "ccr_name_cr_n": _scalar("count", _scalar_int_min1),
    "ccr_name_bands_hhi": _structural("band_table", _band_table("ccr_name_bands_hhi")),
    "ccr_name_bands_gini": _structural("band_table", _band_table("ccr_name_bands_gini")),
    "ccr_name_bands_crn": _structural("band_table", _band_table("ccr_name_bands_crn")),
    "ccr_sector_bands_hhi": _structural("band_table", _band_table("ccr_sector_bands_hhi")),
    "ccr_name_hhi_coeff": _scalar("ratio", _scalar_ratio),
    "ccr_sector_hhi_coeff": _scalar("ratio", _scalar_ratio),
    "irrbb_outlier_threshold_pct_tier1": _scalar("percent", _scalar_pct),
    "icaap_irrbb_interim_scenarios": _structural("code_list", _code_list),
    "fx_p2_shock_pct": _structural("shock_table", _fx_shocks),
    "op_p2_scenario_severity_pct_gross_income": _structural("severity_map", _severity_map),
    "sov_p2_haircut_pct": _structural("haircut_grid", _haircut_grid),
    "sov_p2_exposure_categories": _structural("code_list", _code_list),
    # --- filing plane (seeded by 202609190059) -----------------------------
    "icaap_report_first_as_of_date": _structural("effective_date", _effective_date),
    "icaap_stress_severe_scenarios_min": _scalar("count", _scalar_int_min1),
    # --- IRRBB Standardised Framework (seeded by 202609190061) -------------
    "irrbb_sf_time_buckets": _structural("tenor_table", _sf_buckets),
    "irrbb_sf_parallel_shock_bp": _structural("currency_bp", _sf_currency_bp),
    "irrbb_sf_short_shock_bp": _structural("currency_bp", _sf_currency_bp),
    "irrbb_sf_long_shock_bp": _structural("currency_bp", _sf_currency_bp),
    "irrbb_sf_short_decay_x": _scalar("years", _scalar_positive),
    "irrbb_sf_rotation_coefficients": _structural("rotation", _sf_rotation),
    "irrbb_sf_cpr_multipliers": _structural("scenario_scalars", _sf_scenario_scalars),
    "irrbb_sf_tdrr_scalars": _structural("scenario_scalars", _sf_scenario_scalars),
    "irrbb_sf_nmd_caps": _structural("nmd_caps", _sf_nmd_caps),
    "irrbb_sf_nmd_history_years": _scalar("years", _scalar_positive),
    "irrbb_sf_major_currency_threshold_pct": _scalar("percent", _scalar_pct),
    "irrbb_sf_outlier_scenario_set": _structural("code_list", _code_list),
    "irrbb_sf_mandatory_scenarios": _structural("code_list", _code_list),
    "irrbb_sf_nii_horizon_months": _scalar("months", _scalar_int_min1),
    "irrbb_sf_cpr_time_scaling": _structural("cpr_scaling", _sf_cpr_scaling),
    "irrbb_sf_default_cash_flow_profile": _structural(
        "cash_flow_profile", _sf_cash_flow_profile
    ),
    "irrbb_sf_mandatory_from_as_of": _structural("effective_date", _effective_date),
    # --- granularity adjustment (seeded by 202609190061) -------------------
    "ga_confidence_q": _scalar("ratio", _scalar_ratio),
    "ga_delta": _scalar("multiplier", _scalar_positive),
    "ga_lgd_variance_gamma": _scalar("ratio", _scalar_ratio),
    "ga_default_elgd_pct": _scalar("percent", _scalar_pct),
    "ga_min_effective_names": _scalar("count", _scalar_int_min1),
    "ga_asset_correlation": _structural("asset_correlation", _ga_asset_correlation),
    "ga_maturity_adjustment": _structural("maturity_adjustment", _ga_maturity_adjustment),
    "ga_proxy_pd_by_rw_code": _structural("proxy_pd", _ga_proxy_pd),
    "ga_counterparty_segment_map": _structural("segment_map", _ga_segment_map),
}


def shape_for(param_code: str) -> Shape | None:
    """The declared shape of ``param_code``, or None when it is unconstrained."""
    return SHAPES.get(param_code)


def is_structural(param_code: str) -> bool:
    """Whether ``param_code`` is a registered table-valued parameter."""
    shape = SHAPES.get(param_code)
    return shape is not None and shape.structural


def validate(
    param_code: str,
    value_numeric: Decimal | None,
    value_json: Mapping[str, Any] | None,
) -> None:
    """Check a governed value against its code's declared shape.

    Silent (returns None) for an unregistered code, and for a registered one
    whose value matches. Raises :class:`ParameterShapeError` with the path of
    the offending part otherwise.
    """
    shape = SHAPES.get(param_code)
    if shape is None:
        return
    if shape.structural:
        if value_json is None:
            raise ParameterShapeError(
                "value_json",
                f"{param_code} is a {shape.name}: it carries a structured value, not a number",
            )
        if value_numeric is not None:
            raise ParameterShapeError(
                "value_numeric",
                f"{param_code} is a {shape.name}: it carries a structured value, not a number",
            )
    else:
        if value_numeric is None:
            raise ParameterShapeError(
                "value_numeric", f"{param_code} is a scalar ({shape.name}): give a number"
            )
        if value_json is not None:
            raise ParameterShapeError(
                "value_json", f"{param_code} is a scalar ({shape.name}): give a number"
            )
    shape.validator(value_numeric, value_json)
