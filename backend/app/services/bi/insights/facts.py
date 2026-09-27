"""What an insight is allowed to assert: a closed, typed set of facts.

An insight is a sentence about a bank's figures, and the only defence against a
sentence that overstates what the platform knows is to make the sentence
unable to reach anything the facts do not carry. So the insight layer never sees
a query result, a row, or a number in free text. It sees a
:class:`FactSheet` — a set of instances of the four types below, each carrying
its own measure identity, its own trust state and its own advisory designation —
and the rules may only restate what is in one of them.

The four kinds, and why there are exactly four:

:class:`ObservedFact`
    One measure, one date, one scope. Either a value or a reason there is none,
    never both and never neither: the constructor rejects a fact that carries a
    value AND a missing reason, and one that carries neither. That is where
    "missing data is never zero" (D-042 / D-047) becomes structural rather than
    a convention — there is no way to express a missing figure as ``0``.

:class:`MovementFact`
    The same measure at two dates. It computes its own delta, its own direction
    and its own verdict, so no rule can judge a move by hand; the verdict comes
    from :func:`app.services.bi.insights.drivers.favourability`, which is where
    D-013's magnitude rule lives.

:class:`BridgeFact`
    A ratio's move, decomposed into parts that sum to it exactly
    (``drivers.py``). The bridge is carried whole, including the case where it
    could not be built, so a rule cannot invent an attribution.

:class:`ProjectionFact`
    A forward-looking statement (``projections.py``), typed apart from
    observation so it can never be rendered as something that happened.

Trust and designation ride on the FACT, not on the page
-------------------------------------------------------
Every fact carries a :class:`TrustState` — the reconciliation verdict of the
checks that bear on that measure, folded together with the build's overall badge
so a fact can never look better than the book it came from. ``grey`` means the
check could not be assessed and is NEVER reported as a pass
(``reconciliation.py``). Every fact also carries the registry's
``advisory_designation`` and the catalogue's own ``certified`` verdict (D-022 /
D-055): an advisory figure is analysis, and an insight over one must not read
like a filed number.

Provenance is carried and excluded
----------------------------------
:class:`FactProvenance` holds the identifiers that move every time the live
plane re-derives — the fact's own id, when it was derived, which mart build it
came from. They are evidence and they stay on the record; they are excluded from
``fact_sheet_hash`` (``digest.py``), exactly as the attestation digests exclude
run ids and timestamps while keeping them in the stored snapshot.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import ClassVar, Literal
from uuid import UUID

from app.domain.bi.authority import AdvisoryDesignation
from app.domain.bi.catalogue.members import FavourableDirection, MeasureDef, ValueType
from app.services.bi import reconciliation
from app.services.bi.insights.drivers import (
    BridgeUnavailable,
    Favourability,
    MoveDirection,
    RatioBridge,
    favourability,
    move_direction,
)
from app.services.bi.insights.projections import Projection, ProjectionUnavailable

__all__ = [
    "BridgeFact",
    "Fact",
    "FactError",
    "FactProvenance",
    "FactScope",
    "FactSheet",
    "MissingReason",
    "MovementFact",
    "ObservedFact",
    "ProjectionFact",
    "TrustState",
    "TrustStatus",
    "WHOLE_INSTITUTION",
    "bridge_fact",
    "fact_sheet",
    "movement_fact",
    "observed_fact",
    "projection_fact",
    "trust_state",
]

TrustStatus = Literal["green", "amber", "red", "grey"]

#: Why a measure has no value. Each is a different question the platform could
#: not answer, and the copy a reader sees differs accordingly:
#:
#: * ``not_supplied`` — the bank has not fed the dataset the measure reads;
#: * ``not_answerable`` — the question could not be asked of this population
#:   (D-047: a ratio restricts its denominator to the answerable rows, and when
#:   none are answerable there is no ratio);
#: * ``not_computed`` — the platform has not computed this figure for this date.
MissingReason = Literal["not_supplied", "not_answerable", "not_computed"]


@dataclass(frozen=True, slots=True)
class TrustState:
    """How far the figures behind a fact reconcile to what the platform files.

    ``overall`` is the worst of the checks that bear on the measure and the
    build's own badge — a fact may understate confidence, never overstate it.
    """

    overall: TrustStatus
    checks: tuple[tuple[str, TrustStatus], ...] = ()

    @property
    def assessed(self) -> bool:
        """``False`` when nothing could be checked. Never read as a pass."""
        return self.overall != reconciliation.GREY

    @property
    def reconciles(self) -> bool:
        """Only a green badge means the figures were checked and agreed."""
        return self.overall == reconciliation.GREEN

    @property
    def failing_checks(self) -> tuple[str, ...]:
        return tuple(check for check, status in self.checks if status != reconciliation.GREEN)


def trust_state(
    measure: MeasureDef,
    statuses: Mapping[str, str],
    *,
    build_overall: str | None = None,
) -> TrustState:
    """The trust a fact over ``measure`` carries.

    ``statuses`` is the stored reconciliation verdict per check id; a check the
    measure declares but ``statuses`` does not carry is ``grey``, because an
    absent result is "not assessed" and must never be silently dropped.
    ``build_overall`` is the badge for the whole build, folded in so a measure
    with no check of its own still reports the state of the book it came from.
    """
    checks: tuple[tuple[str, TrustStatus], ...] = tuple(
        (check_id, _status(statuses.get(check_id))) for check_id in measure.reconciliation_checks
    )
    overall = reconciliation.overall_trust(
        [status for _, status in checks] + [_status(build_overall)]
    )
    return TrustState(overall=_status(overall), checks=checks)


def _status(value: str | None) -> TrustStatus:
    """A recognised verdict, or ``grey``. An unknown string is never a pass."""
    if value == reconciliation.GREEN:
        return "green"
    if value == reconciliation.AMBER:
        return "amber"
    if value == reconciliation.RED:
        return "red"
    return "grey"


@dataclass(frozen=True, slots=True)
class FactScope:
    """What slice of the book a fact is about."""

    dimension_id: str | None = None
    value_code: str | None = None
    value_label: str | None = None

    @property
    def whole_institution(self) -> bool:
        return self.dimension_id is None

    @property
    def label(self) -> str:
        """Production copy naming the slice."""
        if self.whole_institution:
            return "across the whole institution"
        return f"for {self.value_label or self.value_code}"


#: The scope of a figure stated for the institution as a whole.
WHOLE_INSTITUTION = FactScope()


@dataclass(frozen=True, slots=True)
class FactProvenance:
    """Where a fact came from. Excluded from ``fact_sheet_hash`` by rule."""

    fact_id: UUID
    derived_at: datetime
    build_id: UUID | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class _FactBase:
    """Everything every fact declares about the measure behind it."""

    KIND: ClassVar[str] = ""

    measure_id: str
    label: str
    value_type: ValueType
    trust: TrustState
    provenance: FactProvenance
    direction: FavourableDirection = "neutral"
    advisory: AdvisoryDesignation | None = None
    certified: bool = False
    scope: FactScope = WHOLE_INSTITUTION

    @property
    def kind(self) -> str:
        return self.KIND

    @property
    def key(self) -> str:
        """A stable, value-derived citation key. Never the volatile fact id."""
        return "|".join(
            (
                self.KIND,
                self.measure_id,
                self.scope.dimension_id or "",
                self.scope.value_code or "",
            )
        )


class FactError(ValueError):
    """A fact that would assert something the platform does not know."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ObservedFact(_FactBase):
    """One measure at one date: a value, or the reason there is none."""

    KIND: ClassVar[str] = "observed"

    as_of: date
    value: Decimal | None = None
    missing_reason: MissingReason | None = None

    def __post_init__(self) -> None:
        if (self.value is None) == (self.missing_reason is None):
            raise FactError(
                f"{self.measure_id} must state either a value or why it has none, "
                "never both and never neither"
            )

    @property
    def is_missing(self) -> bool:
        return self.value is None


@dataclass(frozen=True, slots=True, kw_only=True)
class MovementFact(_FactBase):
    """One measure at two dates, with its own delta, direction and verdict."""

    KIND: ClassVar[str] = "movement"

    as_of: date
    prior_as_of: date
    current: Decimal | None = None
    prior: Decimal | None = None
    missing_reason: MissingReason | None = None

    def __post_init__(self) -> None:
        complete = self.current is not None and self.prior is not None
        if complete == (self.missing_reason is not None):
            raise FactError(
                f"{self.measure_id} must state both figures or why the move is "
                "unknown, never both and never neither"
            )

    @property
    def is_missing(self) -> bool:
        return self.current is None or self.prior is None

    @property
    def delta(self) -> Decimal | None:
        if self.current is None or self.prior is None:
            return None
        return self.current - self.prior

    @property
    def moved(self) -> MoveDirection | None:
        if self.current is None or self.prior is None:
            return None
        return move_direction(self.prior, self.current)

    @property
    def favourability(self) -> Favourability:
        """``neutral`` while anything is unknown — an absent figure is not news."""
        if self.current is None or self.prior is None:
            return "neutral"
        return favourability(self.direction, self.prior, self.current)

    @property
    def relative_change(self) -> Decimal | None:
        """How big the move was against its own base, or ``None``.

        ``None`` when either figure is unknown, and when the base is zero —
        there is no proportion of nothing, and inventing one would turn a first
        appearance into an infinite move.

        A ``magnitude_lower_better`` figure is measured on the change in
        MAGNITUDE (D-013), so a signed value that halves its exposure registers
        as a large move whichever side of zero it sits; every other direction is
        measured on the change itself, so a swing through zero is not flattened
        into "no movement".
        """
        if self.prior is None or self.current is None or self.prior == 0:
            return None
        if self.direction == "magnitude_lower_better":
            return abs(abs(self.current) - abs(self.prior)) / abs(self.prior)
        return abs(self.current - self.prior) / abs(self.prior)


@dataclass(frozen=True, slots=True, kw_only=True)
class BridgeFact(_FactBase):
    """A ratio's move, split into parts that sum to it exactly, or why not."""

    KIND: ClassVar[str] = "bridge"

    as_of: date
    prior_as_of: date
    bridge: RatioBridge | BridgeUnavailable

    @property
    def available(self) -> bool:
        return isinstance(self.bridge, RatioBridge)


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectionFact(_FactBase):
    """A forward-looking statement, or the reason none could be made."""

    KIND: ClassVar[str] = "projection"

    as_of: date
    projection: Projection | ProjectionUnavailable

    @property
    def available(self) -> bool:
        return isinstance(self.projection, Projection)


type Fact = ObservedFact | MovementFact | BridgeFact | ProjectionFact


@dataclass(frozen=True, slots=True)
class FactSheet:
    """Everything one insight run is allowed to know about one institution."""

    institution_id: str
    as_of: date
    catalogue_version: str
    facts: tuple[Fact, ...] = ()
    generated_at: datetime | None = None
    """Volatile. Excluded from ``fact_sheet_hash`` (``digest.py``)."""

    def of_kind(self, kind: str) -> tuple[Fact, ...]:
        return tuple(fact for fact in self.facts if fact.kind == kind)


# ---------------------------------------------------------------------------
# builders — a fact takes its metadata from the catalogue, never from a caller
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Facet:
    """The catalogue's own description of a measure, carried onto every fact."""

    measure_id: str
    label: str
    value_type: ValueType
    direction: FavourableDirection
    advisory: AdvisoryDesignation | None
    certified: bool
    trust: TrustState


def _facet(measure: MeasureDef, statuses: Mapping[str, str], build_overall: str | None) -> _Facet:
    return _Facet(
        measure_id=measure.id,
        label=measure.label,
        value_type=measure.value_type,
        direction=measure.favourable_direction,
        advisory=measure.advisory_designation,
        certified=measure.certified,
        trust=trust_state(measure, statuses, build_overall=build_overall),
    )


def observed_fact(  # noqa: PLR0913 - the measure, its context and its one figure
    measure: MeasureDef,
    *,
    as_of: date,
    provenance: FactProvenance,
    statuses: Mapping[str, str],
    build_overall: str | None = None,
    value: Decimal | None = None,
    missing_reason: MissingReason | None = None,
    scope: FactScope = WHOLE_INSTITUTION,
) -> ObservedFact:
    """One figure for ``measure``, or the reason it has none."""
    facet = _facet(measure, statuses, build_overall)
    return ObservedFact(
        measure_id=facet.measure_id,
        label=facet.label,
        value_type=facet.value_type,
        direction=facet.direction,
        advisory=facet.advisory,
        certified=facet.certified,
        trust=facet.trust,
        scope=scope,
        provenance=provenance,
        as_of=as_of,
        value=value,
        missing_reason=missing_reason,
    )


def movement_fact(  # noqa: PLR0913 - the measure, two dates and two figures
    measure: MeasureDef,
    *,
    as_of: date,
    prior_as_of: date,
    provenance: FactProvenance,
    statuses: Mapping[str, str],
    build_overall: str | None = None,
    current: Decimal | None = None,
    prior: Decimal | None = None,
    missing_reason: MissingReason | None = None,
    scope: FactScope = WHOLE_INSTITUTION,
) -> MovementFact:
    """How ``measure`` moved between two dates, or why that is unknown."""
    facet = _facet(measure, statuses, build_overall)
    return MovementFact(
        measure_id=facet.measure_id,
        label=facet.label,
        value_type=facet.value_type,
        direction=facet.direction,
        advisory=facet.advisory,
        certified=facet.certified,
        trust=facet.trust,
        scope=scope,
        provenance=provenance,
        as_of=as_of,
        prior_as_of=prior_as_of,
        current=current,
        prior=prior,
        missing_reason=missing_reason,
    )


def bridge_fact(  # noqa: PLR0913 - the measure, two dates and the bridge itself
    measure: MeasureDef,
    *,
    as_of: date,
    prior_as_of: date,
    bridge: RatioBridge | BridgeUnavailable,
    provenance: FactProvenance,
    statuses: Mapping[str, str],
    build_overall: str | None = None,
    scope: FactScope = WHOLE_INSTITUTION,
) -> BridgeFact:
    """What moved ``measure``, decomposed exactly, or why it could not be."""
    facet = _facet(measure, statuses, build_overall)
    return BridgeFact(
        measure_id=facet.measure_id,
        label=facet.label,
        value_type=facet.value_type,
        direction=facet.direction,
        advisory=facet.advisory,
        certified=facet.certified,
        trust=facet.trust,
        scope=scope,
        provenance=provenance,
        as_of=as_of,
        prior_as_of=prior_as_of,
        bridge=bridge,
    )


def projection_fact(  # noqa: PLR0913 - the measure, its date and the projection
    measure: MeasureDef,
    *,
    as_of: date,
    projection: Projection | ProjectionUnavailable,
    provenance: FactProvenance,
    statuses: Mapping[str, str],
    build_overall: str | None = None,
    scope: FactScope = WHOLE_INSTITUTION,
) -> ProjectionFact:
    """Where ``measure``'s observed trend reaches, or why it was not drawn."""
    facet = _facet(measure, statuses, build_overall)
    return ProjectionFact(
        measure_id=facet.measure_id,
        label=facet.label,
        value_type=facet.value_type,
        direction=facet.direction,
        advisory=facet.advisory,
        certified=facet.certified,
        trust=facet.trust,
        scope=scope,
        provenance=provenance,
        as_of=as_of,
        projection=projection,
    )


def fact_sheet(
    *,
    institution_id: str,
    as_of: date,
    catalogue_version: str,
    facts: Sequence[Fact],
    generated_at: datetime | None = None,
) -> FactSheet:
    """A sheet whose facts are ordered by their stable keys, not by arrival."""
    return FactSheet(
        institution_id=institution_id,
        as_of=as_of,
        catalogue_version=catalogue_version,
        facts=tuple(sorted(facts, key=lambda item: item.key)),
        generated_at=generated_at,
    )
