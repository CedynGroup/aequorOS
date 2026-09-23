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

from datetime import date, datetime
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


# --- results ---------------------------------------------------------------------------

BiResultColumnKind = Literal["dimension", "measure", "marker"]
BiComparisonRole = Literal["current", "prior", "delta", "delta_pct"]
BiTrustStatus = Literal["green", "amber", "red", "grey"]


class BiResultColumn(BiClosedModel):
    """One output column: which member it came from and how to format it."""

    id: str
    label: str
    kind: BiResultColumnKind
    #: The catalogue value type (``amount`` / ``pct`` / ``ratio`` / ``count`` /
    #: ``text`` / ``date`` / ``flag``) or ``int`` for marker columns.
    format: str
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
