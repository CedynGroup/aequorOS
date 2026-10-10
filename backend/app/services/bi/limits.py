"""BI's one door to the governed limits a measure is judged against (D-069).

A catalogue measure never carries a number: it carries
:attr:`~app.domain.bi.catalogue.members.MeasureDef.thresholds_source`, the CODE
of the register row that states its limit. Until this module existed nothing
resolved that code, so no limit monitor, no chart limit line and no breach
statement could be drawn — the packs ship ``show_limit`` false and the ALCO
net-open-position widget says in as many words that the limits governing it are
held in the bank's register and are not shown.

Two registers, one precedence — the engines' own
------------------------------------------------
A limit BI draws must be the same limit the engine judged the figure against,
or the platform states two limits for one number. The chain is the one
``app/services/parameter_register.py`` describes and every engine applies:

* the institution's **board register** (``param_capital_threshold``: the board's
  own monitoring levels, its internal liquidity floors, its EVE/NII tolerances
  and its net-open-position limits), read through ``app.services.params`` on the
  same active-window rule every other reader uses; then
* the **regulatory control plane** (``regulatory_parameter``: what the regulator
  imposes, licence row before class row), read through
  ``app.services.regulatory_parameters``; and
* between them the tighten-only clamp — a board value stands only when it is
  stricter, and the evidence that it was overridden travels on the answer
  (:attr:`GovernedLimit.clamped_from`) rather than being applied silently.

Neither value is ever invented here, neither register is written, and no number
appears in this module.

Why this is one module (the plane, D-078)
----------------------------------------
Every read is ``record=False``, and the decision is taken HERE rather than at
each call site. ``RegulatoryRun.parameter_provenance`` is drained from an
ambient session-scoped ledger when a run row is built, so a recording read
credits whichever run seals next in that session with a row it never consumed.
BI is a DISPATCH plane: it seals no run, and a dashboard resolves the limits of
every widget on the canvas — across every family — to draw them. Recording
those would make a sealed run's provenance a function of which dashboard a
different request happened to open. ``app/services/icaap/parameters.py`` is the
precedent, and ``tests/architecture/test_bi_limit_resolver_boundary.py`` keeps
this the only BI module that can reach the resolver at all.

Absent is not zero
------------------
An unresolved limit is :class:`NoGovernedLimit`, which HAS NO ``value``
attribute. There is no ``Decimal | None`` and no sentinel anywhere in the
public surface, so "the bank has no governed limit for this figure" cannot be
read as "the limit is 0" by a caller that forgot to check — the attribute it
would read does not exist. A limit line at zero reads as a breach of
everything.

Units are reconciled or refused, never assumed
----------------------------------------------
A register row states its own unit (``percent``, ``bps``, ``ratio``, ``count``,
``days``, ``multiplier``, and the amount unit whose name is a wire key); a
measure states its own ``value_type``. Only the pairs in :data:`_CONVERSIONS`
are converted, each by an exact decimal factor, and only because the catalogue
now declares what each value type IS — a ``pct`` is already multiplied by a
hundred and a ``fraction`` is not, so the factor between them is a fact rather
than a convention. Every other pair is refused as ``unit_not_reconcilable``: a
limit that is silently 100x or a million times wrong is worse than no limit, and
a wrong limit line is read as a breach.

This module decides no authorization. It is called for measures the caller has
already authorized (``app.services.bi.authorization``); a register row is
platform reference data keyed by institution class and jurisdiction, not tenant
book data, and resolving one grants no view of a figure.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal, TypeIs

from sqlalchemy.orm import Session

from app.domain.authority.outcomes import NotComputable
from app.domain.bi.catalogue import MeasureDef
from app.domain.bi.catalogue.members import NUMERIC_VALUE_TYPES, ValueType
from app.domain.bi.catalogue.targets import ACTUAL_SUFFIX, is_target_variant
from app.identity.public import Bank
from app.models import ParamCapitalThreshold
from app.services import params, regulatory_parameters
from app.services.params import PrefetchedActiveParams

logger = logging.getLogger(__name__)

__all__ = [
    "GovernedLimit",
    "LimitAbsenceReason",
    "LimitAuthority",
    "LimitOutcome",
    "LimitResolver",
    "NoGovernedLimit",
    "absence_copy",
    "conversion_factors",
    "is_governed",
    "measure_limit",
]

#: Which register supplied the value a surface will draw.
LimitAuthority = Literal["board_register", "regulatory_register"]

#: Why no limit is being stated. Every one of these is a POSITIVE statement that
#: a surface can render; none of them means zero.
LimitAbsenceReason = Literal[
    "no_threshold_declared",
    "no_governed_row",
    "not_a_scalar",
    "unit_not_reconcilable",
    "value_type_not_comparable",
    "comparison_not_the_figure",
    "scope_unresolved",
]

#: Production copy for each absence, for a surface that must say why a widget
#: shows no limit line. No jargon, no register table names, no wire codes.
_ABSENCE_COPY: dict[LimitAbsenceReason, str] = {
    "no_threshold_declared": ("No limit governs this figure, so none is shown."),
    "no_governed_row": (
        "This figure's limit has not been set for the institution on this date, so none is "
        "shown. It is a governed value, held in the institution's register and in the "
        "regulatory parameters staff maintain."
    ),
    "not_a_scalar": (
        "This figure's limit is held as a table of values rather than a single number, so no "
        "single limit line can be drawn."
    ),
    "unit_not_reconcilable": (
        "This figure's limit is stated in a different unit from the figure itself, and the two "
        "cannot be converted without an assumption, so no limit is shown."
    ),
    "value_type_not_comparable": (
        "This figure is not a number a limit can be compared against, so none is shown."
    ),
    "comparison_not_the_figure": (
        "This is a comparison against the institution's own plan rather than the figure the "
        "limit governs, so no limit is shown."
    ),
    "scope_unresolved": (
        "The institution's regulatory scope is not resolved, so no limit can be selected for "
        "it. Its jurisdiction and licence class have to be recorded first."
    ),
}


def absence_copy(reason: LimitAbsenceReason) -> str:
    """The sentence a surface shows in place of a limit."""
    return _ABSENCE_COPY[reason]


#: The board register's own unit. ``param_capital_threshold.value_pct`` is a
#: percentage by the column's definition (the 1250 % RWA multiplier is why it is
#: ``Numeric(12, 6)``), so a board row needs no unit column and this is not a
#: guess about one.
_BOARD_UNIT = "percent"

#: Exact conversions from a register unit into a measure's own ``value_type``.
#: Each factor is an exact decimal scaling of a ``Decimal``, so no rounding and
#: no floating point enters a limit. The catalogue declares what each value type
#: IS (``members.py``): ``pct`` is already multiplied by a hundred, ``fraction``
#: is a proportion of one and is not, which is what makes both rows below exact
#: rather than a convention this module invented.
#:
#: What is deliberately NOT here, and why refusing beats converting:
#:
#: * the register's ``ratio`` unit -> anything. It carries method coefficients
#:   and a confidence quantile, whose scale is the method's own rather than a
#:   declared proportion of one. No measure names one of those codes as its
#:   limit, so refusing costs nothing and assuming would cost a wrong line.
#: * ``multiplier`` -> ``index``. Both are dimensionless "on their own scale",
#:   which is precisely why neither states what the other's scale is.
#: * the amount unit -> ``amount``. That unit's name states millions; a measure's
#:   ``amount`` declares no scale at all, so the factor would be invented — and
#:   an amount limit drawn a million times off is the worst case in this file.
#:   The catalogue has to declare an amount scale before the pair can be honest.
#: * ``days`` -> ``duration_years``. Needs a day-count convention, which is a
#:   methodology choice and not a unit conversion.
#: * any unit this table does not name — including the ones the register uses
#:   that its own column comment does not list (``months``, ``years``,
#:   ``boolean``, and the table-shaped ones). An unknown unit refuses.
_CONVERSIONS: dict[tuple[str, ValueType], Decimal] = {
    ("percent", "pct"): Decimal(1),
    ("percent", "fraction"): Decimal("0.01"),
    # 100 basis points is one percentage point: exact, and the same dimension.
    ("bps", "pct"): Decimal("0.01"),
    ("bps", "fraction"): Decimal("0.0001"),
    ("count", "count"): Decimal(1),
}

#: A target variant that restates the figure itself rather than comparing it.
#: ``.actual`` keeps its base's limit; ``.variance`` / ``.variance_pct`` /
#: ``.attainment_pct`` are comparisons against the bank's own plan, and
#: ``.target`` declares no threshold source at all.
_FIGURE_SUFFIX = f".{ACTUAL_SUFFIX}"

_ONE = Decimal(1)


def _normalise(value: Decimal) -> Decimal:
    """Strip the trailing zeros a ``Numeric(_, 6)`` round trip adds.

    The same rule as ``regulatory_parameters.ResolvedParameter.normalized_value``
    — an integral value quantizes to scale 0, a fractional one normalises — so a
    limit read from the board register and one read from the control plane are
    stated identically. A test pins the two against each other.
    """
    return value.quantize(_ONE) if value == value.to_integral_value() else value.normalize()


@dataclass(frozen=True, slots=True)
class GovernedLimit:
    """A limit that exists, in the measure's own unit, with its provenance.

    ``value`` is never ``None``: this class is only ever constructed when a
    register row supplied a scalar and its unit reconciled with the measure's.
    """

    #: The register code the catalogue named (``MeasureDef.thresholds_source``).
    param_code: str
    #: The measure this limit was resolved for.
    measure_id: str
    #: The limit, converted into ``stated_as`` and normalised. Never ``None``.
    value: Decimal
    #: The measure's own ``value_type``: the unit ``value`` is expressed in.
    stated_as: ValueType
    #: Which register supplied ``value``.
    authority: LimitAuthority
    #: The unit the register row itself stated, before conversion.
    source_unit: str
    #: Where the number comes from, as the register states it: the legal or
    #: directive citation for a regulatory row, the board's approval evidence
    #: for a register row.
    source: str
    #: ``confirmed`` / ``pending`` for a regulatory row. ``None`` for a board
    #: row, whose standing is its own approval evidence rather than the control
    #: plane's confirmation status.
    confirmation_status: str | None
    #: The identity of the row that supplied the value, for audit.
    source_row_id: str
    #: The effective window of that row: from, and until (``None`` = open).
    effective_from: date
    effective_to: date | None
    #: The date the limit was resolved for.
    as_of: date
    #: Set when the board's own value was weaker than the regulatory one and the
    #: regulatory value therefore governs. Stated in ``stated_as``, so a surface
    #: can say what the board held and what applies instead.
    clamped_from: Decimal | None = None

    @property
    def is_provisional(self) -> bool:
        """Whether a regulatory row is a documented default awaiting confirmation."""
        return self.confirmation_status == "pending"

    @property
    def clamped(self) -> bool:
        return self.clamped_from is not None

    def provenance(self) -> dict[str, object]:
        """The audit record of this limit. Stable wire keys."""
        return {
            "param_code": self.param_code,
            "measure_id": self.measure_id,
            "value": str(self.value),
            "stated_as": self.stated_as,
            "authority": self.authority,
            "source_unit": self.source_unit,
            "source": self.source,
            "confirmation_status": self.confirmation_status,
            "source_row_id": self.source_row_id,
            "effective_from": self.effective_from.isoformat(),
            "effective_to": None if self.effective_to is None else self.effective_to.isoformat(),
            "as_of": self.as_of.isoformat(),
            "clamped": self.clamped,
            "clamped_from": None if self.clamped_from is None else str(self.clamped_from),
        }


@dataclass(frozen=True, slots=True)
class NoGovernedLimit:
    """There is no limit to state, and why.

    Deliberately carries NO ``value`` / ``stated_as`` / ``source`` field. A
    caller that reads a limit off this class gets an ``AttributeError`` — and a
    type error before that — rather than a zero that renders as a breach of
    everything.
    """

    #: The code the catalogue named, or ``None`` when it named none.
    param_code: str | None
    measure_id: str
    reason: LimitAbsenceReason
    #: The date the limit was looked for.
    as_of: date

    @property
    def detail(self) -> str:
        """Production copy for the surface, from :func:`absence_copy`."""
        return absence_copy(self.reason)

    def provenance(self) -> dict[str, object]:
        return {
            "param_code": self.param_code,
            "measure_id": self.measure_id,
            "reason": self.reason,
            "as_of": self.as_of.isoformat(),
        }


#: What a limit lookup answers. Exhaustive: there is no ``None`` arm.
LimitOutcome = GovernedLimit | NoGovernedLimit


def is_governed(outcome: LimitOutcome) -> TypeIs[GovernedLimit]:
    """Narrow an outcome to the arm that has a value. Never a truthiness test.

    A ``TypeIs`` rather than a ``TypeGuard`` so the ELSE branch narrows too: a
    caller writing ``else: outcome.reason`` is type-checked, which is what makes
    "state the absence" as easy to write as reading a value would have been.

    Neither outcome class defines ``__bool__`` on purpose: a governed limit OF
    zero is a real limit and must not be falsy, and an absent one must not be
    truthy. The caller narrows explicitly.
    """
    return isinstance(outcome, GovernedLimit)


@dataclass(frozen=True, slots=True)
class LimitResolver:
    """Every limit one institution's dashboard needs, in two queries.

    Built once per request (or per export job) with the dates the surface will
    ask about, exactly as ``regulatory_parameters.PrefetchedParameterResolver``
    is: the policy scope resolves once, both registers load once over the whole
    date window, and each measure is then answered without a round trip. A
    dashboard asks for dozens of limits across widgets that share a handful of
    codes, so N queries per widget is the wrong shape.
    """

    bank_id: str
    #: ``None`` when the institution's jurisdiction or licence class does not
    #: resolve. No parameter set can be selected for such an institution, and
    #: one is never substituted, so every lookup answers ``scope_unresolved``.
    _governed: regulatory_parameters.PrefetchedParameterResolver | None
    _board: PrefetchedActiveParams[ParamCapitalThreshold]
    #: Per-date board register index, built on first use for that date.
    _board_index: dict[date, dict[str, ParamCapitalThreshold]] = field(
        default_factory=dict, repr=False
    )

    # -- construction ---------------------------------------------------------

    @classmethod
    def load(cls, db: Session, bank: Bank, *, as_of_dates: Iterable[date]) -> LimitResolver:
        """Prefetch both registers for ``bank`` over ``as_of_dates``.

        There is no ``record`` argument by design. BI seals no run, so the
        governed resolver is always built non-recording; see the module
        docstring. A caller cannot opt into the calculation plane from here.
        """
        dates = list(as_of_dates)
        try:
            governed = regulatory_parameters.PrefetchedParameterResolver.load(
                db, bank, as_of_dates=dates, record=False
            )
        except NotComputable:
            # The jurisdiction or the licence class does not resolve. That is a
            # tenant configuration fault, not a reason to fail a read surface —
            # and emphatically not a reason to select another jurisdiction's
            # parameter set. Every lookup then states that, and shows no limit.
            logger.warning(
                "bi.limits.scope_unresolved bank=%s org=%s",
                bank.id,
                bank.organization_id,
            )
            return cls(
                bank_id=bank.id,
                _governed=None,
                _board=PrefetchedActiveParams[ParamCapitalThreshold](()),
            )
        board = params.prefetch_active_params(
            db,
            bank.organization_id,
            governed.scope.jurisdiction_code,
            ParamCapitalThreshold,
            dates,
        )
        return cls(bank_id=bank.id, _governed=governed, _board=board)

    # -- lookup ---------------------------------------------------------------

    def for_measure(self, measure: MeasureDef, *, as_of: date) -> LimitOutcome:
        """The limit governing ``measure`` on ``as_of``, or why there is none."""
        code = measure.thresholds_source
        if code is None:
            return NoGovernedLimit(
                param_code=None,
                measure_id=measure.id,
                reason="no_threshold_declared",
                as_of=as_of,
            )
        if _is_comparison_variant(measure.id):
            return self._absent(measure, code, "comparison_not_the_figure", as_of)
        if measure.value_type not in NUMERIC_VALUE_TYPES:
            return self._absent(measure, code, "value_type_not_comparable", as_of)
        if self._governed is None:
            return self._absent(measure, code, "scope_unresolved", as_of)
        return self._resolve(measure, code, as_of=as_of)

    def for_measures(
        self, measures: Sequence[MeasureDef], *, as_of: date
    ) -> dict[str, LimitOutcome]:
        """One outcome per measure id, in the order given.

        Every measure gets an entry: a measure is never simply missing from the
        mapping, because an absent key is the one shape a caller can mistake for
        "nothing to show here" and then default.
        """
        return {measure.id: self.for_measure(measure, as_of=as_of) for measure in measures}

    # -- internals ------------------------------------------------------------

    def _absent(
        self, measure: MeasureDef, code: str, reason: LimitAbsenceReason, as_of: date
    ) -> NoGovernedLimit:
        return NoGovernedLimit(param_code=code, measure_id=measure.id, reason=reason, as_of=as_of)

    def _board_row(self, code: str, as_of: date) -> ParamCapitalThreshold | None:
        index = self._board_index.get(as_of)
        if index is None:
            # Same fold as every engine loader: rows arrive ordered by
            # (effective_from, id) and the later generation overwrites, so BI
            # and the engines select the same row for the same date.
            index = {row.threshold_code: row for row in self._board.active_on(as_of)}
            self._board_index[as_of] = index
        return index.get(code)

    def _resolve(self, measure: MeasureDef, code: str, *, as_of: date) -> LimitOutcome:
        assert self._governed is not None  # noqa: S101 - for_measure checks it first
        board = self._board_row(code, as_of)
        governed = self._governed.try_resolve(code, as_of=as_of)
        if governed is None:
            if board is None:
                return self._absent(measure, code, "no_governed_row", as_of)
            return self._from_board(measure, code, board, as_of=as_of, clamped_from=None)
        return self._against_governed(measure, code, governed, board, as_of=as_of)

    def _against_governed(
        self,
        measure: MeasureDef,
        code: str,
        governed: regulatory_parameters.ResolvedParameter,
        board: ParamCapitalThreshold | None,
        *,
        as_of: date,
    ) -> LimitOutcome:
        """The regulatory row exists; state it, or the board's stricter value."""
        governed_value = governed.normalized_value
        if governed_value is None:
            # The row states a table, not a number. Even where a board scalar
            # exists it cannot be clamped against a table, so nothing here can
            # produce one honest line.
            return self._absent(measure, code, "not_a_scalar", as_of)
        factor = _CONVERSIONS.get((governed.unit, measure.value_type))
        if factor is None:
            return self._absent(measure, code, "unit_not_reconcilable", as_of)
        if board is None:
            return self._from_governed(measure, governed, factor, as_of=as_of)
        if governed.unit != _BOARD_UNIT:
            # A board percentage and a governed value in another unit cannot be
            # compared, so the tighten-only rule cannot be applied and no single
            # effective limit can be stated. Refusing is the only honest answer:
            # taking either side would drop the other register's authority.
            return self._absent(measure, code, "unit_not_reconcilable", as_of)
        board_value = Decimal(str(board.value_pct))
        effective = regulatory_parameters.tighten(code, board_value, governed_value)
        if effective == board_value:
            return self._from_board(measure, code, board, as_of=as_of, clamped_from=None)
        return self._from_governed(measure, governed, factor, as_of=as_of, clamped_from=board_value)

    def _from_board(
        self,
        measure: MeasureDef,
        code: str,
        row: ParamCapitalThreshold,
        *,
        as_of: date,
        clamped_from: Decimal | None,
    ) -> LimitOutcome:
        factor = _CONVERSIONS.get((_BOARD_UNIT, measure.value_type))
        if factor is None:
            return self._absent(measure, code, "unit_not_reconcilable", as_of)
        return GovernedLimit(
            param_code=code,
            measure_id=measure.id,
            value=_convert(Decimal(str(row.value_pct)), factor),
            stated_as=measure.value_type,
            authority="board_register",
            source_unit=_BOARD_UNIT,
            source=_board_source(row),
            confirmation_status=None,
            source_row_id=str(row.id),
            effective_from=row.effective_from,
            effective_to=row.effective_to,
            as_of=as_of,
            clamped_from=None if clamped_from is None else _convert(clamped_from, factor),
        )

    def _from_governed(
        self,
        measure: MeasureDef,
        resolved: regulatory_parameters.ResolvedParameter,
        factor: Decimal,
        *,
        as_of: date,
        clamped_from: Decimal | None = None,
    ) -> GovernedLimit:
        value = resolved.normalized_value
        assert value is not None  # noqa: S101 - callers check the scalar arm first
        return GovernedLimit(
            param_code=resolved.param_code,
            measure_id=measure.id,
            value=_convert(value, factor),
            stated_as=measure.value_type,
            authority="regulatory_register",
            source_unit=resolved.unit,
            source=resolved.source_citation,
            confirmation_status=resolved.confirmation_status,
            source_row_id=resolved.parameter_id,
            effective_from=resolved.effective_from,
            effective_to=self._governed_effective_to(resolved),
            as_of=as_of,
            clamped_from=None if clamped_from is None else _convert(clamped_from, factor),
        )

    def _governed_effective_to(
        self, resolved: regulatory_parameters.ResolvedParameter
    ) -> date | None:
        """The end of the window, read off the row the resolver chose.

        The resolved value does not carry it, so the row is found again by the
        id the resolver stamped on the answer — the resolution itself is never
        repeated here.
        """
        if self._governed is None:  # pragma: no cover - only reachable with a row
            return None
        for row in self._governed.rows:
            if str(row.id) == resolved.parameter_id:
                return row.effective_to
        return None


def _is_comparison_variant(measure_id: str) -> bool:
    """Whether the measure is a variance / attainment view of its base.

    A variance is ``actual - target`` and an attainment is ``actual / target``:
    neither is the quantity a register limit is about, so drawing the base's
    limit across one would state a limit on the wrong number. The base and its
    ``.actual`` restatement are the two that carry the figure itself.
    """
    return is_target_variant(measure_id) and not measure_id.endswith(_FIGURE_SUFFIX)


def _convert(value: Decimal, factor: Decimal) -> Decimal:
    """Scale ``value`` by an exact factor and state it without trailing zeros."""
    return _normalise(value if factor == _ONE else value * factor)


def _board_source(row: ParamCapitalThreshold) -> str:
    """What a board register row cites: its own approval evidence.

    The register has no citation column because there is nothing external to
    cite — a board limit is the institution's own decision, and who approved it
    when is the whole provenance.
    """
    approved_on = row.approval_timestamp.date().isoformat()
    return f"The institution's own board register, approved by {row.approved_by} on {approved_on}"


def measure_limit(db: Session, bank: Bank, measure: MeasureDef, *, as_of: date) -> LimitOutcome:
    """The limit governing one measure on one date.

    The single-measure form of :class:`LimitResolver`, built on the same code
    path rather than a second one, so a limit cannot differ between the widget
    that asked for one and the dashboard that asked for forty. Use the resolver
    for more than a handful.
    """
    return LimitResolver.load(db, bank, as_of_dates=[as_of]).for_measure(measure, as_of=as_of)


def conversion_factors() -> Mapping[tuple[str, ValueType], Decimal]:
    """The exact unit pairs this module will convert. For tests and audits."""
    return dict(_CONVERSIONS)
