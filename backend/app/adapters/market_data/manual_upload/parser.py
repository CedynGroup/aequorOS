"""Parse uploaded market data files against the §8.2 template shapes.

The parser detects the template kind from the header row (per sheet — a
single workbook may carry several template sheets), coerces cell values
strictly (``Decimal``, ``int``, ISO dates), converts human-entered percent
yields to the decimal fractions the canonical model stores, and maps rows to
the pull-runner record dataclasses grouped by :class:`DataScope`.

Bad rows never abort the file: each problem is collected with its sheet and
row number (§8.3 partial-success contract), and everything parseable
proceeds. Rows that map to no scope in the taxonomy (an unsupported currency,
issuer, FX pair, or index code) are problems, not crashes.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.adapters.market_data.manual_upload.templates import (
    COMMENT_PREFIX,
    TEMPLATE_HEADERS,
    TemplateKind,
)
from app.adapters.market_data.pull_runner import (
    CurvePoint,
    CurveRecord,
    FxRateRecord,
    IndexRecord,
    MarketDataBundle,
    RatingRecord,
)
from app.adapters.market_data.scope_taxonomy import DataScope

SUPPORTED_SUFFIXES = (".xlsx", ".csv")

_PERCENT = Decimal(100)
_RATE_LOWER = Decimal(-1)
_RATE_UPPER = Decimal(1)

_FX_RATE_TYPES = ("spot", "forward")
_RATING_AGENCIES = ("moodys", "sp", "fitch")
_WATCH_STATUSES = ("positive", "negative", "stable", "developing")
_SCENARIOS = ("base", "adverse", "severely_adverse")

_RATING_SCOPES: dict[str, DataScope] = {
    "GHANA_SOVEREIGN": DataScope.CREDIT_RATING_GHANA_SOVEREIGN,
    "NIGERIA_SOVEREIGN": DataScope.CREDIT_RATING_NIGERIA_SOVEREIGN,
}

_MAX_CURVE_NAME_LENGTH = 80
_MAX_RATING_LENGTH = 16

_HEADER_SETS: dict[frozenset[str], TemplateKind] = {
    frozenset(headers): kind for kind, headers in TEMPLATE_HEADERS.items()
}


class ManualUploadParseError(ValueError):
    """The uploaded file cannot be opened or read as a template at all."""


@dataclass(frozen=True)
class RowProblem:
    """One row-level defect, located by sheet and 1-based row number."""

    sheet: str
    row_number: int
    message: str


@dataclass
class ScopeRows:
    """Everything one scope parsed into: raw rows for the raw tier plus the
    translated bundle the pull runner persists."""

    raw_rows: list[dict[str, Any]] = field(default_factory=list)
    bundle: MarketDataBundle = field(default_factory=MarketDataBundle)


@dataclass
class ParsedUpload:
    filename: str
    kinds: tuple[TemplateKind, ...]
    scopes: dict[DataScope, ScopeRows]
    problems: list[RowProblem]


class _RowError(ValueError):
    """A single row failed coercion or validation; recorded, never raised out."""


# ---------------------------------------------------------------------------
# File reading.
# ---------------------------------------------------------------------------


def read_grids(content: bytes, filename: str) -> list[tuple[str, list[list[Any]]]]:
    """Read an .xlsx or .csv upload into per-sheet cell grids.

    Raises :class:`ManualUploadParseError` when the file itself is unusable
    (unsupported type, corrupt workbook, non-UTF-8 CSV); row-level defects
    are the parser's concern, not the reader's.
    """
    suffix = Path(filename).suffix.lower()
    if suffix == ".xlsx":
        return _read_xlsx(content, filename)
    if suffix == ".csv":
        return [_read_csv(content, filename)]
    supported = ", ".join(SUPPORTED_SUFFIXES)
    msg = f"Unsupported file type {suffix or '(none)'!r}; supported: {supported}."
    raise ManualUploadParseError(msg)


def _read_xlsx(content: bytes, filename: str) -> list[tuple[str, list[list[Any]]]]:
    # Imported lazily: openpyxl is only needed when an .xlsx upload is read.
    import openpyxl  # noqa: PLC0415

    try:
        workbook = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    except Exception as exc:
        msg = f"Cannot open workbook {filename!r}: not a valid .xlsx file."
        raise ManualUploadParseError(msg) from exc
    try:
        return [
            (worksheet.title, [list(row) for row in worksheet.iter_rows(values_only=True)])
            for worksheet in workbook.worksheets
        ]
    finally:
        workbook.close()


def _read_csv(content: bytes, filename: str) -> tuple[str, list[list[Any]]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        msg = f"File {filename!r} is not valid UTF-8 text."
        raise ManualUploadParseError(msg) from exc
    grid: list[list[Any]] = [list(row) for row in csv.reader(io.StringIO(text))]
    return Path(filename).stem, grid


def is_blank_row(row: list[Any]) -> bool:
    return all(cell is None or (isinstance(cell, str) and not cell.strip()) for cell in row)


def is_comment_row(row: list[Any]) -> bool:
    if not row:
        return False
    first = row[0]
    return isinstance(first, str) and first.lstrip().startswith(COMMENT_PREFIX)


def detect_header(row: list[Any]) -> tuple[TemplateKind, dict[str, int]] | None:
    """Match a row against the template header sets.

    Returns the detected kind and a column-name -> cell-index map (column
    order in the upload does not matter), or None when the row is not a
    known template header.
    """
    named: list[tuple[int, str]] = []
    for index, cell in enumerate(row):
        if cell is None:
            continue
        name = str(cell).strip().lower()
        if name:
            named.append((index, name))
    names = [name for _, name in named]
    if len(names) != len(set(names)):
        return None
    kind = _HEADER_SETS.get(frozenset(names))
    if kind is None:
        return None
    return kind, {name: index for index, name in named}


# ---------------------------------------------------------------------------
# Cell coercion (strict; every failure names the offending column).
# ---------------------------------------------------------------------------


def _cell(cells: dict[str, Any], column: str) -> Any:
    value = cells.get(column)
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    return value


def _text(
    cells: dict[str, Any], column: str, *, required: bool = True, max_length: int | None = None
) -> str | None:
    value = _cell(cells, column)
    if value is None:
        if required:
            raise _RowError(f"{column} is required")
        return None
    text = str(value).strip()
    if max_length is not None and len(text) > max_length:
        raise _RowError(f"{column} exceeds {max_length} characters")
    return text


def _currency(cells: dict[str, Any], column: str) -> str:
    text = _text(cells, column)
    assert text is not None
    code = text.upper()
    if len(code) != 3 or not code.isalpha():
        raise _RowError(f"{column} must be a 3-letter ISO 4217 code, got {text!r}")
    return code


def _decimal(cells: dict[str, Any], column: str) -> Decimal:
    value = _cell(cells, column)
    if value is None:
        raise _RowError(f"{column} is required")
    if isinstance(value, bool | datetime | date):
        raise _RowError(f"{column} must be a number, got {value!r}")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise _RowError(f"{column} must be a number, got {value!r}") from exc
    if not number.is_finite():
        raise _RowError(f"{column} must be a finite number, got {value!r}")
    return number


def _int(cells: dict[str, Any], column: str, *, required: bool = True) -> int | None:
    if _cell(cells, column) is None:
        if required:
            raise _RowError(f"{column} is required")
        return None
    number = _decimal(cells, column)
    if number != number.to_integral_value():
        raise _RowError(f"{column} must be a whole number, got {number}")
    return int(number)


def _date(cells: dict[str, Any], column: str) -> date:
    value = _cell(cells, column)
    if value is None:
        raise _RowError(f"{column} is required")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise _RowError(f"{column} must be an ISO 8601 date (YYYY-MM-DD), got {value!r}") from exc


def _choice(
    cells: dict[str, Any],
    column: str,
    choices: tuple[str, ...],
    *,
    required: bool = True,
) -> str | None:
    text = _text(cells, column, required=required)
    if text is None:
        return None
    value = text.lower()
    if value not in choices:
        raise _RowError(f"{column} must be one of {', '.join(choices)}; got {text!r}")
    return value


def _check_as_of(row_as_of: date, expected: date | None) -> None:
    if expected is not None and row_as_of != expected:
        raise _RowError(
            f"as_of_date {row_as_of.isoformat()} does not match the upload as-of date "
            f"{expected.isoformat()}"
        )


def _lookup_scope(name: str) -> DataScope | None:
    try:
        return DataScope[name]
    except KeyError:
        return None


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


# ---------------------------------------------------------------------------
# Row collection.
# ---------------------------------------------------------------------------


@dataclass
class _CurveBuilder:
    currency: str
    curve_name: str
    source_reference: str
    points: dict[int, Decimal] = field(default_factory=dict)


class _Collector:
    """Accumulates parsed rows per scope and builds the final bundles."""

    def __init__(self, filename: str, expected_as_of: date | None) -> None:
        self.filename = filename
        self.expected_as_of = expected_as_of
        self.problems: list[RowProblem] = []
        self.kinds: list[TemplateKind] = []
        self._curves: dict[DataScope, dict[tuple[str, str], _CurveBuilder]] = {}
        self._fx: dict[DataScope, list[FxRateRecord]] = {}
        self._ratings: dict[DataScope, dict[tuple[str, str], RatingRecord]] = {}
        self._indices: dict[DataScope, dict[tuple[str, str, int | None], IndexRecord]] = {}
        self._raw: dict[DataScope, list[dict[str, Any]]] = {}
        self._samples: dict[DataScope, dict[str, str]] = {}

    # -- sheet walking -------------------------------------------------------

    def parse_sheet(self, sheet: str, grid: list[list[Any]]) -> None:
        kind: TemplateKind | None = None
        columns: dict[str, int] = {}
        for row_number, row in enumerate(grid, start=1):
            if is_blank_row(row) or is_comment_row(row):
                continue
            if kind is None:
                detected = detect_header(row)
                if detected is None:
                    self.problems.append(
                        RowProblem(
                            sheet,
                            row_number,
                            "headers do not match any manual upload template "
                            "(yield_curve, fx_rates, credit_ratings, macro_forecasts)",
                        )
                    )
                    return
                kind, columns = detected
                if kind not in self.kinds:
                    self.kinds.append(kind)
                continue
            cells = {
                name: row[index] if index < len(row) else None for name, index in columns.items()
            }
            try:
                _ROW_PARSERS[kind](self, sheet, row_number, cells)
            except _RowError as exc:
                self.problems.append(RowProblem(sheet, row_number, str(exc)))

    # -- shared bookkeeping ---------------------------------------------------

    def _row_reference(self, sheet: str, row_number: int) -> str:
        return f"{self.filename}!{sheet}:r{row_number}"

    def _record_raw(
        self, scope: DataScope, sheet: str, row_number: int, cells: dict[str, Any]
    ) -> None:
        self._raw.setdefault(scope, []).append(
            {
                "sheet": sheet,
                "row": row_number,
                **{name: _json_safe(value) for name, value in cells.items()},
            }
        )

    def _record_sample(self, scope: DataScope, key: str, value: str) -> None:
        self._samples.setdefault(scope, {})[key] = value

    # -- per-kind row parsers --------------------------------------------------

    def parse_yield_curve_row(self, sheet: str, row_number: int, cells: dict[str, Any]) -> None:
        currency = _currency(cells, "currency")
        curve_name = _text(cells, "curve_name", max_length=_MAX_CURVE_NAME_LENGTH)
        assert curve_name is not None
        _check_as_of(_date(cells, "as_of_date"), self.expected_as_of)
        tenor = _int(cells, "tenor_months")
        assert tenor is not None
        if tenor <= 0:
            raise _RowError(f"tenor_months must be positive, got {tenor}")
        percent = _decimal(cells, "rate_percent")
        rate = percent / _PERCENT
        if not (_RATE_LOWER <= rate <= _RATE_UPPER):
            raise _RowError(
                f"rate_percent {percent} is outside the plausible [-100, 100] percent band"
            )
        scope = _lookup_scope(f"YIELD_CURVE_{currency}")
        if scope is None:
            raise _RowError(f"unsupported currency {currency!r}: no yield curve scope exists")

        curves = self._curves.setdefault(scope, {})
        builder = curves.get((currency, curve_name))
        if builder is None:
            builder = _CurveBuilder(
                currency=currency,
                curve_name=curve_name,
                source_reference=f"{self.filename}!{sheet}:{currency}/{curve_name}",
            )
            curves[(currency, curve_name)] = builder
        if tenor in builder.points:
            raise _RowError(f"duplicate tenor {tenor} for curve {curve_name}")
        builder.points[tenor] = rate
        self._record_raw(scope, sheet, row_number, cells)
        self._record_sample(scope, f"{currency} {tenor}M", f"{rate * _PERCENT:.2f}%")

    def parse_fx_row(self, sheet: str, row_number: int, cells: dict[str, Any]) -> None:
        base = _currency(cells, "base_currency")
        quote = _currency(cells, "quote_currency")
        rate_type = _choice(cells, "rate_type", _FX_RATE_TYPES)
        assert rate_type is not None
        tenor = _int(cells, "tenor_months", required=False)
        if rate_type == "spot" and tenor is not None:
            raise _RowError("tenor_months must be blank for spot rates")
        if rate_type == "forward":
            if tenor is None:
                raise _RowError("tenor_months is required for forward rates")
            if tenor <= 0:
                raise _RowError(f"tenor_months must be positive, got {tenor}")
        rate = _decimal(cells, "rate")
        if rate <= 0:
            raise _RowError(f"rate must be positive, got {rate}")
        _check_as_of(_date(cells, "as_of_date"), self.expected_as_of)

        scope_name = (
            f"FX_SPOT_{base}_{quote}"
            if rate_type == "spot"
            else f"FX_FORWARD_{base}_{quote}_{tenor}M"
        )
        scope = _lookup_scope(scope_name)
        if scope is None:
            raise _RowError(
                f"unsupported FX {rate_type} {base}/{quote}"
                f"{'' if tenor is None else f' {tenor}M'}: no scope {scope_name} exists"
            )
        records = self._fx.setdefault(scope, [])
        if records:
            raise _RowError(f"duplicate row for {scope_name}")
        records.append(
            FxRateRecord(
                base_currency=base,
                quote_currency=quote,
                rate_type=rate_type,
                tenor_months=tenor,
                rate=rate,
                source_reference=self._row_reference(sheet, row_number),
            )
        )
        self._record_raw(scope, sheet, row_number, cells)
        label = (
            f"{base}/{quote} spot" if rate_type == "spot" else f"{base}/{quote} {tenor}M forward"
        )
        self._record_sample(scope, label, f"{rate:.4f}")

    def parse_rating_row(self, sheet: str, row_number: int, cells: dict[str, Any]) -> None:
        issuer_text = _text(cells, "issuer")
        assert issuer_text is not None
        issuer = issuer_text.upper()
        scope = _RATING_SCOPES.get(issuer)
        if scope is None:
            expected = ", ".join(sorted(_RATING_SCOPES))
            raise _RowError(f"unsupported issuer {issuer_text!r}; expected one of {expected}")
        agency = _choice(cells, "agency", _RATING_AGENCIES)
        assert agency is not None
        rating = _text(cells, "rating", max_length=_MAX_RATING_LENGTH)
        assert rating is not None
        watch_status = _choice(cells, "watch_status", _WATCH_STATUSES, required=False)
        rating_date = _date(cells, "rating_date")

        ratings = self._ratings.setdefault(scope, {})
        if (issuer, agency) in ratings:
            raise _RowError(f"duplicate rating row for {issuer} / {agency}")
        ratings[(issuer, agency)] = RatingRecord(
            issuer=issuer,
            agency=agency,
            rating=rating,
            watch_status=watch_status,
            rating_date=rating_date,
            source_reference=self._row_reference(sheet, row_number),
        )
        self._record_raw(scope, sheet, row_number, cells)
        value = rating if watch_status is None else f"{rating}, watch {watch_status}"
        self._record_sample(scope, f"{issuer} ({agency})", value)

    def parse_macro_row(self, sheet: str, row_number: int, cells: dict[str, Any]) -> None:
        index_text = _text(cells, "index_code")
        assert index_text is not None
        index_code = index_text.upper()
        scope = _lookup_scope(f"MACRO_{index_code}")
        if scope is None:
            raise _RowError(f"unsupported index_code {index_text!r}: no macro scope exists")
        value = _decimal(cells, "value")
        scenario = _choice(cells, "scenario", _SCENARIOS)
        assert scenario is not None
        horizon = _int(cells, "horizon_months", required=False)
        if horizon is not None and horizon <= 0:
            raise _RowError(f"horizon_months must be positive when present, got {horizon}")
        _check_as_of(_date(cells, "as_of_date"), self.expected_as_of)

        indices = self._indices.setdefault(scope, {})
        key = (index_code, scenario, horizon)
        if key in indices:
            raise _RowError(
                f"duplicate row for {index_code} scenario {scenario}"
                f"{'' if horizon is None else f' horizon {horizon}M'}"
            )
        indices[key] = IndexRecord(
            index_code=index_code,
            value=value,
            scenario=scenario,
            horizon_months=horizon,
            source_reference=self._row_reference(sheet, row_number),
        )
        self._record_raw(scope, sheet, row_number, cells)
        label = f"{index_code} {scenario}" + ("" if horizon is None else f" {horizon}M")
        self._record_sample(scope, label, str(value))

    # -- assembly ----------------------------------------------------------------

    def finish(self) -> ParsedUpload:
        scopes: dict[DataScope, ScopeRows] = {}
        for scope, builders in self._curves.items():
            bundle = MarketDataBundle(
                curves=[
                    CurveRecord(
                        currency=builder.currency,
                        curve_name=builder.curve_name,
                        curve_type="sovereign",
                        source_reference=builder.source_reference,
                        points=tuple(
                            CurvePoint(tenor_months=tenor, rate=builder.points[tenor])
                            for tenor in sorted(builder.points)
                        ),
                    )
                    for builder in builders.values()
                ],
                sample_values=self._samples.get(scope, {}),
            )
            scopes[scope] = ScopeRows(raw_rows=self._raw.get(scope, []), bundle=bundle)
        for scope, fx_records in self._fx.items():
            scopes[scope] = ScopeRows(
                raw_rows=self._raw.get(scope, []),
                bundle=MarketDataBundle(
                    fx_rates=list(fx_records), sample_values=self._samples.get(scope, {})
                ),
            )
        for scope, rating_map in self._ratings.items():
            scopes[scope] = ScopeRows(
                raw_rows=self._raw.get(scope, []),
                bundle=MarketDataBundle(
                    ratings=list(rating_map.values()),
                    sample_values=self._samples.get(scope, {}),
                ),
            )
        for scope, index_map in self._indices.items():
            scopes[scope] = ScopeRows(
                raw_rows=self._raw.get(scope, []),
                bundle=MarketDataBundle(
                    indices=list(index_map.values()),
                    sample_values=self._samples.get(scope, {}),
                ),
            )
        return ParsedUpload(
            filename=self.filename,
            kinds=tuple(self.kinds),
            scopes=scopes,
            problems=self.problems,
        )


_ROW_PARSERS: dict[TemplateKind, Any] = {
    "yield_curve": _Collector.parse_yield_curve_row,
    "fx_rates": _Collector.parse_fx_row,
    "credit_ratings": _Collector.parse_rating_row,
    "macro_forecasts": _Collector.parse_macro_row,
}


def parse_upload(
    content: bytes, filename: str, *, expected_as_of: date | None = None
) -> ParsedUpload:
    """Parse one uploaded file into scope-grouped bundles plus row problems.

    ``expected_as_of`` enforces that every row's ``as_of_date`` matches the
    upload's business date (rows that disagree become problems rather than
    silently re-dated records); pass None to skip the check (test pulls).
    """
    grids = read_grids(content, filename)
    collector = _Collector(filename, expected_as_of)
    for sheet_name, grid in grids:
        collector.parse_sheet(sheet_name, grid)
    return collector.finish()
