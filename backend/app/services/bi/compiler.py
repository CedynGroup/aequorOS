"""The BI compiler: a ``BiQuery`` becomes ONE SQLAlchemy Core ``select``.

Every member id resolves through the catalogue to a mapped column of
``app.models.bi``; every value the request carries is bound as a parameter;
the tenant predicates come from the CALLER, never from the query; and the
statement text contains nothing the client sent — not a member id, not a
filter value, not a sort key. Column aliases are positional (``k0``, ``m1``),
so a compiled statement is the same shape whoever asks. There is no ``text()``,
no ``literal_column`` and no string assembly anywhere in this package
(``tests/architecture/test_bi_compiler_injection.py``); the two constant
settings statements the executor issues live in ``execution.py`` and are
allow-listed there by name.

What a query compiles to, in order:

1. **Resolution.** Ids → ``MemberDef``; hierarchy ids expand to their levels;
   an id the catalogue does not know, or whose ``(table, column)`` is not a
   mapped column, is ``UnknownMember`` BEFORE anything else is looked at, so
   the member list is the only thing a probe can learn.
2. **Shape checks.** One fact table per query, one time behaviour, every
   dimension allowed by every measure, sort keys drawn from the requested
   members only, at most one concentration dimension, no pivot over a
   concentration measure.
3. **Source.** The fact table joined (LEFT OUTER, on org + bank + key) to the
   conformed dimensions the members need. When every measure is an additive
   sum / count — or a ratio of such — over columns ``bi_agg_position_daily``
   carries, and every dimension and filter is in its grain, the aggregate
   table is the source and ``used_aggregate`` is set; the numbers are the
   same by construction (the aggregate is a per-grain sum of the fact).
4. **Measures.** Each measure is a set of additive COMPONENTS (``sum`` /
   ``count`` of a ``CASE WHEN <row filters> THEN column END``) and a
   finalizer: a ratio is ``sum(num) / nullif(sum(den), 0)`` — a ratio of sums,
   never an average of ratios, and NULL when the denominator is NULL or zero;
   a weighted average is ``sum(w·x) / nullif(sum(w), 0)`` over the rows that
   carry a value; concentration measures (``hhi``, ``top_n_share``) take a
   second grouping level over the ``over`` dimension. Row filters sit INSIDE
   the aggregate so measures with different filters share one scan.

   **NULL versus zero** is one rule on both sources. A measure's row filters
   split into its POPULATION (the position type, the engine copy's identity,
   the pivot value) and its SELECTION (non-performing, a band, restructured):
   ``sum(CASE WHEN population THEN CASE WHEN selection THEN x ELSE 0 END END)``.
   A group with no loans has a NULL non-performing exposure — there is
   nothing to measure — while a group of loans none of which is
   non-performing has 0, and an NPL ratio of 0 %, not "—". A NULL VALUE
   (an unconverted foreign-currency balance under D-015) stays NULL on
   either path: the column is NULL, not the predicate. Counts are 0 over an
   empty population by SQL's own rule, and the aggregate path coalesces its
   summed counts to match.

   **The third case is D-042:** a selection column that is NULL for EVERY row
   in the population was never supplied, and "not selected" is then a lie. A
   bank that sent no ``days_past_due`` has ``dpd_band`` NULL on every loan, and
   ``loans.par_90_pct`` rendered ``0.00 %`` — a clean book (audit A5-02). So
   each measure whose selection reads a NULLABLE column also aggregates an
   ANSWERABLE count — population rows where that column is not NULL — and is
   NULL when the count is 0. A populated column that simply matches nothing
   still gives a genuine 0 (a book with no 90+ loans really is 0 %), and for a
   RATIO the denominator is restricted to the answerable rows too, so the
   figure is a rate over the rows it could be measured on rather than one that
   silently counts unknowns as current. ``_PRECOMPUTED_SELECTION_COLUMNS``
   names the two exceptions and why.
5. **Time.** ``as_of`` is equality on the fact's date column. ``range`` is
   BETWEEN for flow measures; for stock measures it is the last date with
   data per requested time grain, read from ``bi_dim_date`` (D-014), so a
   monthly series of a balance is the month-end book, not a sum of days.
6. **Top-N.** The N largest groups by the first measure come from a ranked
   subquery; the rest collapse into one row whose key is the "Other" label.
7. **Pivot** (D-030). The pivot dimension's values are fetched first by a
   capped query; each measure becomes one conditional aggregate per value,
   emitted as ``<measure>|<value>``.
8. **Subtotals.** ``GROUP BY ROLLUP`` on Postgres with a ``__level`` marker
   (``grouping()`` arithmetic); a UNION ALL of the levels on SQLite, which
   has no ROLLUP — the hermetic suite proves the shape, ``tests/db`` the
   Postgres path.
9. **Comparison.** ``compare_to`` widens the window to both periods and
   aggregates every measure component twice — under the current window's
   predicate and under the prior's — in the SAME grouped select (a period
   axis, like the pivot; never a join of two grouped results, which Postgres
   cannot FULL JOIN on ``IS NOT DISTINCT FROM``), emitting current / prior /
   delta / delta % per measure column; delta % is NULL when prior is NULL
   or zero.

Limits and offsets are NOT applied here: the executor clamps the request to
the surface's row cap and asks for one row more to detect truncation (S13).
"""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, Literal, cast, get_args
from uuid import UUID

from loguru import logger
from sqlalchemy import (
    Boolean,
    CompoundSelect,
    Date,
    DateTime,
    Float,
    Integer,
    Numeric,
    Select,
    String,
    Table,
    Text,
    Uuid,
    and_,
    case,
    func,
    literal,
    not_,
    null,
    or_,
    select,
    true,
    union_all,
)
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement, FromClause
from sqlalchemy.sql.expression import cast as sql_cast

from app.core.config import get_settings
from app.db.base import Base
from app.domain.bi import expr
from app.domain.bi.catalogue import (
    Catalogue,
    DimensionDef,
    HierarchyDef,
    MeasureDef,
    MemberDef,
)
from app.domain.bi.catalogue import UnknownMember as CatalogueUnknownMember
from app.domain.bi.catalogue.dimensions import DATE_TABLE, POSITION_TABLE
from app.domain.bi.catalogue.members import RowFilter
from app.models.bi import (
    BiAggPositionDaily,
    BiDimBranch,
    BiDimCounterparty,
    BiDimDate,
    BiDimGlAccount,
    BiDimProduct,
    BiFactEngineMetric,
    BiFactGlBranchMonthly,
    BiFactGlMonthly,
    BiFactLoanEvent,
    BiFactPositionDaily,
    BiFactPositionEom,
    BiFactTarget,
)
from app.models.bi_content import BiMeasure
from app.schemas.bi import (
    BI_MAX_MEASURES,
    BI_PIVOT_MAX_COLUMNS,
    BI_TOP_N_OTHER_LABEL,
    BiComparisonRole,
    BiFilter,
    BiFilterValue,
    BiPivot,
    BiQuery,
    BiResultColumnFormat,
    BiTime,
    BiTopN,
)
from app.services.bi import execution
from app.services.bi.errors import InvalidQuery, UnknownMember

__all__ = [
    "MAX_PERIODS_PER_QUERY",
    "ColumnSpec",
    "CompiledQuery",
    "aggregate_table_covers",
    "compile_query",
    "expand_calculated_measures",
]

#: Most distinct periods one compiled statement may carry. Every period is a
#: second aggregation of every measure component, so the statement grows with
#: this number; a formula that reads its figures at eight different periods is
#: past anything a person composes and well inside what the retention bound
#: already allows (D-196 caps the REACH; this caps how many points inside it one
#: formula may name).
MAX_PERIODS_PER_QUERY = 8

# --- the mapped plane ------------------------------------------------------------------

#: Every table a catalogue member may bind to, by name. A member naming any
#: other table — or a column these tables do not carry — is ``UnknownMember``.
_TABLES: dict[str, Table] = {
    name: Base.metadata.tables[name]
    for name in (
        BiFactPositionDaily.__tablename__,
        BiFactPositionEom.__tablename__,
        BiAggPositionDaily.__tablename__,
        BiFactLoanEvent.__tablename__,
        BiFactGlMonthly.__tablename__,
        BiFactGlBranchMonthly.__tablename__,
        BiFactEngineMetric.__tablename__,
        BiFactTarget.__tablename__,
        BiDimBranch.__tablename__,
        BiDimProduct.__tablename__,
        BiDimCounterparty.__tablename__,
        BiDimGlAccount.__tablename__,
        BiDimDate.__tablename__,
    )
}
_AGG_TABLE = BiAggPositionDaily.__tablename__

#: The business-date column of each fact.
_FACT_DATE_COLUMN: dict[str, str] = {
    BiFactPositionDaily.__tablename__: "as_of_date",
    BiFactPositionEom.__tablename__: "as_of_date",
    _AGG_TABLE: "as_of_date",
    BiFactLoanEvent.__tablename__: "event_date",
    BiFactGlMonthly.__tablename__: "month_end",
    BiFactGlBranchMonthly.__tablename__: "month_end",
    BiFactEngineMetric.__tablename__: "as_of_date",
    BiFactTarget.__tablename__: "as_of_date",
}

#: Conformed dimension → (fact key column, dimension key column). ``bi_dim_date``
#: joins on the fact's own date column and is handled separately.
_DIM_JOIN_KEYS: dict[str, tuple[str, str]] = {
    BiDimBranch.__tablename__: ("branch_code", "branch_code"),
    BiDimProduct.__tablename__: ("product_code", "product_code"),
    BiDimCounterparty.__tablename__: ("counterparty_id", "counterparty_id"),
    BiDimGlAccount.__tablename__: ("gl_account_code", "account_code"),
}

#: ``bi_agg_position_daily``: fact ``sum`` column → aggregate column.
_AGG_SUM_COLUMNS: dict[str, str] = {
    "balance_rc": "balance_rc_sum",
    "classification_exposure_rc": "classification_exposure_rc_sum",
    "provision_required_rc": "provision_required_rc_sum",
    "provision_held_rc": "provision_held_rc_sum",
    "collateral_rc": "collateral_rc_sum",
}
#: The aggregate's grain, i.e. the only fact-local columns a query over it may
#: group, filter or pivot by.
_AGG_GRAIN: frozenset[str] = frozenset(
    {
        "position_type",
        "product_family",
        "branch_code",
        "currency",
        "ifrs9_stage",
        "dpd_band",
        "grade",
        "deposit_account_type",
    }
)
#: Row filters the aggregate absorbs into a dedicated measure column.
_AGG_NON_PERFORMING = RowFilter("non_performing", "is_true")
_AGG_UNCONVERTED = RowFilter("fx_unconverted", "is_true")
#: Members whose catalogue column lives on a dimension table but is carried
#: verbatim on the aggregate's grain.
_AGG_OVERRIDES: dict[tuple[str, str], str] = {
    (BiDimProduct.__tablename__, "product_family"): "product_family",
}

_ADDITIVE: frozenset[str] = frozenset({"sum", "count", "flow_sum"})
_RATIO: frozenset[str] = frozenset({"ratio_of_sums", "share"})
_CONCENTRATION: frozenset[str] = frozenset({"top_n_share", "hhi"})
_ORDERED_OPS: frozenset[str] = frozenset({"gt", "gte", "lt", "lte", "between"})

_LEVEL_MARKER = "__level"
_PIVOT_SEPARATOR = "|"


# --- output -------------------------------------------------------------------------------


#: Every format a result column may declare, as a SET, for checking a value that
#: arrives as text (a calculated measure's ``value_type`` comes off a row).
_RESULT_FORMATS: frozenset[str] = frozenset(get_args(BiResultColumnFormat))


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    """One column of the result: its wire id and how the client renders it."""

    id: str
    label: str
    kind: Literal["dimension", "measure", "marker"]
    #: A catalogue value type, or ``int`` for the marker column.
    format: BiResultColumnFormat
    member_id: str | None = None
    role: BiComparisonRole | None = None
    pivot_value: str | None = None


@dataclass(frozen=True)
class CompiledQuery:
    """A compiled statement plus everything the executor and the log need."""

    select: Select[Any]
    columns: tuple[ColumnSpec, ...]
    organization_id: str
    bank_id: str
    fact_table: str
    used_aggregate: bool
    #: Every catalogue member the statement reads — measures, their composed
    #: measures, dimensions, filter members (injected ones included), the
    #: Top-N, pivot and concentration dimensions.
    member_ids: tuple[str, ...]
    #: Injected (data-scope) filter members, so a log can say they applied.
    injected_member_ids: tuple[str, ...]
    #: The client's requested page; the executor clamps it to the surface cap.
    requested_limit: int | None
    offset: int

    @property
    def params(self) -> Mapping[str, Any]:
        """The bound parameters, by generated name (values, for introspection)."""
        return dict(self.select.compile(compile_kwargs={"render_postcompile": True}).params)

    def with_row_cap(self, row_cap: int) -> tuple[Select[Any], int]:
        """The statement asking for ``limit + 1`` rows, and the effective limit.

        The extra row is how truncation is detected without a count query.
        """
        if row_cap < 1:
            raise ValueError("row_cap must be positive")
        limit = row_cap if self.requested_limit is None else min(self.requested_limit, row_cap)
        return self.select.limit(limit + 1).offset(self.offset), limit


# --- resolution ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _FilterSpec:
    dimension: DimensionDef
    filter: BiFilter
    injected: bool


@dataclass(frozen=True, slots=True)
class _Window:
    start: date
    end: date
    #: ``as_of`` semantics (equality) rather than a range.
    single: bool


#: The one state in which a calculated measure belongs to the INSTITUTION rather
#: than to one person, and therefore the only one the compiler will evaluate. The
#: value is pinned against ``app/models/bi_content.py``'s own vocabulary by
#: ``tests/services/bi/test_compiler_calculated.py``.
_CERTIFIED_STATE = "bank_certified"


@dataclass(frozen=True, slots=True)
class _Certified:
    """A bank-certified calculated measure, straight off the row.

    Deliberately carries the certified TEXT and not a member list: what the
    formula names is re-derived from this text on every compile
    (``expr.referenced_members``), so the stored ``referenced_members`` column
    can never become the authority for which figures a statement reads.
    """

    id: str
    label: str
    value_type: str
    expression: str


@dataclass(frozen=True, slots=True)
class _Calculated:
    """One certified calculated measure, resolved against the catalogue."""

    id: str
    label: str
    value_type: BiResultColumnFormat
    tree: expr.Expr
    #: The period the formula compares over, declared in its own text (D-195);
    #: ``None`` when it names no period function and is plain arithmetic.
    grain: expr.PeriodGrain | None
    #: The figures the formula names, in the order written. From the SERVER's
    #: parse, resolved to catalogue measures.
    references: tuple[MeasureDef, ...]
    #: Every period the formula reads, as grains back from the query's own window.
    periods: tuple[int, ...]

    @property
    def reach(self) -> int:
        return max(self.periods)


#: What the compiler emits one result column for: a catalogue measure, or one of
#: the institution's own certified calculated measures. Both declare the three
#: things a column needs — an id, a label and a value type.
_Emitted = MeasureDef | _Calculated


@dataclass(slots=True)
class _Resolved:
    fact: str
    #: The result columns, in the order the request named them.
    outputs: list[_Emitted]
    #: Every CATALOGUE measure the query reads: the measure outputs, plus every
    #: figure a calculated output's formula names. Every shape rule — one fact
    #: table, one time behaviour, allowed dimensions — is decided on this list,
    #: so a calculated measure is held to exactly the rules its figures are.
    measures: list[MeasureDef]
    #: Row group keys, in order (the pivot dimension is not one of them).
    dimensions: list[DimensionDef]
    filters: list[_FilterSpec]
    top_n: BiTopN | None
    top_n_dimension: DimensionDef | None
    pivot: BiPivot | None
    pivot_dimension: DimensionDef | None
    over_dimension: DimensionDef | None
    stock: bool
    current: _Window
    prior: _Window | None
    member_ids: tuple[str, ...]
    injected_member_ids: tuple[str, ...]
    #: The declared grain every calculated output shares, or ``None``.
    grain: expr.PeriodGrain | None = None
    #: Distinct periods the statement must aggregate, ascending from ``0``.
    periods: tuple[int, ...] = (0,)

    @property
    def calculated(self) -> tuple[_Calculated, ...]:
        return tuple(output for output in self.outputs if isinstance(output, _Calculated))


def _lookup(cat: Catalogue, member_id: str) -> MemberDef:
    try:
        return cat.member(member_id)
    except CatalogueUnknownMember as exc:
        raise UnknownMember(member_id) from exc


def _assert_mapped(member: MemberDef) -> None:
    """The member's ``(table, column)`` must be a mapped column of ``app.models.bi``."""
    table = _TABLES.get(member.table)
    if table is None or member.column not in table.c:
        raise UnknownMember(member.id)


def _composed(cat: Catalogue, measure: MeasureDef) -> list[MeasureDef]:
    """The measures a measure is composed from (numerator, denominator, weight)."""
    out: list[MeasureDef] = []
    for reference in (measure.numerator, measure.denominator, measure.weight):
        if reference is not None:
            out.append(cast(MeasureDef, _lookup(cat, reference)))
    return out


#: Calendar months per period of each declared grain. The grain VOCABULARY is
#: ``app/domain/bi/expr.py``'s (the formula declares it); what a period spans on a
#: calendar is the compiler's, because only the compiler turns it into dates.
_MONTHS_PER_PERIOD: Mapping[str, int] = {"month": 1, "quarter": 3, "year": 12}


def _period_bounds(day: date, grain: str) -> tuple[date, date]:
    """The first and last calendar day of the ``grain`` period containing ``day``."""
    span = _MONTHS_PER_PERIOD[grain]
    first_month = ((day.month - 1) // span) * span + 1
    last_month = first_month + span - 1
    return (
        date(day.year, first_month, 1),
        date(day.year, last_month, monthrange(day.year, last_month)[1]),
    )


def _shifted_period(day: date, grain: str, periods_back: int) -> tuple[date, date]:
    """The bounds of the period ``periods_back`` grains before ``day``'s own.

    Calendar arithmetic on months rather than on days: three months before 31 May
    is March, whose end is the 31st, and no number of days says that.
    """
    start, _ = _period_bounds(day, grain)
    months = (start.year * 12 + start.month - 1) - periods_back * _MONTHS_PER_PERIOD[grain]
    year, month = divmod(months, 12)
    return _period_bounds(date(year, month + 1, 1), grain)


def _period_window(as_of: date, grain: str, periods_back: int) -> _Window:
    """The window of the period ``periods_back`` grains before ``as_of``'s own.

    Never past the reporting date: a closed period is read whole, and the period in
    progress is read up to the date asked for. Clipping matters for a flow, where
    the window is an accumulation; for a stock it is the period's last date with
    data either way.
    """
    start, end = _shifted_period(as_of, grain, periods_back)
    return _Window(start, min(end, as_of), single=False)


def _load_certified(
    db: Session, q: BiQuery, cat: Catalogue, *, organization_id: str, bank_id: str
) -> Mapping[str, _Certified]:
    """The institution's certified calculated measures this query names.

    Only ``bank_certified`` rows, and only the ids the request actually asked
    for. Three properties, each of which is the reason this is not a wider query:

    * **Certified only.** A personal draft or a measure awaiting review is not
      loaded at all, so it resolves as an unknown id exactly like a typo. That is
      deliberate on two counts: a saved or shared dashboard must not be able to
      make someone else run a formula nobody certified, and the compiler holds no
      principal, so it could not tell a draft's own author from anyone else
      without being handed an identity it has no other use for. The refusal is
      the generic one because naming the measure would tell a caller that another
      person's private draft exists.
    * **Institution-scoped at the query.** Two banks of one organization share an
      RLS tenant, so organization scoping alone cannot isolate their rows
      (the by-id rule in ``AGENTS.md``).
    * **The certified TEXT is verified against its digest HERE, on every read**
      (audit A9-10). The row's CHECK constraints guarantee that
      ``approved_expression`` is present on a certified row and that
      ``approved_expression_digest`` EQUALS ``expression_digest`` — but they bind
      two digest COLUMNS to each other and nothing in the database binds either
      one to the TEXT, because no portable CHECK can compute a SHA-256 (SQLite has
      no such function, so the hermetic suite could not enforce a DB-level version
      even where Postgres could). An UPDATE that rewrites ``approved_expression``
      and leaves both digests alone therefore satisfies every constraint — and
      since ``expand_calculated_measures`` authorizes a query as the figures the
      TEXT names, a doctored formula would reach figures no checker approved. So
      the digest of the text is recomputed on each read and a mismatch REFUSES:
      the row is skipped, which makes the id resolve as unknown exactly like a
      typo. It is never repaired — recomputing and storing the digest would
      launder the tampering — and the refusal is the generic one, so a caller
      cannot learn from the response that a tampered row exists.
    """
    # Imported inside the call: ``content`` imports this module, so the dependency
    # runs one way at module scope. ``expression_digest`` is the same function the
    # certification path hashes with, not a second SHA-256 spelled here.
    from app.services.bi.content import (  # noqa: PLC0415 - one-way dependency at run time
        expression_digest,
    )

    wanted = [member_id for member_id in dict.fromkeys(q.measures) if member_id not in cat]
    if not wanted:
        return {}
    rows = db.scalars(
        select(BiMeasure).where(
            BiMeasure.organization_id == organization_id,
            BiMeasure.bank_id == bank_id,
            BiMeasure.state == _CERTIFIED_STATE,
            BiMeasure.measure_key.in_(wanted),
        )
    )
    found: dict[str, _Certified] = {}
    for row in rows:
        text = row.approved_expression
        if text is None:  # pragma: no cover - refused by ck_bi_measures_approval_complete
            continue
        if expression_digest(text) != row.approved_expression_digest:
            # Loud in the log, silent to the caller. Nothing about the row reaches
            # the response, and the operator board is where a tampered
            # certification has to become visible.
            # ``loguru`` formats with ``{}``, not ``%s`` — written the printf way
            # this logged the placeholders literally and dropped the three values
            # (audit A10-09). It is the one line that says a certification was
            # tampered with, so it has to carry the institution and the measure.
            logger.error(
                "bi.compiler.certified_expression_digest_mismatch "
                "organization={} bank={} measure={}",
                organization_id,
                bank_id,
                row.measure_key,
            )
            continue
        found[row.measure_key] = _Certified(
            id=row.measure_key, label=row.label, value_type=row.value_type, expression=text
        )
    return found


def expand_calculated_measures(
    db: Session, cat: Catalogue, q: BiQuery, *, organization_id: str, bank_id: str
) -> BiQuery:
    """``q`` with every certified calculated measure replaced by the FIGURES it names.

    This is the authorization surface of a calculated measure, and it is the only
    thing the authorization walk needs: ``authorize_query`` evaluates a sentence per
    ``(module, sensitivity)`` pair of the members a query touches, and a formula
    touches exactly the catalogue figures its text names. Hand it this query instead
    of the caller's and a reader cannot use a formula to reach a figure they hold no
    binding for — the ids come from the SERVER's parse of the stored text, by the
    same structural walk (``expr.referenced_members``) that
    ``content.py::_readable`` uses, and never from the stored
    ``referenced_members`` column or from anything on the request.

    Three deliberately refusing properties:

    * an id that is neither a catalogue member nor a CERTIFIED measure of this
      institution is left exactly as it arrived, so it resolves as unknown. The
      same is true of a stored formula that no longer parses: an expansion that
      dropped either would hand the walk a SHORTER member list than the statement
      would read, which is the one failure mode here that is a leak rather than an
      inconvenience;
    * a sort key naming a calculated measure is dropped from the probe, because it
      is not a catalogue member and the figures behind it are already in the list;
    * the expanded list is capped at the same number of figures a request may name,
      so twenty-five formulas of twenty-five figures cannot be composed into one
      evaluation.

    The retention bound (D-196) is NOT applied: whether an identity may read the
    figures a formula names must not change when a deployment shortens its window.
    The compiler applies it where it belongs, before any figure is served.
    """
    certified = _load_certified(db, q, cat, organization_id=organization_id, bank_id=bank_id)
    if not certified:
        return q
    expanded: list[str] = []

    def keep(member_id: str) -> None:
        if member_id not in expanded:
            expanded.append(member_id)

    for member_id in q.measures:
        stored = certified.get(member_id)
        if stored is None:
            keep(member_id)
            continue
        try:
            tree = expr.parse(stored.expression, retention_days=None)
        except expr.ExpressionError:
            keep(member_id)
            continue
        for reference in expr.referenced_members(tree):
            keep(reference)
    if not expanded:
        raise InvalidQuery("This result names no figure, so there is nothing to read.")
    if len(expanded) > BI_MAX_MEASURES:
        raise InvalidQuery(
            f"This result reads {len(expanded)} figures once its calculated measures are "
            f"worked out; at most {BI_MAX_MEASURES} can be read at once."
        )
    return q.model_copy(
        update={
            "measures": expanded,
            "sort": [sort for sort in q.sort if sort.member not in certified],
        }
    )


def _resolve_calculated(cat: Catalogue, certified: _Certified, retention_days: int) -> _Calculated:
    """Parse a certified formula and resolve every figure it names.

    The ids come from ``expr.referenced_members`` over the tree this function
    parses — never from the stored ``referenced_members`` column, and never from
    the request. That is what makes the member set a statement reads the same set
    the authorization walk was given: both derive it from the same text by the
    same structural walk.

    The retention bound (D-196) is applied again here, not only where the formula
    was written: a deployment that SHORTENS ``BI_DAILY_RETENTION_DAYS`` after a
    measure was certified would otherwise start answering it out of partitions
    that no longer exist, and an empty prior period renders as a real figure of
    nothing.
    """
    try:
        tree = expr.parse(certified.expression, retention_days=retention_days)
    except expr.ExpressionError as error:
        raise InvalidQuery(
            f"{certified.label} cannot be computed: {error.message}", members=(certified.id,)
        ) from error
    references: list[MeasureDef] = []
    for member_id in expr.referenced_members(tree):
        member = _lookup(cat, member_id)
        if not isinstance(member, MeasureDef):
            raise InvalidQuery(
                f"{certified.label} names {member_id}, which is something to group or filter "
                "by rather than a figure.",
                members=(certified.id, member_id),
            )
        if member.aggregation in _CONCENTRATION:
            raise InvalidQuery(
                f"{certified.label} names {member.label}, a concentration figure. "
                "Those are worked out over a second grouping of their own and cannot be "
                "combined inside a formula.",
                members=(certified.id, member_id),
            )
        references.append(member)
    if not references:
        raise InvalidQuery(
            f"{certified.label} names no figure, so there is nothing to compute.",
            members=(certified.id,),
        )
    if certified.value_type not in _RESULT_FORMATS:
        # The row's own CHECK constraint already limits this to the catalogue's
        # measure value types; refusing here as well is what makes the narrowing
        # below honest rather than an unchecked assertion about a text column.
        raise InvalidQuery(
            f"{certified.label} declares a kind of number this result cannot render.",
            members=(certified.id,),
        )
    periods = expr.period_offsets(tree)
    if len(periods) > MAX_PERIODS_PER_QUERY:
        raise InvalidQuery(
            f"{certified.label} reads its figures at {len(periods)} different periods; "
            f"at most {MAX_PERIODS_PER_QUERY} can be shown in one result.",
            members=(certified.id,),
        )
    return _Calculated(
        id=certified.id,
        label=certified.label,
        value_type=cast(BiResultColumnFormat, certified.value_type),
        tree=tree,
        grain=expr.declared_grain(tree),
        references=tuple(references),
        periods=periods,
    )


def _windows(time: BiTime) -> tuple[_Window, _Window | None]:
    if time.as_of is not None:
        current = _Window(time.as_of, time.as_of, single=True)
        prior = None if time.compare_to is None else _Window(time.compare_to, time.compare_to, True)
        return current, prior
    assert time.range is not None
    start, end = time.range.start, time.range.end
    current = _Window(start, end, single=False)
    if time.compare_to is None:
        return current, None
    return current, _Window(time.compare_to - (end - start), time.compare_to, single=False)


def _period_shape(  # noqa: PLR0911, PLR0912 - one branch per named refusal
    q: BiQuery,
    outputs: Sequence[_Emitted],
    *,
    time_axis: Sequence[DimensionDef],
    stock: bool,
    retention_days: int,
) -> tuple[expr.PeriodGrain | None, tuple[int, ...]]:
    """D-195: the query must CARRY the grain a calculated measure declares.

    A calculated measure that names ``PCT_CHANGE`` or ``LAG`` states the period it
    compares over, and it means that period everywhere it appears. A query that
    cannot answer at that period is therefore REFUSED BY NAME rather than answered
    at whatever period it happens to have — the point of certifying a measure is
    that one label means one figure, and a figure that is month-on-month in one
    widget and quarter-on-quarter in the next defeats it.

    Four things a query has to carry, each refused in the measure's own words:

    1. **One declared grain per result.** Two calculated measures comparing over
       different periods cannot share a result: the rows would be at two periods.
    2. **A single reporting date.** "The period before" is defined relative to one
       date. Over a window there is no single period to be before, and the row
       axis this compiler builds cannot carry a per-row shift.
    3. **No second period axis.** ``compare_to`` and a time dimension each add
       their own comparison; two comparisons under one label is the ambiguity
       this whole rule exists to refuse.
    4. **Reach inside the retained window** (D-196), and for a FLOW the whole of
       the lagged period has to be retained rather than just its last date —
       a stock reads the period's closing book, a flow adds the period up
       (D-195), so the same reach costs one more period of history.
    """
    grained = [
        output for output in outputs if isinstance(output, _Calculated) and output.grain is not None
    ]
    if not grained:
        return None, (0,)
    first = grained[0]
    grain = cast(expr.PeriodGrain, first.grain)
    period = expr.GRAIN_LABELS[grain]
    for output in grained[1:]:
        if output.grain != grain:
            other = expr.GRAIN_LABELS[cast(expr.PeriodGrain, output.grain)]
            raise InvalidQuery(
                f"{first.label} compares by {period} and {output.label} by {other}. "
                "One result can carry one comparison period; show them side by side "
                "on a dashboard instead.",
                members=(first.id, output.id),
            )
    opening = f"{first.label} compares each {period} with the {period} before it"
    if q.time.as_of is None:
        raise InvalidQuery(
            f"{opening}, so it has to be read at a single reporting date rather than "
            "over a window.",
            members=(first.id,),
        )
    if q.time.compare_to is not None:
        raise InvalidQuery(
            f"{opening}, so it cannot also be compared with another period.",
            members=(first.id,),
        )
    if time_axis:
        raise InvalidQuery(
            f"{opening}, so the result cannot also be broken down by {time_axis[0].label}.",
            members=(first.id, time_axis[0].id),
        )
    periods = sorted({offset for output in grained for offset in output.periods})
    if len(periods) > MAX_PERIODS_PER_QUERY:
        raise InvalidQuery(
            f"This result reads its figures at {len(periods)} different periods; "
            f"at most {MAX_PERIODS_PER_QUERY} can be shown at once.",
            members=tuple(output.id for output in grained),
        )
    if not stock:
        # A flow is added up ACROSS the lagged period, so the retained window has
        # to reach that period's FIRST day; a stock only needs its last.
        needed = (max(periods) + 1) * expr.GRAIN_MAX_DAYS[grain]
        if needed > retention_days:
            raise InvalidQuery(
                f"{first.label} adds up over each {period}, so this comparison needs "
                f"{needed} days of history and daily figures are kept for "
                f"{retention_days}.",
                members=(first.id,),
            )
    return grain, tuple(periods)


def _resolve(  # noqa: PLR0912, PLR0915 - one branch per shape rule, all named
    cat: Catalogue,
    q: BiQuery,
    injected_filters: Sequence[BiFilter],
    certified: Mapping[str, _Certified] = MappingProxyType({}),
    retention_days: int = 0,
) -> _Resolved:
    """Ids → members, then every shape rule. Unknown ids are refused first."""
    hierarchies: dict[str, HierarchyDef] = {h.id: h for h in cat.hierarchies()}

    # 1. Existence: every id the request names must be a catalogue member (a
    #    hierarchy id stands for its levels) or one of the institution's own
    #    CERTIFIED calculated measures. Nothing else is examined yet.
    outputs: list[_Emitted] = []
    measures: list[MeasureDef] = []
    for member_id in q.measures:
        stored = certified.get(member_id)
        if stored is not None:
            calculated = _resolve_calculated(cat, stored, retention_days)
            outputs.append(calculated)
            measures.extend(
                reference for reference in calculated.references if reference not in measures
            )
            continue
        member = _lookup(cat, member_id)
        if not isinstance(member, MeasureDef):
            raise InvalidQuery(f"{member_id} is a dimension, not a measure.", members=(member_id,))
        outputs.append(member)
        if member not in measures:
            measures.append(member)
    dimension_ids: list[str] = []
    for member_id in q.dimensions:
        hierarchy = hierarchies.get(member_id)
        dimension_ids.extend(hierarchy.levels if hierarchy is not None else (member_id,))
    dimensions: list[DimensionDef] = []
    for member_id in dimension_ids:
        member = _lookup(cat, member_id)
        if not isinstance(member, DimensionDef):
            raise InvalidQuery(f"{member_id} is a measure, not a dimension.", members=(member_id,))
        if member not in dimensions:
            dimensions.append(member)

    def _dimension(member_id: str, role: str) -> DimensionDef:
        member = _lookup(cat, member_id)
        if not isinstance(member, DimensionDef):
            raise InvalidQuery(f"{role} must name a dimension; {member_id} is a measure.")
        return member

    filters = [_FilterSpec(_dimension(f.member, "A filter"), f, False) for f in q.filters]
    injected = [
        _FilterSpec(_dimension(f.member, "A data scope"), f, True) for f in injected_filters
    ]
    top_n_dimension = None if q.top_n is None else _dimension(q.top_n.dimension, "top_n")
    pivot_dimension = None if q.pivot is None else _dimension(q.pivot.dimension, "pivot")
    for sort in q.sort:
        # A sort key naming one of the institution's certified calculated measures
        # is not a catalogue member; the shape rules below still require it to be
        # among the requested outputs.
        if sort.member not in certified:
            _lookup(cat, sort.member)
    composed = {m.id: m for measure in measures for m in _composed(cat, measure)}
    over_dimensions: dict[str, DimensionDef] = {}
    for measure in measures:
        if measure.over is not None:
            over_dimensions[measure.over] = _dimension(measure.over, "over")

    # 2. Mapping: each member's (table, column) is a real column of the plane.
    for member in (
        *measures,
        *composed.values(),
        *dimensions,
        *(spec.dimension for spec in (*filters, *injected)),
        *([top_n_dimension] if top_n_dimension else []),
        *([pivot_dimension] if pivot_dimension else []),
        *over_dimensions.values(),
    ):
        _assert_mapped(member)

    # 3. Shape.
    facts = {measure.table for measure in (*measures, *composed.values())}
    if len(facts) != 1:
        raise InvalidQuery(
            "The measures read from more than one fact table; query one fact at a time.",
            members=tuple(measure.id for measure in measures),
        )
    fact = next(iter(facts))
    if len(measures) > BI_MAX_MEASURES:
        raise InvalidQuery(
            f"This result reads {len(measures)} figures once its calculated measures are "
            f"worked out; at most {BI_MAX_MEASURES} can be read at once.",
            members=tuple(output.id for output in outputs),
        )
    behaviours = {measure.time_behaviour for measure in measures}
    if len(behaviours) != 1:
        raise InvalidQuery(
            "Stock and flow measures cannot share one query.",
            members=tuple(measure.id for measure in measures),
        )
    if len(over_dimensions) > 1:
        raise InvalidQuery(
            "Concentration measures over different dimensions cannot share one query.",
            members=tuple(measure.id for measure in measures if measure.over),
        )
    grain, periods = _period_shape(
        q,
        outputs,
        time_axis=[
            *(dimension for dimension in dimensions if dimension.table == DATE_TABLE),
            *(
                [pivot_dimension]
                if pivot_dimension is not None and pivot_dimension.table == DATE_TABLE
                else []
            ),
        ],
        stock=measures[0].time_behaviour == "stock",
        retention_days=retention_days,
    )
    over_dimension = next(iter(over_dimensions.values()), None)
    if over_dimension is not None and any(isinstance(output, _Calculated) for output in outputs):
        # A concentration measure is aggregated twice — once per obligor, then over
        # the group — and a calculated measure's own components are finalized at the
        # outer level. Combining the two would re-aggregate one inside the other,
        # so they are kept apart rather than made to look combinable.
        raise InvalidQuery(
            "A concentration figure and a calculated measure cannot be read in one result.",
            members=tuple(
                output.id
                for output in outputs
                if isinstance(output, _Calculated) or output.over is not None
            ),
        )
    if pivot_dimension is not None and over_dimension is not None:
        raise InvalidQuery("A concentration measure cannot be pivoted.")
    if pivot_dimension is not None and pivot_dimension in dimensions:
        raise InvalidQuery(
            f"{pivot_dimension.id} is the pivot axis; remove it from dimensions.",
            members=(pivot_dimension.id,),
        )
    if top_n_dimension is not None and top_n_dimension not in dimensions:
        raise InvalidQuery(
            f"top_n names {top_n_dimension.id}, which is not a requested dimension.",
            members=(top_n_dimension.id,),
        )
    if pivot_dimension is not None and pivot_dimension == top_n_dimension:
        raise InvalidQuery("The pivot axis cannot also be the Top-N dimension.")
    sliceable = [
        *dimensions,
        *(spec.dimension for spec in (*filters, *injected)),
        *([pivot_dimension] if pivot_dimension else []),
        *over_dimensions.values(),
    ]
    for measure in measures:
        for dimension in sliceable:
            if dimension.id not in measure.allowed_dimensions:
                raise InvalidQuery(
                    f"{measure.id} cannot be sliced by {dimension.id}.",
                    members=(measure.id, dimension.id),
                )
    requested = {output.id for output in outputs} | {d.id for d in dimensions}
    for sort in q.sort:
        if sort.member not in requested:
            raise InvalidQuery(
                f"Sort by {sort.member} needs it among the requested measures or dimensions.",
                members=(sort.member,),
            )
        if q.pivot is not None and sort.member in {output.id for output in outputs}:
            raise InvalidQuery(
                "A pivoted result cannot be sorted by a measure; sort by a row dimension.",
                members=(sort.member,),
            )

    current, prior = _windows(q.time)
    member_ids: list[str] = []
    for member_id in (
        *(output.id for output in outputs),
        *(m.id for m in measures),
        *composed,
        *(d.id for d in dimensions),
        *(spec.dimension.id for spec in (*filters, *injected)),
        *([top_n_dimension.id] if top_n_dimension else []),
        *([pivot_dimension.id] if pivot_dimension else []),
        *over_dimensions,
    ):
        if member_id not in member_ids:
            member_ids.append(member_id)
    return _Resolved(
        fact=fact,
        outputs=outputs,
        measures=measures,
        dimensions=dimensions,
        filters=[*filters, *injected],
        top_n=q.top_n,
        top_n_dimension=top_n_dimension,
        pivot=q.pivot,
        pivot_dimension=pivot_dimension,
        over_dimension=over_dimension,
        stock=measures[0].time_behaviour == "stock",
        current=current,
        prior=prior,
        member_ids=tuple(member_ids),
        injected_member_ids=tuple(dict.fromkeys(spec.dimension.id for spec in injected)),
        grain=grain,
        periods=periods,
    )


# --- sources ------------------------------------------------------------------------------

#: Row-filter columns that define a measure's POPULATION rather than select
#: within it (see the module docstring, "NULL versus zero").
#:
#: The three ``bi_fact_target`` keys are here for the reason the rule exists.
#: A target variant is ABOUT the rows carrying its own measure id, bank-wide
#: scope and register version; the rows beside them belong to other measures
#: entirely. As a SELECTION each would contribute ``0``, so a bank holding a
#: budget for anything else on the same date would see a measure with NO target
#: render as zero rather than blank — the D-015 hazard the answerable rule was
#: written for. As a POPULATION they contribute NULL, and a sum of nothing is
#: NULL (D-064).
_POPULATION_COLUMNS: frozenset[str] = frozenset(
    {"position_type", "measure_id", "scope_dimension", "target_version"}
)

#: Selection columns the D-042 answerable rule does NOT ask about, and why.
#: ``bi_agg_position_daily`` PRE-COMPUTES both as their own measure columns
#: (``non_performing_exposure_rc_sum``, ``fx_unconverted_count``) and carries
#: neither as a grain attribute, so on that source "was the question
#: answerable" cannot be asked at all; applying the rule on the fact path
#: alone would make the two sources return different numbers for one query,
#: which is the single invariant aggregate selection rests on. Completeness of
#: the classification instead has a reconciliation check of its own: R1 compares
#: the mart's non-performing exposure with the engine's, reading the fact table
#: directly rather than through this compiler. ``fx_unconverted`` is NOT NULL on
#: both sources, so the question never arises for it.
_PRECOMPUTED_SELECTION_COLUMNS: frozenset[str] = frozenset({"non_performing", "fx_unconverted"})


@dataclass(frozen=True, slots=True)
class _MeasureValue:
    """What an additive measure aggregates: the column and its two predicates."""

    column: ColumnElement[Any]
    population: ColumnElement[Any] | None
    selection: ColumnElement[Any] | None
    #: "The selection question is answerable on this row" (D-042), or ``None``
    #: when no selection column can be NULL and the question cannot arise.
    answerable: ColumnElement[Any] | None
    #: The aggregate table pre-counts rows, so a count is a sum of counts.
    count_as_sum: bool


def _split_row_filters(
    filters: Iterable[RowFilter],
) -> tuple[tuple[RowFilter, ...], tuple[RowFilter, ...]]:
    population = tuple(f for f in filters if f.column in _POPULATION_COLUMNS)
    selection = tuple(f for f in filters if f.column not in _POPULATION_COLUMNS)
    return population, selection


class _Source:
    """A fact table and the conformed dimensions joined to it, built lazily.

    One instance per ``select``: joins are recorded as members are resolved,
    and ``select()`` emits them last. Every join is LEFT OUTER on the tenant
    keys plus the natural key, so a fact row whose dimension is unknown keeps
    its place under a NULL key rather than vanishing.
    """

    def __init__(self, fact: Table) -> None:
        self.fact = fact
        self.date_column: ColumnElement[Any] = fact.c[_FACT_DATE_COLUMN[fact.name]]
        self._joins: dict[str, tuple[Table, ColumnElement[Any], ColumnElement[Any]]] = {}

    #: Tables whose columns are read straight off the fact.
    @property
    def local_tables(self) -> frozenset[str]:
        return frozenset({self.fact.name})

    def _local_column(self, member: MemberDef) -> ColumnElement[Any] | None:
        if member.table in self.local_tables and member.column in self.fact.c:
            return self.fact.c[member.column]
        return None

    def resolve(self, member: MemberDef) -> ColumnElement[Any]:
        local = self._local_column(member)
        if local is not None:
            return local
        if member.table in self.local_tables:
            raise UnknownMember(member.id)
        table = _TABLES.get(member.table)
        if table is None or member.column not in table.c:
            raise UnknownMember(member.id)
        if member.table == DATE_TABLE:
            self._joins.setdefault(member.table, (table, self.date_column, table.c["date"]))
        elif member.table in _DIM_JOIN_KEYS:
            fact_key, dim_key = _DIM_JOIN_KEYS[member.table]
            if fact_key not in self.fact.c:
                raise InvalidQuery(
                    f"{member.id} does not apply to this fact.", members=(member.id,)
                )
            self._joins.setdefault(member.table, (table, self.fact.c[fact_key], table.c[dim_key]))
        else:
            raise InvalidQuery(f"{member.id} does not apply to this fact.", members=(member.id,))
        return table.c[member.column]

    def select(
        self, columns: Sequence[ColumnElement[Any]], where: Sequence[ColumnElement[Any]]
    ) -> Select[Any]:
        """``SELECT columns FROM <fact + joins> WHERE where``.

        The ONLY way a select is built over a source: ``columns`` and ``where``
        are already resolved (so every join they need is recorded) before the
        FROM clause is emitted. Building FROM first and resolving a filter
        afterwards leaves its dimension table out of the joins, and SQLAlchemy
        then adds it as a cartesian product — a filter on branch would match
        every fact row.
        """
        return select(*columns).select_from(self._from_clause()).where(*where)

    def _from_clause(self) -> FromClause:
        clause: FromClause = self.fact
        for table, fact_key, dim_key in self._joins.values():
            clause = clause.outerjoin(
                table,
                and_(
                    table.c["organization_id"] == self.fact.c["organization_id"],
                    table.c["bank_id"] == self.fact.c["bank_id"],
                    dim_key == fact_key,
                ),
            )
        return clause

    def measure_value(self, measure: MeasureDef) -> _MeasureValue:
        if measure.column not in self.fact.c:
            raise UnknownMember(measure.id)
        population, selection = _split_row_filters(measure.row_filters)
        population_predicate = self._row_predicate(population)
        engine = self._engine_predicate(measure)
        if engine is not None:
            population_predicate = (
                engine if population_predicate is None else and_(population_predicate, engine)
            )
        return _MeasureValue(
            self.fact.c[measure.column],
            population_predicate,
            self._row_predicate(selection),
            self._answerable_predicate(selection),
            count_as_sum=False,
        )

    def _row_predicate(self, filters: Iterable[RowFilter]) -> ColumnElement[Any] | None:
        clauses: list[ColumnElement[Any]] = []
        for row_filter in filters:
            if row_filter.column not in self.fact.c:
                raise InvalidQuery(f"Row filter column {row_filter.column} is not on this fact.")
            column = self.fact.c[row_filter.column]
            if row_filter.op == "in":
                clauses.append(column.in_(list(row_filter.values)))
            elif row_filter.op == "is_true":
                clauses.append(column.is_(true()))
            elif row_filter.op == "is_false":
                clauses.append(column.is_not(true()))
            else:  # not_null
                clauses.append(column.is_not(None))
        if not clauses:
            return None
        return and_(*clauses) if len(clauses) > 1 else clauses[0]

    def _answerable_predicate(self, filters: Iterable[RowFilter]) -> ColumnElement[Any] | None:
        """ "The selection question is answerable on this row" (D-042).

        Only a NULLABLE selection column can be unanswerable, and a column the
        aggregate pre-computes is never asked (``_PRECOMPUTED_SELECTION_COLUMNS``).
        ``None`` means every selection column always holds a value, so the
        measure needs no answerable count at all and its SQL is unchanged.
        """
        clauses = [
            self.fact.c[row_filter.column].is_not(None)
            for row_filter in filters
            if row_filter.column in self.fact.c
            and self.fact.c[row_filter.column].nullable
            and row_filter.column not in _PRECOMPUTED_SELECTION_COLUMNS
        ]
        if not clauses:
            return None
        return and_(*clauses) if len(clauses) > 1 else clauses[0]

    def _engine_predicate(self, measure: MeasureDef) -> ColumnElement[Any] | None:
        """An engine copy is one ``(module, metric_id, regime, tier)`` row per date."""
        rule = measure.engine_rule
        if rule is None:
            return None
        prefix = f"engine.{rule.metric_id}."
        suffix = f".{rule.tier}"
        if not (measure.id.startswith(prefix) and measure.id.endswith(suffix)):
            raise InvalidQuery(f"{measure.id} is not a well-formed engine measure id.")
        regime = measure.id[len(prefix) : -len(suffix)]
        if not regime:
            raise InvalidQuery(f"{measure.id} names no regime.")
        return and_(
            self.fact.c["module"] == rule.module,
            self.fact.c["metric_id"] == rule.metric_id,
            self.fact.c["tier"] == rule.tier,
            self.fact.c["regime"] == regime,
        )


class _AggregateSource(_Source):
    """``bi_agg_position_daily`` standing in for the daily position fact."""

    def __init__(self) -> None:
        super().__init__(_TABLES[_AGG_TABLE])

    @property
    def local_tables(self) -> frozenset[str]:
        return frozenset({_AGG_TABLE, POSITION_TABLE})

    def _local_column(self, member: MemberDef) -> ColumnElement[Any] | None:
        override = _AGG_OVERRIDES.get((member.table, member.column))
        if override is not None:
            return self.fact.c[override]
        if member.table in self.local_tables and member.column in _AGG_GRAIN:
            return self.fact.c[member.column]
        return None

    def measure_value(self, measure: MeasureDef) -> _MeasureValue:
        mapped = _aggregate_measure(measure)
        if mapped is None:
            raise InvalidQuery(f"{measure.id} is not served by the aggregate table.")
        column, remaining = mapped
        population, selection = _split_row_filters(remaining)
        return _MeasureValue(
            self.fact.c[column],
            self._row_predicate(population),
            self._row_predicate(selection),
            self._answerable_predicate(selection),
            count_as_sum=measure.aggregation == "count",
        )


def _aggregate_measure(measure: MeasureDef) -> tuple[str, tuple[RowFilter, ...]] | None:
    """The aggregate column an additive position measure reads, and the row
    filters left over once the aggregate has absorbed what it pre-computes."""
    if measure.table != POSITION_TABLE:
        return None
    remaining = list(measure.row_filters)
    if measure.aggregation == "sum":
        column = _AGG_SUM_COLUMNS.get(measure.column)
        if column is None:
            return None
        if column == "classification_exposure_rc_sum" and _AGG_NON_PERFORMING in remaining:
            column = "non_performing_exposure_rc_sum"
            remaining.remove(_AGG_NON_PERFORMING)
    elif measure.aggregation == "count":
        if measure.column != "snapshot_id":
            return None
        column = "row_count"
        if _AGG_UNCONVERTED in remaining:
            column = "fx_unconverted_count"
            remaining.remove(_AGG_UNCONVERTED)
    else:
        return None
    for row_filter in remaining:
        if row_filter.op != "in" or row_filter.column not in _AGG_GRAIN:
            return None
    return column, tuple(remaining)


def aggregate_table_covers(cat: Catalogue, resolved: _Resolved) -> bool:
    """Whether ``bi_agg_position_daily`` answers this query exactly.

    Every measure must be an additive sum / count the aggregate carries — or a
    ratio / share composed only of such — and every dimension, filter, pivot
    and Top-N member must be in the aggregate's grain (a conformed dimension
    reachable through a grain key counts). Concentration and weighted-average
    measures never qualify: the first needs the obligor grain, the second a
    denominator restricted to rows that carry a rate, which the aggregate does
    not pre-compute.
    """
    if resolved.fact != POSITION_TABLE or resolved.over_dimension is not None:
        return False
    probe = _AggregateSource()
    for measure in resolved.measures:
        if measure.aggregation not in _ADDITIVE | _RATIO:
            return False
        parts = _composed(cat, measure) if measure.aggregation in _RATIO else [measure]
        for part in parts:
            if part.aggregation not in _ADDITIVE or _aggregate_measure(part) is None:
                return False
    members: list[DimensionDef] = [
        *resolved.dimensions,
        *(spec.dimension for spec in resolved.filters),
        *([resolved.pivot_dimension] if resolved.pivot_dimension else []),
    ]
    for member in members:
        if probe._local_column(member) is not None or member.table == DATE_TABLE:
            continue
        if member.table in probe.local_tables:
            return False
        join = _DIM_JOIN_KEYS.get(member.table)
        if join is None or join[0] not in probe.fact.c:
            return False
    return True


# --- measures -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Component:
    alias: str
    expr: ColumnElement[Any]


Finalizer = Callable[[Mapping[str, ColumnElement[Any]]], ColumnElement[Any]]


@dataclass(frozen=True, slots=True)
class _MeasurePlan:
    measure: _Emitted
    #: First-level aggregates (over fact rows), by alias.
    components: tuple[_Component, ...]
    #: Second-level aggregates (over the ``over`` grouping), by alias; empty
    #: when the measure has no concentration dimension. Each takes the
    #: first-level subquery's columns.
    second_level: Callable[[Mapping[str, ColumnElement[Any]]], tuple[_Component, ...]] | None
    finalize: Finalizer


def _all(*predicates: ColumnElement[Any] | None) -> ColumnElement[Any] | None:
    clauses = [predicate for predicate in predicates if predicate is not None]
    if not clauses:
        return None
    return and_(*clauses) if len(clauses) > 1 else clauses[0]


def _present(
    column: ColumnElement[Any], *predicates: ColumnElement[Any] | None
) -> ColumnElement[Any]:
    """``CASE WHEN <predicates> THEN column END``: NULL outside, so the row drops out."""
    predicate = _all(*predicates)
    return column if predicate is None else case((predicate, column))


def _summed(
    column: ColumnElement[Any],
    population: ColumnElement[Any] | None,
    selection: ColumnElement[Any] | None,
) -> ColumnElement[Any]:
    """A sum's row expression: NULL outside the population, 0 inside it when
    the selection does not hold, the value when it does."""
    inner = column if selection is None else case((selection, column), else_=literal(0))
    return _present(inner, population)


def _plan_additive(
    value: _MeasureValue, kind: str, pivot: ColumnElement[Any] | None
) -> ColumnElement[Any]:
    population = _all(value.population, pivot)
    if kind == "count" and not value.count_as_sum:
        return func.count(_present(value.column, population, value.selection))
    summed = func.sum(_summed(value.column, population, value.selection))
    return func.coalesce(summed, literal(0)) if value.count_as_sum else summed


def _answerable_count(
    value: _MeasureValue, population: ColumnElement[Any] | None, alias: str
) -> _Component | None:
    """How many rows of the population could answer the selection (D-042)."""
    if value.answerable is None:
        return None
    return _Component(
        alias, func.count(_present(literal(1, Integer), population, value.answerable))
    )


def _when_answerable(
    expr: ColumnElement[Any], cols: Mapping[str, ColumnElement[Any]], alias: str | None
) -> ColumnElement[Any]:
    """``expr``, or NULL when nothing in the population could answer it (D-042)."""
    if alias is None or alias not in cols:
        return expr
    return case((cols[alias] > 0, expr))


def _ratio(numerator: ColumnElement[Any], denominator: ColumnElement[Any]) -> ColumnElement[Any]:
    """``num / nullif(den, 0)`` as a floating ratio: NULL for a NULL or zero denominator."""
    return sql_cast(numerator, Float) / func.nullif(denominator, 0)


def _scaled(measure: MeasureDef, value: ColumnElement[Any]) -> ColumnElement[Any]:
    """Percentage-typed ratios are emitted in percentage points, like the engine's."""
    if measure.value_type == "pct":
        return value * 100
    return value


def _plan_measure(  # noqa: PLR0911, PLR0913 - one return per aggregation kind
    cat: Catalogue,
    source: _Source,
    measure: MeasureDef,
    prefix: str,
    pivot_predicate: ColumnElement[Any] | None,
    over_column: ColumnElement[Any] | None,
) -> _MeasurePlan:
    kind = measure.aggregation
    if kind in _ADDITIVE:
        value = source.measure_value(measure)
        alias = f"{prefix}v"
        answer = f"{prefix}a"
        counted = _answerable_count(value, _all(value.population, pivot_predicate), answer)
        aggregate = _plan_additive(value, kind, pivot_predicate)
        return _MeasurePlan(
            measure,
            (_Component(alias, aggregate), *([counted] if counted else ())),
            None,
            lambda cols: _when_answerable(cols[alias], cols, answer),
        )
    if kind == "last_value":
        value = source.measure_value(measure)
        alias = f"{prefix}v"
        answer = f"{prefix}a"
        counted = _answerable_count(value, _all(value.population, pivot_predicate), answer)
        latest = func.max(
            _present(value.column, value.population, value.selection, pivot_predicate)
        )
        return _MeasurePlan(
            measure,
            (_Component(alias, latest), *([counted] if counted else ())),
            None,
            lambda cols: _when_answerable(cols[alias], cols, answer),
        )
    if kind == "weighted_avg":
        value = source.measure_value(measure)
        weight = source.measure_value(_weight_of(cat, measure))
        population = _all(value.population, weight.population, pivot_predicate)
        selection = _all(value.selection, weight.selection)
        numerator = f"{prefix}n"
        denominator = f"{prefix}d"
        answer = f"{prefix}a"
        counted = _answerable_count(value, population, answer)
        return _MeasurePlan(
            measure,
            (
                _Component(
                    numerator,
                    func.sum(_summed(value.column * weight.column, population, selection)),
                ),
                _Component(
                    denominator,
                    func.sum(
                        _summed(
                            weight.column, population, _all(selection, value.column.is_not(None))
                        )
                    ),
                ),
                *([counted] if counted else ()),
            ),
            None,
            lambda cols: _when_answerable(_ratio(cols[numerator], cols[denominator]), cols, answer),
        )
    if kind in _RATIO:
        numerator_measure, denominator_measure = _ratio_parts(cat, measure)
        # D-042: when the numerator selects on a column that can be missing, the
        # denominator counts only the rows that could answer it, so the ratio is
        # a rate over the measurable population rather than one that reads an
        # unrecorded arrears flag as "current".
        answerable = source.measure_value(numerator_measure).answerable
        num = _plan_measure(cat, source, numerator_measure, f"{prefix}n", pivot_predicate, None)
        den = _plan_measure(
            cat,
            source,
            denominator_measure,
            f"{prefix}d",
            _all(pivot_predicate, answerable),
            None,
        )
        return _MeasurePlan(
            measure,
            (*num.components, *den.components),
            None,
            lambda cols: _scaled(measure, _ratio(num.finalize(cols), den.finalize(cols))),
        )
    if kind in _CONCENTRATION:
        if over_column is None:
            raise InvalidQuery(f"{measure.id} concentrates over no dimension.")
        value = source.measure_value(measure)
        share = f"{prefix}s"
        squares = f"{prefix}q"
        largest = f"{prefix}l"
        total = f"{prefix}t"
        answer = f"{prefix}a"
        counted = _answerable_count(value, _all(value.population, pivot_predicate), answer)

        def _second(cols: Mapping[str, ColumnElement[Any]]) -> tuple[_Component, ...]:
            s = cols[share]
            over = cols["over"]
            # The answerable count is additive over the concentration grouping,
            # so it is summed forward to the level the measure is reported at.
            forwarded = (_Component(answer, func.sum(cols[answer])),) if answer in cols else ()
            return (
                _Component(squares, func.sum(s * s)),
                _Component(largest, func.max(case((over.is_not(None), s)))),
                _Component(total, func.sum(s)),
                *forwarded,
            )

        if kind == "hhi":

            def _finalize(cols: Mapping[str, ColumnElement[Any]]) -> ColumnElement[Any]:
                concentration = _ratio(cols[squares], cols[total] * cols[total])
                return _scaled(measure, _when_answerable(concentration, cols, answer))

        else:

            def _finalize(cols: Mapping[str, ColumnElement[Any]]) -> ColumnElement[Any]:
                concentration = _ratio(cols[largest], cols[total])
                return _scaled(measure, _when_answerable(concentration, cols, answer))

        return _MeasurePlan(
            measure,
            (
                _Component(
                    share, func.sum(_summed(value.column, value.population, value.selection))
                ),
                *([counted] if counted else ()),
            ),
            _second,
            _finalize,
        )
    raise InvalidQuery(f"{measure.id} uses an aggregation the compiler does not support.")


def _plan_calculated(  # noqa: PLR0913 - one plan per figure per period, all explicit
    cat: Catalogue,
    source: _Source,
    calculated: _Calculated,
    prefix: str,
    pivot_predicate: ColumnElement[Any] | None,
    windows: Mapping[int, ColumnElement[Any] | None],
) -> _MeasurePlan:
    """A calculated measure: one sub-plan per (figure it names × period it reads).

    The formula itself never becomes an aggregate. Each FIGURE it names is planned
    exactly as it would be if the query had asked for it directly — same
    components, same population/selection split, same D-042 answerable count —
    once per period the formula reads it at, under that period's own window
    predicate. The formula is then evaluated over those finalized values
    (:func:`_evaluate`), which is why a calculated measure cannot reach outside the
    aggregation vocabulary the catalogue already declares: it has no aggregate of
    its own to reach with.
    """
    parts: dict[tuple[str, int], _MeasurePlan] = {}
    components: list[_Component] = []
    for index, measure in enumerate(calculated.references):
        for offset in calculated.periods:
            plan = _plan_measure(
                cat,
                source,
                measure,
                f"{prefix}r{index}p{offset}",
                _all(pivot_predicate, windows[offset]),
                None,
            )
            parts[(measure.id, offset)] = plan
            components.extend(plan.components)
    return _MeasurePlan(
        calculated,
        tuple(components),
        None,
        lambda cols: _evaluate(calculated.tree, cols, parts, 0),
    )


def _evaluate(  # noqa: PLR0911, PLR0912 - one branch per node shape, all named
    node: expr.Expr,
    cols: Mapping[str, ColumnElement[Any]],
    parts: Mapping[tuple[str, int], _MeasurePlan],
    offset: int,
) -> ColumnElement[Any]:
    """The formula's AST as one SQL expression over already-aggregated values.

    ``offset`` is which period this sub-expression is being read at, in grains
    back from the query's own; ``LAG`` and ``PCT_CHANGE`` shift it rather than
    producing an aggregate, so a nested lag composes into a single lookup.

    **A missing figure stays missing.** Every division — the ``/`` operator as
    well as ``SAFE_DIV`` — goes through :func:`_ratio`, so a zero or absent
    denominator gives NO VALUE rather than zero, "flat", or a database error that
    would fail the whole result. ``IF`` names BOTH branches explicitly instead of
    using ``ELSE``, so a condition that cannot be evaluated (a figure the bank
    never supplied) yields no value either, rather than quietly taking the false
    branch and reading as a real answer.
    """
    if isinstance(node, expr.NumberLiteral):
        return literal(node.value, Numeric(38, 10))
    if isinstance(node, expr.MemberReference):
        return parts[(node.member_id, offset)].finalize(cols)
    if isinstance(node, expr.Negation):
        return -_evaluate(node.operand, cols, parts, offset)
    if isinstance(node, expr.Arithmetic):
        left = _evaluate(node.left, cols, parts, offset)
        right = _evaluate(node.right, cols, parts, offset)
        if node.operator == "+":
            return left + right
        if node.operator == "-":
            return left - right
        if node.operator == "*":
            return left * right
        return _ratio(left, right)
    if isinstance(node, expr.Comparison):
        return _compared(
            node.operator,
            _evaluate(node.left, cols, parts, offset),
            _evaluate(node.right, cols, parts, offset),
        )
    if isinstance(node, expr.Logical):
        left = _evaluate(node.left, cols, parts, offset)
        right = _evaluate(node.right, cols, parts, offset)
        return and_(left, right) if node.operator == "and" else or_(left, right)
    if isinstance(node, expr.LogicalNot):
        return not_(_evaluate(node.operand, cols, parts, offset))
    if isinstance(node, expr.SafeDivide):
        return _ratio(
            _evaluate(node.numerator, cols, parts, offset),
            _evaluate(node.denominator, cols, parts, offset),
        )
    if isinstance(node, expr.Conditional):
        condition = _evaluate(node.condition, cols, parts, offset)
        return case(
            (condition, _evaluate(node.when_true, cols, parts, offset)),
            (not_(condition), _evaluate(node.when_false, cols, parts, offset)),
        )
    if isinstance(node, expr.PercentChange):
        now = _evaluate(node.operand, cols, parts, offset)
        before = _evaluate(node.operand, cols, parts, offset + 1)
        return _ratio(now - before, before)
    lag = cast(expr.Lag, node)
    return _evaluate(lag.operand, cols, parts, offset + lag.periods)


def _compared(
    operator: str, left: ColumnElement[Any], right: ColumnElement[Any]
) -> ColumnElement[Any]:
    """One comparison, as SQL. NULL on either side is SQL's own unknown."""
    if operator == "=":
        return left == right
    if operator == "!=":
        return left != right
    if operator == "<":
        return left < right
    if operator == "<=":
        return left <= right
    if operator == ">":
        return left > right
    return left >= right


def _weight_of(cat: Catalogue, measure: MeasureDef) -> MeasureDef:
    if measure.weight is None:
        raise InvalidQuery(f"{measure.id} is a weighted average with no weight.")
    weight = cast(MeasureDef, _lookup(cat, measure.weight))
    if weight.aggregation not in _ADDITIVE or weight.aggregation == "count":
        raise InvalidQuery(f"{measure.id} weights by a non-additive measure.")
    return weight


def _ratio_parts(cat: Catalogue, measure: MeasureDef) -> tuple[MeasureDef, MeasureDef]:
    if measure.numerator is None or measure.denominator is None:
        raise InvalidQuery(f"{measure.id} is a ratio with no numerator or denominator.")
    parts = (
        cast(MeasureDef, _lookup(cat, measure.numerator)),
        cast(MeasureDef, _lookup(cat, measure.denominator)),
    )
    for part in parts:
        if part.aggregation not in _ADDITIVE:
            raise InvalidQuery(f"{measure.id} composes {part.id}, which is not additive.")
    return parts


# --- filters ------------------------------------------------------------------------------


def _expected_type(kind: Any) -> str:  # noqa: PLR0911 - one return per column type
    """Production copy for what a column's filter value must be."""
    if isinstance(kind, Boolean):
        return "true or false"
    if isinstance(kind, String | Text):
        return "text"
    if isinstance(kind, Integer):
        return "a whole number"
    if isinstance(kind, Numeric):
        return "a number"
    if isinstance(kind, DateTime):
        return "a timestamp"
    if isinstance(kind, Date):
        return "a date (YYYY-MM-DD)"
    if isinstance(kind, Uuid):
        return "an identifier"
    return "a value of the column's type"


def _coerce(  # noqa: PLR0911, PLR0912 - one branch per column type
    column: ColumnElement[Any], value: BiFilterValue, member_id: str
) -> Any:
    """A wire value as the column's Python type, or ``InvalidQuery``.

    The refusal names the type the member expects and never echoes the value:
    a client string must not travel back through an error message either.
    """
    kind = column.type

    def refuse() -> InvalidQuery:
        return InvalidQuery(f"{member_id} expects {_expected_type(kind)}.", members=(member_id,))

    if isinstance(kind, Boolean):
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in {"true", "false"}:
            return value.lower() == "true"
        raise refuse()
    if isinstance(value, bool):
        raise refuse()
    if isinstance(kind, String | Text):
        if isinstance(value, str):
            return value
        raise refuse()
    if isinstance(kind, Integer):
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                raise refuse() from None
        raise refuse()
    if isinstance(kind, Numeric):
        if isinstance(value, int | float | str):
            try:
                number = Decimal(str(value))
            except InvalidOperation:
                raise refuse() from None
            if not number.is_finite():
                raise refuse()
            return number
        raise refuse()
    if isinstance(kind, DateTime):
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                raise refuse() from None
        raise refuse()
    if isinstance(kind, Date):
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError:
                raise refuse() from None
        raise refuse()
    if isinstance(kind, Uuid):
        if isinstance(value, str):
            try:
                return UUID(value)
            except ValueError:
                raise refuse() from None
        raise refuse()
    raise refuse()


def _filter_predicate(  # noqa: PLR0911, PLR0912 - one branch per operator
    column: ColumnElement[Any], spec: _FilterSpec
) -> ColumnElement[Any]:
    member_id = spec.dimension.id
    op = spec.filter.op
    kind = column.type
    if op == "contains" and not isinstance(kind, String | Text):
        raise InvalidQuery(
            f"contains applies to text; {member_id} is not text.", members=(member_id,)
        )
    if op in _ORDERED_OPS and isinstance(kind, Boolean | Uuid):
        raise InvalidQuery(f"{op} does not apply to {member_id}.", members=(member_id,))
    values = [_coerce(column, value, member_id) for value in spec.filter.values]
    if op == "is_null":
        return column.is_(None)
    if op == "not_null":
        return column.is_not(None)
    if op == "in":
        return column.in_(values)
    if op == "not_in":
        return column.not_in(values)
    if op == "between":
        return column.between(values[0], values[1])
    if op == "contains":
        return column.contains(values[0], autoescape=True)
    value = values[0]
    if op == "eq":
        return column == value
    if op == "ne":
        return column != value
    if op == "gt":
        return column > value
    if op == "gte":
        return column >= value
    if op == "lt":
        return column < value
    return column <= value


# --- assembly -----------------------------------------------------------------------------


@dataclass(slots=True)
class _Build:
    """Everything one grouped select needs; ``_grouped`` turns it into SQL."""

    cat: Catalogue
    resolved: _Resolved
    organization_id: str
    bank_id: str
    dialect: str
    aggregate: bool
    #: Pivot values, in column order, once fetched.
    pivot_values: tuple[Any, ...] = ()
    #: The ranked Top-N keys as a select, once built.
    top_keys: Select[Any] | None = None
    #: Only ``other=True`` collapses; otherwise Top-N is a plain restriction.
    top_other: bool = True


@dataclass(frozen=True, slots=True)
class _Grouped:
    """A grouped select and the aliases of its keys and measure columns."""

    statement: Select[Any] | CompoundSelect
    key_aliases: tuple[str, ...]
    #: ``(alias, output, pivot value)`` per measure column.
    measure_aliases: tuple[tuple[str, _Emitted, Any | None], ...]
    has_level: bool


def _new_source(build: _Build) -> _Source:
    return _AggregateSource() if build.aggregate else _Source(_TABLES[build.resolved.fact])


def _tenant_predicates(source: _Source, build: _Build) -> list[ColumnElement[Any]]:
    return [
        source.fact.c["organization_id"] == build.organization_id,
        source.fact.c["bank_id"] == build.bank_id,
    ]


def _time_predicate(
    source: _Source, build: _Build, window: _Window, time_dimensions: Sequence[DimensionDef]
) -> ColumnElement[Any]:
    """``as_of`` → equality; a flow range → BETWEEN; a stock range → the last
    date with data per requested time grain (``bi_dim_date``)."""
    date_column = source.date_column
    if window.single:
        return date_column == window.start
    if not build.resolved.stock:
        return date_column.between(window.start, window.end)
    calendar = _TABLES[DATE_TABLE]
    last_dates = select(func.max(calendar.c["date"])).where(
        calendar.c["organization_id"] == build.organization_id,
        calendar.c["bank_id"] == build.bank_id,
        calendar.c["has_data"].is_(true()),
        calendar.c["date"].between(window.start, window.end),
    )
    # The static bound is REDUNDANT and that is the point: every date the
    # subquery can return is a `max()` taken from inside this same window, so
    # ANDing it cannot change the answer — and without it the planner cannot
    # prune partitions. Postgres decides partition pruning at PLAN time, and a
    # subquery or join condition is not known then, so `IN (subquery)` makes a
    # twelve-month question scan every month the mart holds. Measured by the
    # benchmark: 60 partitions and 124,348 buffers became 12 partitions at 4.3×
    # less I/O, 469 ms to 97 ms, for a byte-identical result over 797,207 rows.
    bound = date_column.between(window.start, window.end)
    if not time_dimensions:
        return and_(bound, date_column == last_dates.scalar_subquery())
    grains = [calendar.c[dimension.column] for dimension in time_dimensions]
    return and_(bound, date_column.in_(last_dates.group_by(*grains)))


def _where(
    source: _Source, build: _Build, time_predicate: ColumnElement[Any]
) -> list[ColumnElement[Any]]:
    """Tenant keys (the caller's), the window, then every user and injected filter."""
    predicates = _tenant_predicates(source, build)
    predicates.append(time_predicate)
    for spec in build.resolved.filters:
        predicates.append(_filter_predicate(source.resolve(spec.dimension), spec))
    return predicates


def _key_expression(source: _Source, build: _Build, dimension: DimensionDef) -> ColumnElement[Any]:
    column = source.resolve(dimension)
    if dimension != build.resolved.top_n_dimension or build.top_keys is None:
        return column
    if not build.top_other:
        return column
    # A NULL key is "no value", not one of the others: it keeps its own row.
    return case(
        (column.in_(build.top_keys), sql_cast(column, String)),
        (column.is_(None), null()),
        else_=literal(BI_TOP_N_OTHER_LABEL, String),
    )


def _grouped(  # noqa: PLR0912, PLR0913, PLR0915 - the one assembly site, kept linear
    build: _Build,
    window: _Window,
    dimensions: Sequence[DimensionDef],
    measures: Sequence[_Emitted],
    *,
    prior: _Window | None = None,
    rollup: bool,
    pivot: bool,
) -> _Grouped:
    """One grouped select: keys ``k0..``, ``__level`` when rolled up, then the
    measure columns ``m0..`` (``m0_0..`` when pivoted), each followed by its
    ``_prior`` / ``_delta`` / ``_delta_pct`` companions when ``prior`` is set.

    A comparison is a PERIOD AXIS inside the one select — every measure
    component is aggregated once under the current window's predicate and
    once under the prior's — never a join of two grouped results: Postgres
    cannot FULL JOIN on ``IS NOT DISTINCT FROM`` (it is neither hash- nor
    merge-joinable), and grouping the union of both periods' rows gives the
    same key set and the same NULL-key handling with a single scan.
    """
    resolved = build.resolved
    source = _new_source(build)
    time_dimensions = [d for d in dimensions if d.table == DATE_TABLE]
    if (
        pivot
        and resolved.pivot_dimension is not None
        and resolved.pivot_dimension.table == DATE_TABLE
    ):
        time_dimensions.append(resolved.pivot_dimension)
    key_exprs = [_key_expression(source, build, dimension) for dimension in dimensions]
    key_aliases = tuple(f"k{i}" for i in range(len(dimensions)))
    over_column = (
        None if resolved.over_dimension is None else source.resolve(resolved.over_dimension)
    )
    pivot_column = (
        source.resolve(resolved.pivot_dimension)
        if pivot and resolved.pivot_dimension is not None
        else None
    )
    current_period = _time_predicate(source, build, window, time_dimensions)
    periods: list[tuple[str, ColumnElement[Any] | None]] = [("", None)]
    in_window = current_period
    if prior is not None:
        prior_period = _time_predicate(source, build, prior, time_dimensions)
        periods = [("", current_period), ("p", prior_period)]
        in_window = or_(current_period, prior_period)

    # The periods a calculated measure declares (D-195), each a window of its own,
    # derived from the query's reporting date by calendar arithmetic on the grain.
    #
    # Period 0 is the WHOLE declared period up to the reporting date, and that is
    # not the same thing as the reporting date: a stock reads the period's closing
    # book (which is what the date is, because the date has to end its period —
    # ``_require_period_end``), while a flow is added up across it. Comparing a
    # day's flow with a whole month's would be out by a whole period, which is the
    # error D-195 names. Plain measures keep the query's own window and therefore
    # gain an explicit predicate here, because the WHERE now admits every period's
    # rows and a component with no predicate would sum across all of them.
    lagged: dict[int, ColumnElement[Any] | None] = {0: None}
    if resolved.grain is not None:
        periods = [("", current_period)]
        windows = {
            offset: _time_predicate(
                source, build, _period_window(window.end, resolved.grain, offset), time_dimensions
            )
            for offset in resolved.periods
        }
        lagged = dict(windows)
        in_window = or_(current_period, *windows.values())

    # Measure plans, one per (output, pivot value, period).
    plans: list[tuple[str, _Emitted, Any | None, str, _MeasurePlan]] = []
    for j, measure in enumerate(measures):
        cells: list[tuple[str, Any | None, ColumnElement[Any] | None]] = (
            [
                (f"m{j}_{i}", value, pivot_column == value)
                for i, value in enumerate(build.pivot_values)
            ]
            if pivot_column is not None
            else [(f"m{j}", None, None)]
        )
        for alias, value, pivot_predicate in cells:
            for suffix, period_predicate in periods:
                if isinstance(measure, _Calculated):
                    plan = _plan_calculated(
                        build.cat,
                        source,
                        measure,
                        f"{alias}{suffix}",
                        pivot_predicate,
                        lagged if measure.grain is not None else {0: period_predicate},
                    )
                else:
                    plan = _plan_measure(
                        build.cat,
                        source,
                        measure,
                        f"{alias}{suffix}",
                        _all(pivot_predicate, period_predicate),
                        over_column,
                    )
                plans.append((alias, measure, value, suffix, plan))

    where = _where(source, build, in_window)
    if build.top_keys is not None and not build.top_other and resolved.top_n_dimension is not None:
        where.append(source.resolve(resolved.top_n_dimension).in_(build.top_keys))

    def _statement(columns: Sequence[ColumnElement[Any]]) -> Select[Any]:
        return source.select(columns, where)

    if over_column is not None:
        # Level 1: the fact grouped by keys + the concentration dimension.
        first = _statement(
            [
                *[
                    column.label(alias)
                    for column, alias in zip(key_exprs, key_aliases, strict=True)
                ],
                over_column.label("over"),
                *[
                    component.expr.label(component.alias)
                    for _, _, _, _, plan in plans
                    for component in plan.components
                ],
            ]
        ).group_by(*key_exprs, over_column)
        level_one = first.subquery("l1")
        # Level 2: re-aggregate additive components, compute concentration ones.
        components: dict[str, ColumnElement[Any]] = {}
        for _, _, _, _, plan in plans:
            if plan.second_level is None:
                for component in plan.components:
                    components[component.alias] = func.sum(level_one.c[component.alias])
            else:
                inputs: dict[str, ColumnElement[Any]] = {
                    component.alias: level_one.c[component.alias] for component in plan.components
                }
                inputs["over"] = level_one.c["over"]
                for component in plan.second_level(inputs):
                    components[component.alias] = component.expr
        key_exprs = [level_one.c[alias] for alias in key_aliases]

        def _statement(columns: Sequence[ColumnElement[Any]]) -> Select[Any]:
            return select(*columns).select_from(level_one)

    else:
        components = {
            component.alias: component.expr
            for _, _, _, _, plan in plans
            for component in plan.components
        }

    group_exprs: list[ColumnElement[Any]] = list(key_exprs)
    finalized = {(alias, suffix): plan.finalize(components) for alias, _, _, suffix, plan in plans}
    value_columns: list[ColumnElement[Any]] = []
    aliases: list[tuple[str, _Emitted, Any | None]] = []
    for alias, measure, value, suffix, _ in plans:
        if suffix:
            continue
        current = finalized[(alias, "")]
        value_columns.append(current.label(alias))
        aliases.append((alias, measure, value))
        if prior is not None:
            previous = finalized[(alias, "p")]
            value_columns.extend(
                (
                    previous.label(f"{alias}_prior"),
                    (current - previous).label(f"{alias}_delta"),
                    (_ratio(current - previous, previous) * 100).label(f"{alias}_delta_pct"),
                )
            )
    key_columns = [
        column.label(alias) for column, alias in zip(key_exprs, key_aliases, strict=True)
    ]
    measure_aliases = tuple(aliases)

    if not rollup:
        statement = _statement([*key_columns, *value_columns])
        if group_exprs:
            statement = statement.group_by(*group_exprs)
        return _Grouped(statement, key_aliases, measure_aliases, has_level=False)

    depth = len(group_exprs)
    if build.dialect == "postgresql":
        level: ColumnElement[Any] = literal(depth, Integer)
        for group_expr in group_exprs:
            level = level - func.grouping(group_expr)
        statement = _statement([*key_columns, level.label(_LEVEL_MARKER), *value_columns])
        statement = statement.group_by(func.rollup(*group_exprs))
        return _Grouped(statement, key_aliases, measure_aliases, has_level=True)
    # SQLite has no ROLLUP: one select per level, leaf level first so the
    # UNION's column types come from real columns.
    levels: list[Select[Any]] = []
    for kept in range(depth, -1, -1):
        keys = [
            (column.label(alias) if i < kept else null().label(alias))
            for i, (column, alias) in enumerate(zip(key_exprs, key_aliases, strict=True))
        ]
        marker = literal(kept, Integer).label(_LEVEL_MARKER)
        level_select = _statement([*keys, marker, *value_columns])
        if kept:
            level_select = level_select.group_by(*group_exprs[:kept])
        levels.append(level_select)
    return _Grouped(union_all(*levels), key_aliases, measure_aliases, has_level=True)


def _top_keys(build: _Build) -> Select[Any]:
    """The Top-N dimension's ``n`` largest values by the first measure."""
    resolved = build.resolved
    assert resolved.top_n is not None and resolved.top_n_dimension is not None
    ranked = _grouped(
        build,
        resolved.current,
        [resolved.top_n_dimension],
        [resolved.outputs[0]],
        rollup=False,
        pivot=False,
    )
    statement = cast(Select[Any], ranked.statement)
    measure_alias = ranked.measure_aliases[0][0]
    subquery = (
        statement.order_by(statement.selected_columns[measure_alias].desc().nulls_last())
        .limit(resolved.top_n.n)
        .subquery("topn")
    )
    return select(subquery.c["k0"])


def _pivot_values(db: Session, build: _Build) -> tuple[Any, ...]:
    """The pivot dimension's distinct values in the window, capped at the column limit."""
    resolved = build.resolved
    assert resolved.pivot is not None and resolved.pivot_dimension is not None
    cap = min(resolved.pivot.max_columns, BI_PIVOT_MAX_COLUMNS)
    source = _new_source(build)
    column = source.resolve(resolved.pivot_dimension)
    time_dimensions = (
        [resolved.pivot_dimension] if resolved.pivot_dimension.table == DATE_TABLE else []
    )
    in_window = _time_predicate(source, build, resolved.current, time_dimensions)
    where = [*_where(source, build, in_window), column.is_not(None)]
    statement = (
        source.select([column.label("v")], where).group_by(column).order_by(column).limit(cap + 1)
    )
    rows = execution.run_select(
        db,
        statement,
        organization_id=build.organization_id,
        timeout_ms=get_settings().bi.interactive_timeout_ms,
    )
    if len(rows) > cap:
        raise InvalidQuery(
            f"{resolved.pivot_dimension.id} has more than {cap} values in this window; "
            "filter it or pivot a coarser dimension.",
            members=(resolved.pivot_dimension.id,),
        )
    if not rows and not resolved.dimensions:
        # A select needs at least one column; with no values and no row
        # dimensions there is nothing to emit.
        raise InvalidQuery(
            f"{resolved.pivot_dimension.id} has no values in this window; nothing to pivot.",
            members=(resolved.pivot_dimension.id,),
        )
    return tuple(row[0] for row in rows)


def _require_period_end(db: Session, build: _Build, grain: expr.PeriodGrain) -> None:
    """The reporting date must be the last date WITH DATA in its ``grain`` period.

    The other half of D-195. A measure that compares each quarter with the one
    before it is only a quarter-on-quarter figure when the date it is read at ends
    a quarter; read three weeks into a quarter it would compare a part period with
    a whole one, and come out a whole period off — which is the defect class the
    targets work already paid for.

    "Ends the period" is the platform's own semi-additive rule, not a new one: it
    is ``bi_dim_date.is_last_in_<grain>``, the LAST DATE THE BANK FED in that
    period (D-014), which is what every stock measure at that grain already reads.
    So a genuine period end qualifies, and so does the institution's latest book —
    and an arbitrary date in the middle of a closed period does not.

    The refusal names the nearest date that would work, the way
    ``no_computed_position`` does, and never substitutes it: choosing a different
    reporting date on the reader's behalf is how a figure comes to be about a day
    nobody asked about.
    """
    as_of = build.resolved.current.end
    calendar = _TABLES[DATE_TABLE]
    statement = select(func.max(calendar.c["date"])).where(
        calendar.c["organization_id"] == build.organization_id,
        calendar.c["bank_id"] == build.bank_id,
        calendar.c[f"is_last_in_{grain}"].is_(true()),
        calendar.c["date"] <= as_of,
    )
    rows = execution.run_select(
        db,
        statement,
        organization_id=build.organization_id,
        timeout_ms=get_settings().bi.interactive_timeout_ms,
    )
    nearest = rows[0][0] if rows else None
    if nearest == as_of:
        return
    period = expr.GRAIN_LABELS[grain]
    named = tuple(output.id for output in build.resolved.calculated)
    if nearest is None:
        raise InvalidQuery(
            f"This result compares each {period} with the {period} before it, and this "
            f"institution has no {period} end on or before the date asked for.",
            members=named,
        )
    raise InvalidQuery(
        f"This result compares each {period} with the {period} before it, so it can only "
        f"be read at the close of a {period}. The most recent one on or before the date "
        f"asked for is {nearest.isoformat()}.",
        members=named,
    )


_COMPARISON_ROLES: tuple[BiComparisonRole, ...] = ("current", "prior", "delta", "delta_pct")


def _column_specs(
    resolved: _Resolved, grouped: _Grouped, *, compared: bool
) -> tuple[ColumnSpec, ...]:
    specs: list[ColumnSpec] = [
        ColumnSpec(
            id=dimension.id,
            label=dimension.label,
            kind="dimension",
            format=(
                "text"
                if dimension == resolved.top_n_dimension
                and resolved.top_n is not None
                and resolved.top_n.other
                else dimension.value_type
            ),
            member_id=dimension.id,
        )
        for dimension in resolved.dimensions
    ]
    if grouped.has_level:
        specs.append(ColumnSpec(id=_LEVEL_MARKER, label="Level", kind="marker", format="int"))
    role_suffix: dict[BiComparisonRole, str] = {
        "current": "",
        "prior": f"{_PIVOT_SEPARATOR}prior",
        "delta": f"{_PIVOT_SEPARATOR}delta",
        "delta_pct": f"{_PIVOT_SEPARATOR}delta_pct",
    }
    role_label: dict[BiComparisonRole, str] = {
        "current": "",
        "prior": " (prior)",
        "delta": " (change)",
        "delta_pct": " (change %)",
    }
    for _, measure, value in grouped.measure_aliases:
        base_id = measure.id if value is None else f"{measure.id}{_PIVOT_SEPARATOR}{value}"
        base_label = measure.label if value is None else f"{measure.label} · {value}"
        pivot_value = None if value is None else str(value)
        if not compared:
            specs.append(
                ColumnSpec(
                    id=base_id,
                    label=base_label,
                    kind="measure",
                    format=measure.value_type,
                    member_id=measure.id,
                    pivot_value=pivot_value,
                )
            )
            continue
        for role in _COMPARISON_ROLES:
            specs.append(
                ColumnSpec(
                    id=f"{base_id}{role_suffix[role]}",
                    label=f"{base_label}{role_label[role]}",
                    kind="measure",
                    format="pct" if role == "delta_pct" else measure.value_type,
                    member_id=measure.id,
                    role=role,
                    pivot_value=pivot_value,
                )
            )
    return tuple(specs)


def _ordered(
    statement: Select[Any], resolved: _Resolved, grouped: _Grouped, q: BiQuery
) -> Select[Any]:
    """ORDER BY from the sort whitelist, or the keys, with subtotal levels first.

    NULL placement is always explicit: Postgres puts NULLs first on DESC and
    last on ASC, SQLite treats NULL as the smallest value either way, so an
    unspecified order would page differently per dialect. NULLs go LAST —
    a group with no value trails the ranked ones — except that a rolled-up
    key sorts NULLS FIRST, so a subtotal precedes its children.
    """
    selected = statement.selected_columns
    key_by_member = {
        dimension.id: selected[alias]
        for dimension, alias in zip(resolved.dimensions, grouped.key_aliases, strict=True)
    }
    measure_by_member = {
        measure.id: selected[alias]
        for alias, measure, value in grouped.measure_aliases
        if value is None
    }
    clauses: list[ColumnElement[Any]] = []
    if grouped.has_level:
        clauses.append(selected[_LEVEL_MARKER].asc())
    if q.sort:
        for sort in q.sort:
            # Membership, never truthiness: ``a or b`` evaluates ``bool(a)``, and
            # a SQLAlchemy Label RAISES on that ("Boolean value of this clause is
            # not defined"), so the ``.get(...) or ...`` form crashed every query
            # that sorted by a dimension — including the only sort a pivoted
            # result allows (audit A5-01).
            column = (
                key_by_member[sort.member]
                if sort.member in key_by_member
                else measure_by_member[sort.member]
            )
            ordered = column.asc() if sort.direction == "asc" else column.desc()
            clauses.append(
                ordered.nulls_first()
                if grouped.has_level and sort.member in key_by_member
                else ordered.nulls_last()
            )
    else:
        clauses.extend(
            column.asc().nulls_first() if grouped.has_level else column.asc().nulls_last()
            for column in key_by_member.values()
        )
    return statement.order_by(*clauses) if clauses else statement


def compile_query(  # noqa: PLR0913 - the contract signature (bi_contracts.md)
    db: Session,
    cat: Catalogue,
    q: BiQuery,
    *,
    organization_id: str,
    bank_id: str,
    injected_filters: Sequence[BiFilter] = (),
) -> CompiledQuery:
    """Compile ``q`` for one tenant institution.

    ``organization_id`` / ``bank_id`` are the caller's (the authenticated
    principal's institution) and become the first two predicates of every
    select; ``injected_filters`` are the caller's data scope (wave 4) and are
    appended to the WHERE of every select the query compiles to — the request
    cannot name, weaken or remove them. ``db`` is used only for the pivot
    value probe and to learn the dialect.
    """
    retention_days = get_settings().bi.daily_retention_days
    certified = _load_certified(db, q, cat, organization_id=organization_id, bank_id=bank_id)
    resolved = _resolve(cat, q, injected_filters, certified, retention_days)
    dialect = db.get_bind().dialect.name
    build = _Build(
        cat=cat,
        resolved=resolved,
        organization_id=organization_id,
        bank_id=bank_id,
        dialect=dialect,
        aggregate=aggregate_table_covers(cat, resolved),
    )
    if resolved.grain is not None:
        _require_period_end(db, build, resolved.grain)
    if resolved.top_n is not None:
        build.top_other = resolved.top_n.other
        build.top_keys = _top_keys(build)
    if resolved.pivot is not None:
        build.pivot_values = _pivot_values(db, build)
    # A rollup over nothing is the plain total; ROLLUP() is not valid SQL.
    rollup = q.subtotals and bool(resolved.dimensions)

    current = _grouped(
        build,
        resolved.current,
        resolved.dimensions,
        resolved.outputs,
        prior=resolved.prior,
        rollup=rollup,
        pivot=resolved.pivot is not None,
    )
    if isinstance(current.statement, CompoundSelect):
        compound = current.statement.subquery("levels")
        statement = select(*compound.c).select_from(compound)
    else:
        statement = current.statement

    statement = _ordered(statement, resolved, current, q)
    return CompiledQuery(
        select=statement,
        columns=_column_specs(resolved, current, compared=resolved.prior is not None),
        organization_id=organization_id,
        bank_id=bank_id,
        fact_table=_AGG_TABLE if build.aggregate else resolved.fact,
        used_aggregate=build.aggregate,
        member_ids=resolved.member_ids,
        injected_member_ids=resolved.injected_member_ids,
        requested_limit=q.limit,
        offset=q.offset,
    )
