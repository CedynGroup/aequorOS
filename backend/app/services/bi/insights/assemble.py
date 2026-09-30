"""Building a fact sheet for one institution and one reporting date.

The insights layer (``facts``, ``drivers``, ``projections``, ``rules``,
``statements``) is pure: it turns a :class:`~app.services.bi.insights.facts.
FactSheet` into statements and reads nothing else. Nothing assembled that sheet
from a bank and a date, and this module is that missing half. It is the ONLY
place in the package that touches a session, and it is deliberately thin: it
reads, it builds facts, and it hands them to ``rules.derive_insights``.

Four properties are the reason it is shaped the way it is.

**It reads through the query path's own compiler.** Every figure comes from
``compile_query`` + ``execute`` over a ``BiQuery`` — the same statement the
``/bi/query`` route would run for the same members — so an insight and the chart
beside it can never disagree about what a measure IS. There is no second
aggregation anywhere in this file: no ``select(func.sum(...))``, no SQL, no
mart-specific knowledge beyond the catalogue's own declarations.

**A missing figure cannot become a zero or a "flat".** The compiler returns
``NULL`` for a measure the book cannot answer, this module turns that into
``None``, and ``None`` reaches only the ``missing_reason`` argument of a fact
constructor — never a value. :class:`~app.services.bi.insights.facts.
MovementFact` is built ONLY when both dates produced a figure; when the current
date has none, the fact is an :class:`~app.services.bi.insights.facts.
ObservedFact` carrying the reason, which the ``data_gap`` rule states in words.
There is no ``or 0``, no ``?? 0`` and no default in this file, and a test reads
its source to keep it that way.

**Authorization is per measure, through the query path's own decision.** One
``authorize_query`` pass over every candidate measure returns the complete set
of denied member ids; a candidate is read only when neither it nor any member it
is composed from is in that set. A reader who may not see a measure gets no
statement mentioning it, and the response says only HOW MANY were withheld —
never which, because naming a withheld measure is the disclosure the refusal
exists to prevent.

**Designation is the book's, not the sentence's.** Every fact is built through
``facts.py``'s constructors, which take the label, value type, direction,
advisory designation and ``certified`` verdict from the catalogue ``MeasureDef``.
An advisory figure therefore cannot be presented as a certified one, because the
sentence never gets to choose.

**Which measures earn a headline.** Not a list in code: the candidates are
derived from the catalogue (certified engine figures that this institution's
metric authority actually resolves to, plus the portfolio ratios that can be
bridged), ordered so that each authorization module contributes its first figure
before any module contributes a second, and capped. That ordering is what stops
one module's four earnings-at-risk variants crowding out every other module's
headline ratio, and it needs no curation of its own.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import Permission
from app.db.base import utc_now
from app.domain.bi.authority import resolve_authority
from app.domain.bi.catalogue import Catalogue, MeasureDef
from app.domain.bi.catalogue.engine import engine_measure_id
from app.models import Bank
from app.schemas.bi import (
    BI_MAX_MEASURES,
    BiDateRange,
    BiFilter,
    BiQuery,
    BiTime,
    shift_months,
)
from app.services import institution_types
from app.services.bi import data_scope, provenance
from app.services.bi.authorization import (
    ALL_INSTITUTION_DATA,
    BiAuthorization,
    BiDataScope,
    authorize_query,
    query_members,
)
from app.services.bi.compiler import compile_query
from app.services.bi.errors import BiQueryError
from app.services.bi.execution import execute
from app.services.bi.insights.drivers import ratio_bridge
from app.services.bi.insights.facts import (
    Fact,
    FactProvenance,
    FactSheet,
    bridge_fact,
    fact_sheet,
    movement_fact,
    observed_fact,
    projection_fact,
)
from app.services.bi.insights.projections import MIN_OBSERVATIONS, Observation, project
from app.services.bi.insights.rules import (
    DEFAULT_POLICY,
    InsightPolicy,
    InsightSet,
    derive_insights,
)

__all__ = [
    "AssembledInsights",
    "HEADLINE_MEASURE_CAP",
    "MAX_COMPILED_READS",
    "PROJECTION_HORIZON_MONTHS",
    "SERIES_WINDOW_MONTHS",
    "TIME_DATE_DIMENSION",
    "assemble",
    "default_compare_to",
    "engine_measure_applies",
    "engine_regime",
    "headline_measures",
]

#: The trend dimension a series read groups by. One id, named once: it is the
#: only dimension this module ever asks for, and it carries its own
#: authorization sentence like any other member.
TIME_DATE_DIMENSION = "time.date"

#: The most headline measures one insights read will consider. The presentation
#: cap that decides how many SENTENCES a reader gets is
#: ``InsightPolicy.max_insights``; this one bounds how much is READ, which is a
#: different question and belongs on the read side.
HEADLINE_MEASURE_CAP = 12

#: How many compiled statements one insights read may run, whatever the
#: catalogue grows to. The read budget (``query_log``) charges an insights
#: request ONCE — it is one thing the reader asked for — so the protection
#: against that request becoming expensive has to be structural, here.
MAX_COMPILED_READS = 8

#: The trailing window a projection's trend is drawn through, and how far ahead
#: it is extended. Both are presentation choices of this surface, stated once:
#: ``projections.py`` owns the arithmetic and refuses rather than guessing when
#: the window does not support a line.
SERIES_WINDOW_MONTHS = 12
PROJECTION_HORIZON_MONTHS = 3


def default_compare_to(as_of: date) -> date:
    """The earlier period a movement is measured against, by default.

    The month before ``as_of`` on the pack windows' own end-of-month convention
    (``app/schemas/bi.py``), so a widget's prior column and the insight strip
    beside it cannot compare against different dates.
    """
    return shift_months(as_of, -1)


# ---------------------------------------------------------------------------
# which engine measures are this institution's at all
# ---------------------------------------------------------------------------


def engine_regime(measure: MeasureDef) -> str | None:
    """The regime encoded in an engine measure id, or ``None`` if it is not one.

    The id is composed by ``engine_measure_id``; this inverts it and checks the
    round trip against that same composer, so the convention has one owner.
    """
    rule = measure.engine_rule
    if rule is None:
        return None
    prefix = f"engine.{rule.metric_id}."
    suffix = f".{rule.tier}"
    if not (measure.id.startswith(prefix) and measure.id.endswith(suffix)):
        return None
    regime = measure.id[len(prefix) : -len(suffix)]
    if not regime or engine_measure_id(rule.metric_id, regime, rule.tier) != measure.id:
        return None
    return regime


def engine_measure_applies(
    measure: MeasureDef, *, institution_class: str, capital_regime: str
) -> bool:
    """Whether ``measure``'s authority is the one THIS institution's figure has.

    An engine measure is keyed by ``(metric_id, regime)`` because a metric under
    two regimes is two different laws (``domain/authority/registry.py``). The
    mart builder stamps each copied row with the regime of the authority that
    RESOLVED for the tenant (``domain/bi/extract.py::engine_metric_row``), so a
    measure whose regime is not that one can never have a row for this
    institution — it could only ever read as no value. The same registry
    resolution is asked here, which is why this is not a new notion of "is this
    an SDI": a class-neutral authority (an accounting standard) resolves for both
    classes and its measure applies to both, and a class-specific one does not.

    A member that names no engine rule at all applies: it reads the
    institution's own book, which has no regime. A member that DOES name one but
    whose id does not yield a regime is refused rather than admitted — an engine
    figure whose authority cannot be identified is exactly the figure that must
    not be asserted about an institution.
    """
    rule = measure.engine_rule
    if rule is None:
        return True
    regime = engine_regime(measure)
    if regime is None:
        return False
    entry = resolve_authority(
        rule.metric_id, regime=capital_regime, institution_class=institution_class
    )
    return entry is not None and entry.regime.value == regime


# ---------------------------------------------------------------------------
# which measures earn a headline
# ---------------------------------------------------------------------------

#: Aggregations whose measure decomposes into a numerator and a denominator, so
#: a move in it can be attributed exactly (``drivers.ratio_bridge``).
_BRIDGEABLE_AGGREGATIONS: frozenset[str] = frozenset({"ratio_of_sums", "share"})


def _is_bridgeable(measure: MeasureDef) -> bool:
    return (
        measure.aggregation in _BRIDGEABLE_AGGREGATIONS
        and measure.numerator is not None
        and measure.denominator is not None
    )


def headline_measures(
    cat: Catalogue,
    *,
    institution_class: str,
    capital_regime: str,
    cap: int = HEADLINE_MEASURE_CAP,
) -> tuple[MeasureDef, ...]:
    """The measures worth a headline for one institution, in reading order.

    Two families qualify, and both come from the catalogue's OWN accessors —
    never from a list of ids and never from a table name spelled here:

    * ``cat.engine_measures()`` filtered to the **certified** ones (a copy of a
      filed, sealed-tier figure) whose authority resolves for this institution;
    * ``cat.portfolio_measures()`` filtered to the **bridgeable ratios**, because
      a ratio is the only thing the exact bridge can attribute and an engine copy
      is a ``last_value`` with no components to attribute to.

    Using those two accessors is what keeps the ``.target`` / ``.variance`` /
    ``.attainment_pct`` variants out: they read the target mart, they are
    ``portfolio`` in kind but not portfolio measures, and the catalogue excludes
    them structurally rather than by the shape of their ids. A variance against a
    register the institution has not fed is not a headline — it is a dash.

    A measure with no favourable direction is excluded: there is no statement to
    make about a figure the catalogue declines to judge. A measure that is
    another candidate's numerator or denominator is excluded too — its move is
    already stated, exactly, as a leg of that ratio's bridge.

    The order interleaves ``(module, family)``, so every module contributes its
    first certified figure AND its first bridgeable ratio before any contributes
    a second of either; inside a group, the figures carrying a governed limit
    come first, then catalogue order. Without the interleave one module's four
    earnings-at-risk variants take a third of the strip, and the bridgeable
    ratios — the only ones an attribution can be built from — never make the cut.
    """
    index = {measure.id: position for position, measure in enumerate(cat.measures())}
    certified = [
        measure
        for measure in cat.engine_measures()
        if measure.certified
        and measure.favourable_direction != "neutral"
        and engine_measure_applies(
            measure, institution_class=institution_class, capital_regime=capital_regime
        )
    ]
    bridgeable = [
        measure
        for measure in cat.portfolio_measures()
        if measure.favourable_direction != "neutral" and _is_bridgeable(measure)
    ]
    components = {
        member_id
        for measure in (*certified, *bridgeable)
        for member_id in (measure.numerator, measure.denominator)
        if member_id is not None
    }

    families: dict[tuple[str, str], list[MeasureDef]] = {}
    for family, measures in (("certified", certified), ("bridgeable", bridgeable)):
        for measure in measures:
            if measure.id in components:
                continue
            families.setdefault((measure.module, family), []).append(measure)
    for measures in families.values():
        measures.sort(
            key=lambda measure: (0 if measure.thresholds_source else 1, index[measure.id])
        )
    ordered: list[MeasureDef] = []
    round_number = 0
    while len(ordered) < cap and any(len(m) > round_number for m in families.values()):
        for measures in families.values():
            if len(measures) <= round_number:
                continue
            ordered.append(measures[round_number])
            if len(ordered) == cap:
                break
        round_number += 1
    return tuple(ordered)


# ---------------------------------------------------------------------------
# the assembled result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AssembledInsights:
    """One insights read: the statements, the sheet they came from, and the log."""

    insight_set: InsightSet
    sheet: FactSheet
    as_of: date
    compare_to: date
    #: Every catalogue member the compiled reads actually touched, for the
    #: ``bi_query_log`` row.
    member_ids: tuple[str, ...]
    #: Members the reader's access does not cover. Recorded in the log; the
    #: response reports only a COUNT.
    denied_members: tuple[str, ...]
    measures_read: int
    measures_withheld: int
    compiled_reads: int
    build_fingerprint: str | None


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Group:
    """Measures that can be read by ONE statement: one fact table, one clock."""

    table: str
    time_behaviour: str
    measures: tuple[MeasureDef, ...]
    #: The numerators and denominators the bridges need, read alongside.
    components: tuple[MeasureDef, ...]

    @property
    def measure_ids(self) -> list[str]:
        return [measure.id for measure in (*self.measures, *self.components)]


def _grouped(cat: Catalogue, measures: Sequence[MeasureDef]) -> tuple[_Group, ...]:
    """Group by (fact table, time behaviour) — the compiler's own two rules.

    A ``BiQuery`` resolves to one fact table and one time behaviour, so this is
    the largest set of measures one statement can answer. Components ride in the
    same group because a ratio's numerator and denominator are declared on the
    same table as the ratio; a group whose members would exceed the request cap
    drops its tail deterministically rather than failing validation.
    """
    buckets: dict[tuple[str, str], tuple[list[MeasureDef], list[MeasureDef]]] = {}
    for measure in measures:
        key = (measure.table, measure.time_behaviour)
        kept, components = buckets.setdefault(key, ([], []))
        extra = [
            cat.measure(member_id)
            for member_id in (measure.numerator, measure.denominator)
            if member_id is not None and _is_bridgeable(measure)
        ]
        if len(kept) + len(components) + 1 + len(extra) > BI_MAX_MEASURES:
            continue
        kept.append(measure)
        for component in extra:
            if component.id not in {existing.id for existing in components}:
                components.append(component)
    return tuple(
        _Group(
            table=table,
            time_behaviour=time_behaviour,
            measures=tuple(kept),
            components=tuple(components),
        )
        for (table, time_behaviour), (kept, components) in buckets.items()
    )


def _point_time(group: _Group, as_of: date, compare_to: date) -> BiTime:
    """The window one figure is read at, with its prior period beside it.

    A stock measure is a position at a date. A flow measure is an amount over a
    period, so it is read over the month ending at the reporting date and the
    compiler derives an equal-length prior window ending at ``compare_to`` — the
    same rule ``provenance.data_window`` states.
    """
    if group.time_behaviour == "flow":
        return BiTime(
            range=BiDateRange(start=as_of.replace(day=1), end=as_of), compare_to=compare_to
        )
    return BiTime(as_of=as_of, compare_to=compare_to)


def _series_time(as_of: date) -> BiTime:
    start = shift_months(as_of, -SERIES_WINDOW_MONTHS)
    return BiTime(range=BiDateRange(start=start, end=as_of))


def _as_decimal(value: object) -> Decimal | None:
    """A cell as a figure, or ``None`` when there is none.

    The one place a database value becomes a number. ``None`` in means ``None``
    out: there is deliberately no fallback, because a fallback here is the zero
    that a bank would read as a real result.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class _Answer:
    """One compiled read: its rows, and where each (measure, role) sits in them."""

    columns: Mapping[tuple[str, str | None], int]
    rows: tuple[tuple[object, ...], ...]

    def value(self, measure_id: str, role: str | None, row: int = 0) -> Decimal | None:
        position = self.columns.get((measure_id, role))
        if position is None or row >= len(self.rows):
            return None
        return _as_decimal(self.rows[row][position])


def _execute(  # noqa: PLR0913 - one read, its scope and its two server-side caps
    db: Session,
    cat: Catalogue,
    query: BiQuery,
    *,
    organization_id: str,
    bank_id: str,
    row_cap: int,
    timeout_ms: int,
    injected_filters: tuple[BiFilter, ...] = (),
) -> _Answer | None:
    """Run one catalogue query, or ``None`` when the platform refused it.

    A refusal is a refusal, not a zero: an unanswerable query yields no facts at
    all, so every measure it would have carried becomes a stated gap rather than
    a figure.

    ``injected_filters`` is the reader's data scope. It is a REQUIRED input rather
    than an optional garnish: a headline figure computed over the whole book and
    stated to a branch-scoped reader is the leak S18 names, and the strip's
    bridgeable portfolio ratios are exactly the family a branch DOES have.
    """
    try:
        compiled = compile_query(
            db,
            cat,
            query,
            organization_id=organization_id,
            bank_id=bank_id,
            injected_filters=injected_filters,
        )
        result = execute(db, compiled, timeout_ms=timeout_ms, row_cap=row_cap)
    except BiQueryError:
        return None
    columns = {
        (spec.member_id, spec.role): position
        for position, spec in enumerate(result.columns)
        if spec.member_id is not None
    }
    return _Answer(columns=columns, rows=tuple(result.rows))


def _dimension_position(answer: _Answer, member_id: str) -> int | None:
    return answer.columns.get((member_id, None))


# ---------------------------------------------------------------------------
# authorization
# ---------------------------------------------------------------------------


def _chunks(values: Sequence[str], size: int) -> list[Sequence[str]]:
    return [values[start : start + size] for start in range(0, len(values), size)]


def _denied_members(  # noqa: PLR0913 - the complete authorization sentence
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    cat: Catalogue,
    *,
    candidates: Sequence[MeasureDef],
    as_of: date,
    surface: str,
) -> tuple[str, ...]:
    """Every member of the candidate set this reader may not see.

    ONE evaluator pass per request-cap-sized chunk, through the same
    ``authorize_query`` the ``/bi/query`` route makes, so the strip can never
    show a figure the query path would refuse. The trend dimension rides along,
    because a projection reads it and it carries its own sentence.
    """
    denied: list[str] = []
    ids = [measure.id for measure in candidates]
    for chunk in _chunks(ids, BI_MAX_MEASURES):
        probe = BiQuery(
            measures=list(chunk),
            dimensions=[TIME_DATE_DIMENSION],
            time=BiTime(as_of=as_of),
        )
        decision: BiAuthorization = authorize_query(
            db, ctx, bank, cat, probe, permission=Permission.VIEW, surface=surface
        )
        denied.extend(member_id for member_id in decision.denied_members if member_id not in denied)
    return tuple(denied)


def _readable(cat: Catalogue, measure: MeasureDef, denied: frozenset[str], as_of: date) -> bool:
    """Whether every member this one measure needs is covered for the reader."""
    probe = BiQuery(measures=[measure.id], time=BiTime(as_of=as_of))
    return not any(member.id in denied for member in query_members(cat, probe))


def _scope_for(  # noqa: PLR0913 - the complete authorization sentence
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    cat: Catalogue,
    *,
    measures: Sequence[MeasureDef],
    as_of: date,
    surface: str,
) -> data_scope.ResolvedDataScope | None:
    """The slice the reader's grants admit over the measures that SURVIVED.

    A second pass, and a deliberate one. The first pass evaluates the whole
    candidate set to learn what is denied, and a chunk containing one refused
    member comes back denied as a whole — carrying no binding ids and therefore no
    scope — while the members beside it remain perfectly readable. So the scope is
    asked of the surviving set, which is the set that will actually be compiled.

    ``None`` means no figure may be read: the surviving set was refused after all,
    or two chunks were authorized under different narrow scopes, which has no
    ordering and so is refused rather than guessed (``read_bi._merged_decision``
    states the same rule for the export sentence).
    """
    scopes: set[BiDataScope] = set()
    for chunk in _chunks([measure.id for measure in measures], BI_MAX_MEASURES):
        decision = authorize_query(
            db,
            ctx,
            bank,
            cat,
            BiQuery(measures=list(chunk), time=BiTime(as_of=as_of)),
            permission=Permission.VIEW,
            surface=surface,
        )
        if not decision.allowed:
            return None
        scopes.add(decision.data_scope)
    narrow = {scope for scope in scopes if not scope.whole_institution}
    if len(narrow) > 1:
        return None
    declared = next(iter(narrow), ALL_INSTITUTION_DATA)
    try:
        resolved = data_scope.resolve(
            db, declared, organization_id=ctx.organization_id, bank_id=bank.id
        )
        # Built here so a scope too wide to express as one filter refuses the
        # strip rather than surfacing later as a per-group "refused" and a
        # sentence that reads as if the bank had no figures.
        _ = resolved.filters
    except BiQueryError:
        return None
    return resolved


# ---------------------------------------------------------------------------
# facts
# ---------------------------------------------------------------------------



def _movement_or_gap(  # noqa: PLR0913 - one measure, its two dates and its evidence
    measure: MeasureDef,
    *,
    as_of: date,
    compare_to: date,
    current: Decimal | None,
    prior: Decimal | None,
) -> Fact:
    """A movement when both dates answered, otherwise the gap, stated.

    This is where "a missing figure is never a zero and never flat" is decided,
    and it is decided by which CONSTRUCTOR is called: a ``MovementFact`` is only
    reachable when both figures exist, and the alternative carries a reason
    instead of a value. Neither type can hold both.
    """
    provenance_block = FactProvenance(fact_id=uuid4(), derived_at=utc_now())
    if current is not None and prior is not None:
        return movement_fact(
            measure,
            as_of=as_of,
            prior_as_of=compare_to,
            provenance=provenance_block,
            current=current,
            prior=prior,
        )
    if current is not None:
        # The figure is known and the comparison is not. Nothing is asserted
        # about a move; the reader is not told a move of zero happened.
        return observed_fact(
            measure,
            as_of=as_of,
            provenance=provenance_block,
            value=current,
        )
    return observed_fact(
        measure,
        as_of=as_of,
        provenance=provenance_block,
        missing_reason="not_computed",
    )


def _bridge(  # noqa: PLR0913 - the ratio, its components and both dates
    cat: Catalogue,
    measure: MeasureDef,
    *,
    as_of: date,
    compare_to: date,
    answer: _Answer,
) -> Fact | None:
    """The exact attribution of a ratio's move, or nothing.

    ``ratio_bridge`` returns its own refusal when a component is missing or a
    denominator is zero, and that refusal is carried onto the fact whole — so
    the ``attribution`` rule states nothing rather than inventing a leg.
    """
    numerator_id, denominator_id = measure.numerator, measure.denominator
    if numerator_id is None or denominator_id is None:
        return None
    numerator, denominator = cat.measure(numerator_id), cat.measure(denominator_id)
    bridge = ratio_bridge(
        measure_id=measure.id,
        numerator_measure_id=numerator_id,
        denominator_measure_id=denominator_id,
        numerator_label=numerator.label,
        denominator_label=denominator.label,
        prior_numerator=answer.value(numerator_id, "prior"),
        prior_denominator=answer.value(denominator_id, "prior"),
        current_numerator=answer.value(numerator_id, "current"),
        current_denominator=answer.value(denominator_id, "current"),
        direction=measure.favourable_direction,
        value_type=measure.value_type,
    )
    return bridge_fact(
        measure,
        as_of=as_of,
        prior_as_of=compare_to,
        bridge=bridge,
        provenance=FactProvenance(fact_id=uuid4(), derived_at=utc_now()),
    )


def _projection(  # noqa: PLR0913 - one measure, its series and its horizon
    measure: MeasureDef,
    *,
    as_of: date,
    observations: Sequence[Observation],
) -> Fact | None:
    """Where the observed trend reaches, or nothing at all.

    ``project`` refuses on a short series, a gap in it, or a horizon that is not
    forward — and a refused projection is carried as the refusal, so no rule can
    render a straight line through two points as a forecast.
    """
    if len(observations) < MIN_OBSERVATIONS:
        return None
    return projection_fact(
        measure,
        as_of=as_of,
        projection=project(
            measure.id,
            observations,
            horizon=shift_months(as_of, PROJECTION_HORIZON_MONTHS),
        ),
        provenance=FactProvenance(fact_id=uuid4(), derived_at=utc_now()),
    )


def _observations(answer: _Answer, measure: MeasureDef) -> tuple[Observation, ...]:
    """The measure's trailing series, one point per date the read returned."""
    position = _dimension_position(answer, TIME_DATE_DIMENSION)
    if position is None:
        return ()
    points: list[Observation] = []
    for index, row in enumerate(answer.rows):
        moment = row[position]
        if not isinstance(moment, date):
            continue
        points.append(Observation(as_of=moment, value=answer.value(measure.id, None, index)))
    return tuple(points)


# ---------------------------------------------------------------------------
# the assembler
# ---------------------------------------------------------------------------


def assemble(  # noqa: PLR0913 - one institution, one date, and the reader
    db: Session,
    *,
    ctx: TenantContext,
    bank: Bank,
    cat: Catalogue,
    as_of: date,
    compare_to: date | None = None,
    surface: str,
    row_cap: int,
    timeout_ms: int,
    policy: InsightPolicy = DEFAULT_POLICY,
) -> AssembledInsights:
    """Read one institution's headline figures and state what they support."""
    prior = compare_to or default_compare_to(as_of)
    institution_type = institution_types.get_type(db, bank)
    candidates = headline_measures(
        cat,
        institution_class=institution_type.institution_class,
        capital_regime=institution_type.capital_regime,
    )
    denied = _denied_members(
        db, ctx, bank, cat, candidates=candidates, as_of=as_of, surface=surface
    )
    denied_set = frozenset(denied)
    allowed = [measure for measure in candidates if _readable(cat, measure, denied_set, as_of)]
    scope = (
        _scope_for(db, ctx, bank, cat, measures=allowed, as_of=as_of, surface=surface)
        if allowed
        else data_scope.WHOLE_INSTITUTION
    )
    if scope is None:
        # Fail closed: nothing may be read, so every candidate is withheld and the
        # route's "read nothing, withheld something" rule refuses the whole strip.
        allowed = []
        denied = tuple(dict.fromkeys((*denied, *(measure.id for measure in candidates))))
        denied_set = frozenset(denied)
        scope = data_scope.WHOLE_INSTITUTION
    injected = scope.filters
    trends_allowed = TIME_DATE_DIMENSION not in denied_set

    window = provenance.data_window(BiTime(as_of=as_of, compare_to=prior))
    fingerprint = provenance.build_fingerprint(
        db, organization_id=ctx.organization_id, bank_id=bank.id, window=window
    )

    facts: list[Fact] = []
    touched: list[str] = []
    reads = 0
    for group in _grouped(cat, allowed):
        if reads >= MAX_COMPILED_READS:
            break
        query = BiQuery(measures=group.measure_ids, time=_point_time(group, as_of, prior))
        answer = _execute(
            db,
            cat,
            query,
            organization_id=ctx.organization_id,
            bank_id=bank.id,
            row_cap=row_cap,
            timeout_ms=timeout_ms,
            injected_filters=injected,
        )
        reads += 1
        touched.extend(member_id for member_id in group.measure_ids if member_id not in touched)
        for measure in group.measures:
            facts.append(
                _movement_or_gap(
                    measure,
                    as_of=as_of,
                    compare_to=prior,
                    current=None if answer is None else answer.value(measure.id, "current"),
                    prior=None if answer is None else answer.value(measure.id, "prior"),
                )
            )
            if answer is None or not _is_bridgeable(measure):
                continue
            bridged = _bridge(
                cat,
                measure,
                as_of=as_of,
                compare_to=prior,
                answer=answer,
            )
            if bridged is not None:
                facts.append(bridged)
        if not trends_allowed or reads >= MAX_COMPILED_READS:
            continue
        trending = [
            measure
            for measure in group.measures
            if TIME_DATE_DIMENSION in measure.allowed_dimensions
        ]
        if not trending:
            continue
        series = _execute(
            db,
            cat,
            BiQuery(
                measures=[measure.id for measure in trending],
                dimensions=[TIME_DATE_DIMENSION],
                time=_series_time(as_of),
            ),
            organization_id=ctx.organization_id,
            bank_id=bank.id,
            row_cap=row_cap,
            timeout_ms=timeout_ms,
            injected_filters=injected,
        )
        reads += 1
        if TIME_DATE_DIMENSION not in touched:
            touched.append(TIME_DATE_DIMENSION)
        if series is None:
            continue
        for measure in trending:
            projected = _projection(
                measure,
                as_of=as_of,
                observations=_observations(series, measure),
            )
            if projected is not None:
                facts.append(projected)

    sheet = fact_sheet(
        institution_id=bank.id,
        as_of=as_of,
        catalogue_version=cat.version,
        facts=facts,
        generated_at=utc_now(),
    )
    return AssembledInsights(
        insight_set=derive_insights(sheet, policy),
        sheet=sheet,
        as_of=as_of,
        compare_to=prior,
        member_ids=tuple(touched),
        denied_members=denied,
        measures_read=len(allowed),
        measures_withheld=len(candidates) - len(allowed),
        compiled_reads=reads,
        build_fingerprint=fingerprint,
    )
