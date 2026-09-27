"""BI query and result contracts (``.ai/bi_contracts.md`` §Interfaces, binding).

A ``BiQuery`` is the ONE shape every BI read surface accepts — the query
endpoint, the grid adapter, drill, explain and exports all compile it through
``app/services/bi/compiler.py``. It is closed (``extra="forbid"``), every
operator is a Literal, every list is bounded, and nothing in it can name a
table, a column or a SQL fragment: members are catalogue ids and values are
typed scalars the compiler binds as parameters. Validation failures are
pydantic's (422) and quote the offending field, never a statement.

Class names here are unique across ``app/schemas`` on purpose: FastAPI emits
``app__schemas__x__Name`` component keys when two modules share a class name,
and the generated client cannot map those back (AGENTS.md, openapi hazard).
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Longest member id the API accepts. A catalogue id is a few dozen characters;
#: this is the bound every member field carries — including the ITEMS of the
#: measure and dimension lists, which once had only a list-length cap, so one
#: oversized id could ride in inside a list (audit A6-03). It is also the length
#: at which ``app/services/bi/errors.py`` stops echoing an id back.
MEMBER_ID_MAX_LENGTH = 120

#: ``in`` / ``not_in`` accept at most this many values (S13: caps are server-side).
BI_FILTER_IN_CAP = 500
#: A pivot may emit at most this many value columns, whatever the client asks for.
BI_PIVOT_MAX_COLUMNS = 50
#: Top-N keeps at most this many groups before the "Other" row.
BI_TOP_N_MAX = 50
#: Bounds on the request shape, so a query cannot be pathological before it is compiled.
BI_MAX_MEASURES = 25
BI_MAX_DIMENSIONS = 12
BI_MAX_FILTERS = 40
BI_MAX_SORTS = 8

#: The label of the collapsed Top-N remainder row. Production copy, held here
#: with the rest of the wire contract rather than in the compiler, so the
#: engine itself carries no display text (the catalogue declares no such label).
BI_TOP_N_OTHER_LABEL = "Other"

#: A catalogue member id as it arrives on the wire. Every field that names a
#: member uses this one type, so the bound cannot be present on some and missing
#: on others. Deliberately a plain ``Annotated`` alias and NOT a PEP 695 ``type``
#: statement: the latter makes pydantic emit a NAMED component and turn all six
#: fields into ``$ref``s, which would rewrite the generated client for the four
#: that were already correct. This form inlines the constraint, so the only
#: schema change is the one intended — ``maxLength`` on the list items.
BiMemberId = Annotated[str, Field(min_length=1, max_length=MEMBER_ID_MAX_LENGTH)]

BiFilterOp = Literal[
    "eq",
    "ne",
    "in",
    "not_in",
    "gt",
    "gte",
    "lt",
    "lte",
    "between",
    "is_null",
    "not_null",
    "contains",
]
BiSortDirection = Literal["asc", "desc"]
#: A filter value as it arrives on the wire. ``date`` is accepted for typed
#: clients; ISO strings are coerced by the compiler against the column type.
BiFilterValue = str | int | float | bool | date

_NO_VALUE_OPS: frozenset[str] = frozenset({"is_null", "not_null"})
_LIST_OPS: frozenset[str] = frozenset({"in", "not_in"})


class BiClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BiFilter(BiClosedModel):
    """One predicate on a catalogue dimension.

    ``values`` carries exactly what the operator needs: none for the null
    tests, two for ``between`` (low then high), one to ``BI_FILTER_IN_CAP``
    for the list operators, exactly one for everything else.
    """

    member: BiMemberId
    op: BiFilterOp
    values: list[BiFilterValue] = Field(default_factory=list, max_length=BI_FILTER_IN_CAP)

    @model_validator(mode="after")
    def _arity_matches_operator(self) -> BiFilter:
        count = len(self.values)
        if self.op in _NO_VALUE_OPS:
            if count:
                raise ValueError(f"{self.op} takes no values")
        elif self.op == "between":
            if count != 2:
                raise ValueError("between takes exactly two values: low then high")
        elif self.op in _LIST_OPS:
            if count == 0:
                raise ValueError(f"{self.op} needs at least one value")
        elif count != 1:
            raise ValueError(f"{self.op} takes exactly one value")
        return self


class BiDateRange(BiClosedModel):
    """An inclusive reporting window, named at both ends.

    A positional pair would be the smaller model but the worse contract: a
    client reading ``["2026-01-01", "2026-06-30"]`` has to know which end is
    which. It is also unrepresentable downstream — a Python tuple renders in
    OpenAPI 3.1 as an array with ``prefixItems`` and no ``items``, which the
    generated client's post-processing cannot type (it was the only such schema
    in the spec, and it failed client regeneration).
    """

    start: date
    end: date

    @model_validator(mode="after")
    def _start_is_not_after_end(self) -> BiDateRange:
        if self.start > self.end:
            raise ValueError("start must be on or before end")
        return self


class BiTime(BiClosedModel):
    """The reporting window: exactly one of ``as_of`` (a single business date)
    or ``range`` (inclusive, ``start`` through ``end``). ``compare_to`` names
    the prior period — the prior date for ``as_of``, the end of an equal-length
    prior window for ``range`` — and turns every measure into current / prior /
    delta / delta %."""

    as_of: date | None = None
    range: BiDateRange | None = None
    compare_to: date | None = None

    @model_validator(mode="after")
    def _exactly_one_window(self) -> BiTime:
        if (self.as_of is None) == (self.range is None):
            raise ValueError("time needs exactly one of as_of or range")
        return self


class BiTopN(BiClosedModel):
    """Keep the ``n`` largest groups of ``dimension`` by the first measure;
    with ``other`` the remainder collapses into one row."""

    dimension: BiMemberId
    n: int = Field(ge=1, le=BI_TOP_N_MAX)
    other: bool = True


class BiSort(BiClosedModel):
    member: BiMemberId
    direction: BiSortDirection = "asc"


class BiPivot(BiClosedModel):
    """Spread one dimension across the columns as ``<measure>|<value>``.

    ``max_columns`` is a client preference below the server cap; the compiler
    clamps it to ``BI_PIVOT_MAX_COLUMNS`` whatever is sent (D-030).
    """

    dimension: BiMemberId
    max_columns: int = Field(default=BI_PIVOT_MAX_COLUMNS, ge=1)


class BiQuery(BiClosedModel):
    measures: list[BiMemberId] = Field(min_length=1, max_length=BI_MAX_MEASURES)
    dimensions: list[BiMemberId] = Field(default_factory=list, max_length=BI_MAX_DIMENSIONS)
    filters: list[BiFilter] = Field(default_factory=list, max_length=BI_MAX_FILTERS)
    time: BiTime
    top_n: BiTopN | None = None
    sort: list[BiSort] = Field(default_factory=list, max_length=BI_MAX_SORTS)
    #: A request below the surface's row cap; the executor clamps it to the cap.
    limit: int | None = Field(default=None, ge=1)
    offset: int = Field(default=0, ge=0)
    pivot: BiPivot | None = None
    #: Emit a rollup over the dimension order with a ``__level`` marker column.
    subtotals: bool = False


# --- dashboard packs -------------------------------------------------------------------

#: Bounds on a pack, so a certified dashboard cannot be pathological and a
#: layout cannot describe a canvas no client will render.
BI_PACK_MAX_WIDGETS = 24
BI_PACK_GRID_COLUMNS = 12
BI_PACK_MAX_ROWS = 64

#: Who a certified pack is written for. One pack per audience.
BiPackAudience = Literal[
    "board",
    "alco",
    "cro",
    "credit",
    "branch_network",
    "finance",
    "compliance",
]

#: How a widget draws what it reads. ``panel`` is the kind of a widget that
#: embeds an existing platform surface rather than a query of its own.
BiWidgetKind = Literal[
    "kpi",
    "kpi_row",
    "line",
    "bar",
    "stacked_bar",
    "area",
    "donut",
    "waterfall",
    "table",
    "record_grid",
    "heatmap",
    "panel",
]

#: Platform surfaces a pack may EMBED. Closed on purpose: each key resolves
#: server-side to a surface that already carries its own authorization
#: sentence, so a pack can reference one without being able to express a
#: query, a route, a parameter or a column. Adding a key is a decision about
#: what a certified dashboard may show, taken here rather than in a pack file.
BiPanelKey = Literal[
    "credit_migration",
    "credit_vintages",
    "return_calendar",
    "attestation_status",
    "reconciliation_trust",
    "ingestion_quality",
]

#: The reporting window a pack widget asks for, RELATIVE to the bank's own
#: reporting date. A pack carries no date: a date is a parameter, and a
#: certified dashboard that pinned one would show a stale book forever.
#: :meth:`BiPackQuery.for_period` turns one of these into a ``BiTime``.
BiPackWindow = Literal[
    "as_of",
    "month_to_date",
    "quarter_to_date",
    "year_to_date",
    "trailing_3_months",
    "trailing_6_months",
    "trailing_12_months",
    "trailing_24_months",
]

#: Which earlier period a widget compares against, as an offset from the
#: window it already names.
BiPackComparison = Literal["none", "prior_month", "prior_quarter", "prior_year"]

#: Why a pack widget carries no query and no panel yet, when the gap is the
#: PLATFORM's rather than a dataset the bank has not supplied:
#:
#: * ``catalogue_member`` — the figure needs a measure or dimension the
#:   catalogue does not define, so no honest query can be written for it;
#: * ``governed_limit`` — the value is readable but its limit is governed
#:   elsewhere and is not resolved for display, and a limit line nobody
#:   governs would be a number the platform invented;
#: * ``mart_field`` — the marts carry no attribute to slice the figure by.
#:
#: It is a separate field from ``needs_data`` because the two say different
#: things to a bank. "Needs data: positions" on the dashboard of a bank that
#: pushes positions every night is a false statement about that bank's book.
BiPendingCapability = Literal["catalogue_member", "governed_limit", "mart_field"]

#: How many months each relative window spans, and how far back each
#: comparison steps. A comparison may never be SHORTER than the window it
#: compares, or the prior period would overlap the current one.
_PACK_WINDOW_MONTHS: dict[str, int] = {
    "as_of": 0,
    "month_to_date": 1,
    "quarter_to_date": 3,
    "year_to_date": 12,
    "trailing_3_months": 3,
    "trailing_6_months": 6,
    "trailing_12_months": 12,
    "trailing_24_months": 24,
}
_PACK_TRAILING_MONTHS: dict[str, int] = {
    "trailing_3_months": 3,
    "trailing_6_months": 6,
    "trailing_12_months": 12,
    "trailing_24_months": 24,
}
_PACK_COMPARISON_MONTHS: dict[str, int] = {
    "prior_month": 1,
    "prior_quarter": 3,
    "prior_year": 12,
}


def shift_months(anchor: date, months: int) -> date:
    """``anchor`` moved by whole months, on the end-of-month convention.

    A month end maps to a month end: the quarter before 30 June is 31 March,
    not 30 March. Reporting dates are overwhelmingly period ends, and a
    comparison that landed a day short of one would ask the book for a date it
    has no snapshot at and quietly render the prior column as no value. Any
    other day keeps its number, clamped to the target month's length.

    Public because the insights assembler derives its own prior period with the
    SAME convention (``app/services/bi/insights/assemble.py``). Two spellings of
    "the month before this reporting date" would let a pack widget and the
    insight strip beside it compare against different dates.
    """
    total = anchor.year * 12 + anchor.month - 1 + months
    year, month = divmod(total, 12)
    month += 1
    last = monthrange(year, month)[1]
    if anchor.day == monthrange(anchor.year, anchor.month)[1]:
        return date(year, month, last)
    return date(year, month, min(anchor.day, last))


class BiPackQuery(BiClosedModel):
    """A ``BiQuery`` with its reporting window left to the reader.

    Every field of ``BiQuery`` except ``time`` is restated here, and
    ``tests/domain/bi/test_packs.py`` pins that parity so a new query field
    cannot land on one and not the other. ``time`` is replaced by ``window``
    and ``compare``, both closed vocabularies: a pack therefore cannot name a
    date, which is the one parameter a certified dashboard must take from the
    bank it is being shown for rather than from the file it shipped in.
    """

    measures: list[BiMemberId] = Field(min_length=1, max_length=BI_MAX_MEASURES)
    dimensions: list[BiMemberId] = Field(default_factory=list, max_length=BI_MAX_DIMENSIONS)
    filters: list[BiFilter] = Field(default_factory=list, max_length=BI_MAX_FILTERS)
    window: BiPackWindow
    compare: BiPackComparison = "none"
    top_n: BiTopN | None = None
    sort: list[BiSort] = Field(default_factory=list, max_length=BI_MAX_SORTS)
    limit: int | None = Field(default=None, ge=1)
    offset: int = Field(default=0, ge=0)
    pivot: BiPivot | None = None
    subtotals: bool = False

    @model_validator(mode="after")
    def _comparison_does_not_overlap_the_window(self) -> BiPackQuery:
        if self.compare == "none":
            return self
        if _PACK_COMPARISON_MONTHS[self.compare] < _PACK_WINDOW_MONTHS[self.window]:
            raise ValueError(
                f"{self.compare} steps back less than {self.window} spans, so the prior "
                "period would overlap the current one"
            )
        return self

    def for_period(self, as_of: date) -> BiQuery:
        """This query as the compiler takes it, read at the bank's ``as_of``."""
        compare_to = (
            None
            if self.compare == "none"
            else shift_months(as_of, -_PACK_COMPARISON_MONTHS[self.compare])
        )
        if self.window == "as_of":
            time = BiTime(as_of=as_of, compare_to=compare_to)
        else:
            time = BiTime(
                range=BiDateRange(start=self._window_start(as_of), end=as_of),
                compare_to=compare_to,
            )
        return BiQuery(
            measures=list(self.measures),
            dimensions=list(self.dimensions),
            filters=list(self.filters),
            time=time,
            top_n=self.top_n,
            sort=list(self.sort),
            limit=self.limit,
            offset=self.offset,
            pivot=self.pivot,
            subtotals=self.subtotals,
        )

    def _window_start(self, as_of: date) -> date:
        if self.window == "month_to_date":
            return as_of.replace(day=1)
        if self.window == "quarter_to_date":
            return date(as_of.year, 3 * ((as_of.month - 1) // 3) + 1, 1)
        if self.window == "year_to_date":
            return date(as_of.year, 1, 1)
        months = _PACK_TRAILING_MONTHS[self.window]
        return shift_months(as_of, -months) + timedelta(days=1)


class BiWidgetDisplay(BiClosedModel):
    """Presentation only.

    No threshold, no target, no currency and no axis maximum: a number a pack
    carried would be a number nobody governs, shown beside figures that are
    governed. A limit resolves at read time from the measure's own
    ``thresholds_source`` register CODE, for the tenant being served.
    """

    show_trend: bool = False
    show_comparison: bool = False
    stacked: bool = False
    show_limit: bool = False
    #: Which of the query's own dimensions becomes the series (or slice) axis.
    series_dimension: BiMemberId | None = None
    #: Decimal places for the values shown. A rounding, not a governed figure.
    precision: int = Field(default=0, ge=0, le=6)


class BiLayoutItem(BiClosedModel):
    """One widget's place on the react-grid-layout canvas."""

    #: The widget id this item positions (react-grid-layout's own field name).
    i: str = Field(min_length=1, max_length=64)
    x: int = Field(ge=0, lt=BI_PACK_GRID_COLUMNS)
    y: int = Field(ge=0, le=BI_PACK_MAX_ROWS)
    w: int = Field(ge=1, le=BI_PACK_GRID_COLUMNS)
    h: int = Field(ge=1, le=BI_PACK_MAX_ROWS)
    min_w: int | None = Field(default=None, ge=1, le=BI_PACK_GRID_COLUMNS)
    min_h: int | None = Field(default=None, ge=1, le=BI_PACK_MAX_ROWS)
    static: bool = False

    @model_validator(mode="after")
    def _fits_the_canvas(self) -> BiLayoutItem:
        if self.x + self.w > BI_PACK_GRID_COLUMNS:
            raise ValueError(f"{self.i} runs past column {BI_PACK_GRID_COLUMNS}")
        if self.y + self.h > BI_PACK_MAX_ROWS:
            raise ValueError(f"{self.i} runs past row {BI_PACK_MAX_ROWS}")
        if self.min_w is not None and self.min_w > self.w:
            raise ValueError(f"{self.i} is narrower than its own minimum width")
        if self.min_h is not None and self.min_h > self.h:
            raise ValueError(f"{self.i} is shorter than its own minimum height")
        return self


class BiPackWidget(BiClosedModel):
    """One tile of a certified pack.

    A widget reads through exactly one of two doors, or through neither:

    * ``query`` — a closed ``BiPackQuery`` of catalogue members;
    * ``panel`` — a closed key naming a platform surface that already carries
      its own authorization sentence;
    * neither, when the figure cannot honestly be produced yet. Such a widget
      still SHIPS, and names what it is waiting for in ``needs_data`` (a
      dataset the bank has not supplied) or ``pending_capability`` (platform
      work), or both. It renders as a named gap and never as a zero.

    ``needs_data`` is also carried by widgets that DO have a query but whose
    figure degrades to "—" when a dataset is absent: days past due is the
    worked example, where a missing column would otherwise read as a clean
    book. It is a dataset KEY, never prose — the human label and the Data
    Engine template link belong to the client, which is also what keeps the
    file free of jurisdiction-specific wording.
    """

    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    kind: BiWidgetKind
    title: str = Field(min_length=1, max_length=120)
    caption: str = Field(default="", max_length=280)
    query: BiPackQuery | None = None
    panel: BiPanelKey | None = None
    display: BiWidgetDisplay = Field(default_factory=BiWidgetDisplay)
    needs_data: str | None = Field(default=None, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    pending_capability: BiPendingCapability | None = None

    @model_validator(mode="after")
    def _reads_through_one_door(self) -> BiPackWidget:
        if self.query is not None and self.panel is not None:
            raise ValueError(f"{self.id} names both a query and a panel; it may name one")
        if (
            self.query is None
            and self.panel is None
            and self.needs_data is None
            and self.pending_capability is None
        ):
            raise ValueError(
                f"{self.id} has no query and no panel, so it must name what it is "
                "waiting for in needs_data or pending_capability"
            )
        if self.panel is not None:
            if self.kind != "panel":
                raise ValueError(f"{self.id} embeds a panel, so its kind must be panel")
            if self.needs_data is not None or self.pending_capability is not None:
                raise ValueError(f"{self.id} embeds a panel, which carries its own empty state")
            if self.display.series_dimension is not None:
                raise ValueError(f"{self.id} embeds a panel and cannot name a series dimension")
            if self.display.show_comparison or self.display.show_limit or self.display.stacked:
                raise ValueError(f"{self.id} embeds a panel and cannot restyle it")
        elif self.kind == "panel":
            raise ValueError(f"{self.id} is of kind panel but embeds no panel")
        if self.display.show_comparison and (self.query is None or self.query.compare == "none"):
            raise ValueError(f"{self.id} shows a comparison but its query names no prior period")
        if self.display.show_limit and self.query is None:
            raise ValueError(f"{self.id} shows a limit but reads no measure to resolve it from")
        series = self.display.series_dimension
        if series is not None and (self.query is None or series not in self.query.dimensions):
            raise ValueError(f"{self.id} draws a series by {series}, which it does not group by")
        return self

    @property
    def is_gap(self) -> bool:
        """Whether this widget renders as a named gap rather than a figure."""
        return self.query is None and self.panel is None


class BiPackSpec(BiClosedModel):
    """A certified dashboard pack: its widgets and where they sit.

    Saved dashboards use the same shape, so an editable copy of a pack is
    simply a copy. Nothing here can name a table, a column, a route or a
    parameter: every model is closed, the only query is a ``BiPackQuery`` of
    catalogue member ids, and ``panel`` is a closed key.
    """

    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=400)
    audience: BiPackAudience
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    widgets: list[BiPackWidget] = Field(min_length=1, max_length=BI_PACK_MAX_WIDGETS)
    layout: list[BiLayoutItem] = Field(min_length=1, max_length=BI_PACK_MAX_WIDGETS)

    @model_validator(mode="after")
    def _every_widget_is_positioned_exactly_once(self) -> BiPackSpec:
        widget_ids = [widget.id for widget in self.widgets]
        if len(widget_ids) != len(set(widget_ids)):
            raise ValueError("widget ids must be unique within a pack")
        positioned = [item.i for item in self.layout]
        if len(positioned) != len(set(positioned)):
            raise ValueError("a widget may be positioned only once")
        if set(positioned) != set(widget_ids):
            missing = sorted(set(widget_ids) - set(positioned))
            unknown = sorted(set(positioned) - set(widget_ids))
            raise ValueError(
                f"the layout must position every widget and nothing else; "
                f"unpositioned: {missing}, unknown: {unknown}"
            )
        return self

    def widget(self, widget_id: str) -> BiPackWidget:
        for widget in self.widgets:
            if widget.id == widget_id:
                return widget
        raise KeyError(widget_id)


# --- results ---------------------------------------------------------------------------

BiResultColumnKind = Literal["dimension", "measure", "marker"]
BiComparisonRole = Literal["current", "prior", "delta", "delta_pct"]
BiTrustStatus = Literal["green", "amber", "red", "grey"]

#: How a client renders a result column: the CATALOGUE's value-type vocabulary
#: (``app/domain/bi/catalogue/members.py::VALUE_TYPES``) plus ``int`` for the
#: subtotal marker, which is a row level rather than a figure.
#:
#: Named as a Literal rather than left as ``str`` (audit A8-11). The untyped
#: version is why retiring the old catch-all ``ratio`` type broke four consumers
#: in silence: nothing on the wire said what the vocabulary was, so no format map
#: could be held to it and every one of them fell through to text (D-189). It is
#: restated here rather than imported because ``app/schemas`` may not import
#: ``app.domain.bi`` — the plane boundary deliberately keeps this file in its scan
#: — and ``tests/services/bi/test_compiler_calculated.py`` asserts the two sides
#: equal in BOTH directions, the same way ``BiExportFormat`` is held to the
#: renderers it names.
BiResultColumnFormat = Literal[
    "amount",
    "pct",
    "fraction",
    "index",
    "duration_years",
    "count",
    "text",
    "date",
    "flag",
    "int",
]


class BiResultColumn(BiClosedModel):
    """One output column: which member it came from and how to format it."""

    id: str
    label: str
    kind: BiResultColumnKind
    #: The catalogue value type the member declared, or ``int`` for the subtotal
    #: marker column. A ``fraction`` is NOT pre-scaled and a ``pct`` already is
    #: (D-189), so a renderer must read this rather than guess from the number.
    format: BiResultColumnFormat
    member_id: str | None = None
    #: Set on comparison columns; ``None`` when the query has no ``compare_to``.
    role: BiComparisonRole | None = None
    #: The pivoted dimension value this column carries, as text.
    pivot_value: str | None = None


class BiTrustBadge(BiClosedModel):
    """Placeholder for the reconciliation verdict wave 3 attaches to a result.

    ``grey`` is "not assessed" and is what a result carries until the
    reconciliation summary is wired in; a missing check is never green.
    """

    status: BiTrustStatus = "grey"
    failing_checks: list[str] = Field(default_factory=list)


class BiQueryResult(BiClosedModel):
    columns: list[BiResultColumn]
    rows: list[list[Any]]
    truncated: bool
    elapsed_ms: int
    used_aggregate: bool
    trust: BiTrustBadge = Field(default_factory=BiTrustBadge)
    catalogue_version: str
    #: The mart build fingerprint the rows were read from; wave 3 fills it.
    build_fingerprint: str | None = None


# --- route envelopes -------------------------------------------------------------------

# The catalogue's own vocabularies (module, sensitivity, measure kind,
# aggregation, time behaviour, value type, grain, favourable direction,
# advisory designation) travel as TEXT, exactly as ``BiResultColumn.format``
# does. ``app/schemas`` may not import ``app.domain.bi`` — the plane boundary
# scans everything outside the BI-owned paths — so restating each Literal here
# would be a second copy free to drift from the catalogue that owns it.
# ``tests/api/test_bi_routes.py`` pins the values a route can emit.


class BiPagedQueryRequest(BiClosedModel):
    """A query plus the page of rows wanted, as AG Grid's row models ask for it.

    ``start_row`` / ``end_row`` (exclusive) are the page; the server clamps the
    span to the surface's cap. The query's own ``limit`` / ``offset`` are
    refused here rather than silently overridden: two paging mechanisms in one
    request cannot both be honoured, and quietly picking one would page a
    record grid through data the caller did not ask for.
    """

    query: BiQuery
    start_row: int = Field(default=0, ge=0)
    end_row: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _one_paging_mechanism(self) -> BiPagedQueryRequest:
        if self.end_row is not None and self.end_row <= self.start_row:
            raise ValueError("end_row must be greater than start_row")
        if self.query.limit is not None or self.query.offset:
            raise ValueError("page this surface with start_row and end_row, not limit and offset")
        return self


class BiExplainRequest(BiClosedModel):
    """One measure of a query, and the query it is read in.

    Provenance is a property of a figure IN CONTEXT: the same measure explained
    at a different as-of, or against the aggregate table rather than the fact
    table, has a different answer. ``measure`` must be one of the query's own
    measures.
    """

    query: BiQuery
    measure: str = Field(min_length=1, max_length=120)


class BiCatalogueValueRead(BiClosedModel):
    """One value of a closed dimension vocabulary, with its production label."""

    code: str
    label: str


class BiCatalogueMeasureRead(BiClosedModel):
    """A measure the CALLER may read, and everything the UI needs to badge it."""

    id: str
    label: str
    description: str
    module: str
    sensitivity: str
    measure_kind: str
    aggregation: str
    time_behaviour: str
    value_type: str
    grain: str
    allowed_dimensions: list[str]
    favourable_direction: str
    #: Register / parameter CODE a limit resolves from — never a number.
    thresholds_source: str | None = None
    #: The reconciliation checks whose status governs this measure's trust badge.
    reconciliation_checks: list[str] = Field(default_factory=list)
    #: ``filed`` / ``advisory_only`` / ``supervisory_monitoring`` / ``unregistered``
    #: for an engine copy; ``None`` for a portfolio measure computed from the marts.
    advisory_designation: str | None = None
    #: True only for a filed figure copied from the sealed tier (D-022). Nothing
    #: else may be shown as platform-certified.
    certified: bool = False
    #: Set when the figure is a COPY of an engine metric rather than a mart
    #: computation: the engine's own metric id and which tier it was copied from.
    engine_metric_id: str | None = None
    engine_tier: str | None = None


class BiCatalogueDimensionRead(BiClosedModel):
    """A dimension the CALLER may group, filter, pivot or sort by."""

    id: str
    label: str
    description: str
    module: str
    sensitivity: str
    value_type: str
    values: list[BiCatalogueValueRead] = Field(default_factory=list)


class BiCatalogueHierarchyRead(BiClosedModel):
    """A drill path, reduced to the levels the caller may see.

    A hierarchy that crosses from an aggregate level into a named one (obligor
    type → group → obligor) is listed with the levels the principal holds and
    no others, so a drill the UI offers is a drill the query path will serve.
    """

    id: str
    label: str
    levels: list[str]


class BiCatalogueRead(BiClosedModel):
    """The catalogue as one caller may query it.

    Filtered by the same authorization the query path applies, so nothing here
    can be selected and then refused. ``withheld_members`` is the count the
    principal has no sentence for — a number, never the ids — so the UI can say
    that more fields exist without naming them.
    """

    version: str
    measures: list[BiCatalogueMeasureRead]
    dimensions: list[BiCatalogueDimensionRead]
    hierarchies: list[BiCatalogueHierarchyRead]
    withheld_members: int = 0


class BiGridRowRead(BiClosedModel):
    """One grid row: cells keyed by column id, plus how to render it."""

    values: dict[str, Any]
    #: How many dimensions the row grouped; ``None`` without subtotals, ``0``
    #: for the grand total.
    level: int | None = None
    subtotal: bool = False


class BiGridPageRead(BiClosedModel):
    """One page of grid rows, with the columns it is keyed by."""

    columns: list[BiResultColumn]
    rows: list[BiGridRowRead]
    #: Row-group columns, in order: the deepest indent a subtotal row can take.
    dimension_count: int
    start_row: int
    #: The total row count once a page proves to be the last; ``None`` while more
    #: rows may exist, which is what keeps the grid paging.
    last_row: int | None = None
    truncated: bool
    elapsed_ms: int
    used_aggregate: bool
    trust: BiTrustBadge = Field(default_factory=BiTrustBadge)
    catalogue_version: str
    build_fingerprint: str | None = None


class BiTrustCheckRead(BiClosedModel):
    """One reconciliation check's current verdict for a (bank, as-of)."""

    check_id: str
    label: str
    status: BiTrustStatus
    lhs: Decimal | None = None
    rhs: Decimal | None = None
    difference: Decimal | None = None
    tolerance: Decimal | None = None
    #: The check's own evidence (counts, lines, the reason it could not be
    #: assessed). Never a row of the book.
    detail: dict[str, Any] = Field(default_factory=dict)
    evaluated_at: datetime | None = None


class BiBuildRead(BiClosedModel):
    """One mart build for the date, so a badge can say WHY it is not green."""

    scope: str
    status: str
    fingerprint: str
    finished_at: datetime | None = None
    row_counts: dict[str, int] = Field(default_factory=dict)


class BiTrustRead(BiClosedModel):
    """Every reconciliation check for a (bank, as-of), and the builds behind them.

    A check with no stored result is ``grey`` — "not assessed" — and never reads
    as a pass.
    """

    as_of: date
    status: BiTrustStatus
    checks: list[BiTrustCheckRead]
    builds: list[BiBuildRead] = Field(default_factory=list)
    build_fingerprint: str | None = None


class BiExplainComponentRead(BiClosedModel):
    """A measure the explained figure is composed from, and its part."""

    role: Literal["numerator", "denominator", "weight", "over"]
    member_id: str
    label: str


class BiExplainEngineRead(BiClosedModel):
    """The engine row a certified figure is a COPY of, exactly as copied.

    ``input_hash`` is the engine's own value-based hash of the facts it computed
    from: it is how a figure on a dashboard is tied back to the run that
    produced it, and to the return that was filed from that run.
    """

    metric_id: str
    module: str
    tier: str
    #: The regime the copy was taken under, and the date of the row quoted.
    regime: str | None = None
    as_of: date | None = None
    #: The engine's own figure and unit, so a dashboard number can be compared
    #: with the row it was copied from.
    value: Decimal | None = None
    unit: str | None = None
    input_hash: str | None = None
    engine_version: str | None = None
    pipeline_state: str | None = None
    status: str | None = None
    advisory_designation: str | None = None
    reconciliation_blocked: bool | None = None
    computed_at: datetime | None = None
    #: Present for the sealed tier: the run and reporting period the copy came from.
    run_id: UUID | None = None
    reporting_period_id: UUID | None = None


class BiExplainRead(BiClosedModel):
    """Where one figure came from: its definition, its source and its checks.

    No SQL: the statement encodes the mart layout and is not a bank-facing
    surface. What a reviewer gets instead is the measure's own declaration, the
    mart table and tier the value was read from, the window it was read over,
    the engine metric and input hash when the figure is a copy, and the current
    status of every reconciliation check that governs it.
    """

    measure: BiCatalogueMeasureRead
    components: list[BiExplainComponentRead] = Field(default_factory=list)
    #: The mart table the value is read from, and whether the daily aggregate
    #: answered it instead of the fact grain.
    source_table: str
    used_aggregate: bool
    #: ``derivation`` or ``classification`` (D-015) for a mart measure; ``None``
    #: when the figure is an engine copy and carries the engine's own rule.
    fx_rule: str | None = None
    as_of: date | None = None
    window_start: date | None = None
    window_end: date | None = None
    compare_to: date | None = None
    engine: BiExplainEngineRead | None = None
    checks: list[BiTrustCheckRead] = Field(default_factory=list)
    trust: BiTrustBadge = Field(default_factory=BiTrustBadge)
    catalogue_version: str
    build_fingerprint: str | None = None


# --- governed exports (T3; ``docs/bi.md`` §Exports) --------------------------------------
#
# One contiguous block, added at the end so the pack schemas above keep their
# place. Every name is unique across ``app/schemas`` (the OpenAPI component-key
# hazard in AGENTS.md), and the format vocabulary is asserted against
# ``app.services.bi.exports.EXPORT_FORMATS`` by a test rather than restated with
# a comment — the renderer that implements a format and the name a client asks
# for it by must not drift.

BiExportFormat = Literal["csv", "xlsx", "pdf"]
#: ``summary`` needs ``view``; ``record_level`` — which covers the spec's
#: "record-level or confidential" — additionally needs ``export``. The SERVER
#: decides which a query is, from the catalogue's sensitivity declarations; this
#: value is reported back, never accepted.
BiExportClass = Literal["summary", "record_level"]
#: ``ready`` — the file is attached to this response. ``queued`` / ``running`` —
#: it is being produced by the ``bi_export`` job. ``failed`` / ``denied`` — it
#: will not be.
BiExportState = Literal["ready", "queued", "running", "failed", "denied"]


class BiExportRequest(BiClosedModel):
    """One query, and the artifact wanted from it.

    There is deliberately no "this is confidential" flag: the disclosure class is
    read from the catalogue members the query touches, because a flag on the
    request body would put the classification in the hands of the party it
    constrains.
    """

    query: BiQuery
    format: BiExportFormat


class BiExportRead(BiClosedModel):
    """A queued or finished export, as its owner sees it.

    ``download_url`` is a short-lived presigned link and is minted when this is
    read, never stored: a URL persisted in a table is a bearer credential that
    outlives the request it was issued for.
    """

    job_id: UUID
    state: BiExportState
    format: BiExportFormat
    export_class: BiExportClass
    #: Production copy explaining the state, suitable for display as-is.
    message: str
    filename: str | None = None
    media_type: str | None = None
    row_count: int | None = None
    #: The export hit the server row cap; the file holds this many rows, not all.
    truncated: bool = False
    size_bytes: int | None = None
    checksum_sha256: str | None = None
    download_url: str | None = None
    download_expires_in_seconds: int | None = None
    trust: BiTrustBadge = Field(default_factory=BiTrustBadge)
    catalogue_version: str
    build_fingerprint: str | None = None


# --- resolved content packs (T7; ``docs/bi.md`` §Phase 2 Content packs) -------------------
#
# ``BiPackSpec`` above is the FILE: what shipped in the repository, with no date
# and no reader. These models are what one reader is served for one reporting
# date — the same widgets, each resolved through ``BiPackQuery.for_period`` and
# each authorized on its own. Two properties are structural rather than
# conventional:
#
# * a refused widget is a model that CANNOT carry a title, a caption, a measure,
#   a dimension, a filter or a figure, because every one of those fields is
#   omitted for it (the client's ``RestrictedWidget`` accepts only a height —
#   anything else sent here would be a disclosure the reader was refused);
# * a pack carries no date of its own and every ``BiQuery`` in the response was
#   produced by ``for_period(as_of)``, so a certified dashboard can never show a
#   date the caller did not ask for.

#: Whether the reader is being shown this widget (or pack) or refused it.
#: ``restricted`` is the only value a refusal takes, and it names nothing.
BiPackAccess = Literal["granted", "restricted"]


class BiPackWidgetRead(BiClosedModel):
    """One widget of a pack resolved for one reader and one reporting date.

    A ``granted`` widget carries everything the file declared plus its query
    resolved for the date. A ``restricted`` widget carries its id and its place
    on the canvas and NOTHING else: not the title, not the caption, not the
    measure, not the dimension, not the filter, not a figure. A denial that
    names what was hidden is a disclosure — "you may not see the largest
    single-name share" tells the reader the institution tracks one — so the
    refusal is a model with nothing in it to leak.
    """

    id: str = Field(min_length=1, max_length=64)
    #: Where the widget sits. The layout item's ``i`` is the widget id, so this
    #: is the one thing a refusal may carry: geometry, which the file already
    #: published and which holds the dashboard's shape together.
    layout: BiLayoutItem
    access: BiPackAccess
    kind: BiWidgetKind | None = None
    title: str | None = None
    caption: str | None = None
    #: The file's query resolved for the requested date. ``None`` for a panel, a
    #: named gap, or a refusal.
    query: BiQuery | None = None
    panel: BiPanelKey | None = None
    display: BiWidgetDisplay | None = None
    #: A Data Engine dataset the institution has not supplied. Passed through
    #: from the file unchanged: it is a dataset KEY, and the client names it.
    needs_data: str | None = None
    #: Platform work the figure is waiting on. Never collapsed into
    #: ``needs_data``: telling a bank it needs data it pushes every night is a
    #: false statement about its own book.
    pending_capability: BiPendingCapability | None = None


class BiPackRead(BiClosedModel):
    """A certified dashboard, resolved for one institution, date and reader."""

    id: str
    title: str
    description: str
    audience: BiPackAudience
    version: str
    #: The reporting date every query in this response was resolved for.
    as_of: date
    access: BiPackAccess
    #: Production copy stating what the reader is looking at, shown as-is. It is
    #: never empty: a dashboard whose every figure was refused must not read as a
    #: dashboard with nothing to show.
    message: str
    widgets: list[BiPackWidgetRead]
    #: How many widgets were refused, and how many could have shown a figure.
    #: A count is not a disclosure; a name would be.
    restricted_widgets: int = 0
    readable_widgets: int = 0
    catalogue_version: str


class BiPackListRead(BiClosedModel):
    """Every certified dashboard this institution and reader may open."""

    as_of: date
    packs: list[BiPackRead]
    catalogue_version: str


# --- insights (T7; ``docs/bi.md`` §Phase 2 Insights layer) -------------------------------
#
# ``BiInsightRead`` mirrors ``app/services/bi/insights/statements.py::Insight``
# field for field; the dashboard's ``components/bi/types.ts::BiInsight`` mirrors
# the same shape in camelCase. Nothing here is recomputed by a client: the
# figures are already rendered into ``headline`` and ``detail`` in the
# institution's own unit, with the reservations already attached, because a
# browser that re-rounded a capital ratio would change what the sentence says.

#: The kind of statement. Mirrors ``statements.StatementClass``.
BiStatementClass = Literal["movement", "attribution", "projection", "data_gap", "trust_notice"]
#: Whether the move was good for the bank. Mirrors ``drivers.Favourability``.
BiFavourability = Literal["favourable", "adverse", "neutral"]
#: How prominently the statement is shown. Mirrors ``statements.Emphasis``.
BiEmphasis = Literal["high", "normal", "low"]


class BiInsightRead(BiClosedModel):
    """One statement the platform is prepared to make about a reporting date."""

    id: str
    rule_id: str
    statement_class: BiStatementClass
    headline: str
    detail: str
    as_of: date
    measure_ids: list[str]
    #: Stable, value-derived fact keys — the citation for the statement. Never
    #: the volatile fact ids (``insights/facts.py``).
    evidence: list[str]
    favourability: BiFavourability
    emphasis: BiEmphasis
    #: Reservations that qualify the statement: advisory basis, trust state, a
    #: gap in the data. Rendered as given.
    qualifiers: list[str]
    #: Only a filed engine figure under a green badge may be shown as certified.
    certified: bool
    advisory_designation: str | None = None
    trust: BiTrustBadge = Field(default_factory=BiTrustBadge)


class BiInsightsRead(BiClosedModel):
    """What the platform will say about one institution at one date.

    An empty ``insights`` list is a real answer — a date whose figures moved
    within the materiality threshold honestly has nothing to report — which is
    why ``measures_read`` is beside it: zero measures read and zero insights is
    "nothing has been computed", and the client must not render that as "nothing
    stands out".
    """

    as_of: date
    #: The earlier date every movement in this set is measured against.
    compare_to: date
    insights: list[BiInsightRead]
    #: The value-based fingerprint of the facts these statements were derived
    #: from (``insights/digest.py``), so two strips can be told apart.
    fact_sheet_hash: str
    #: The presentation cap was reached and some statements are not shown.
    truncated: bool = False
    #: How many headline measures were actually read for this date.
    measures_read: int = 0
    #: How many were withheld because the reader's access does not cover them.
    #: A count only: an insight the reader may not see is not named, and neither
    #: is its measure.
    measures_withheld: int = 0
    trust: BiTrustBadge = Field(default_factory=BiTrustBadge)
    catalogue_version: str
    build_fingerprint: str | None = None
