"""Target variants: every targetable measure's ``.actual`` / ``.target`` /
``.variance`` / ``.variance_pct`` / ``.attainment_pct`` (spec §Phase 2 "Targets").

A variant is derived from its base with :func:`dataclasses.replace`, so
``module``, ``sensitivity``, ``entitlement``, ``grain``, ``allowed_dimensions``
and ``advisory_designation`` are COPIED rather than restated: a variant cannot
be born broader than the measure it budgets, because nobody writes those fields
again. ``engine_rule`` is explicitly nulled and ``measure_kind`` becomes
``portfolio`` — a target is the BANK's own number and must never badge
"Platform-certified" (D-022), even when the figure it is compared against is a
sealed engine copy.

All five variants read ONE table, ``bi_fact_target``, whose rows the mart
builder writes already matched to their actual (D-064). That is why a variance
needs no second fact and no join: the compiler's one-fact-table rule holds
unchanged, and a bank-wide target can never fan out across a scoped one.

Three rules the numbers depend on
---------------------------------
* **A missing target is NULL, never zero.** The row exists only because a
  target does, so the measure's own discriminators (``measure_id``,
  ``scope_dimension``, ``target_version``) are POPULATION filters: outside them
  the row contributes NULL, and a sum of nothing is NULL. A target OF zero is
  legitimate (zero write-offs) and still yields a real variance; it is only the
  two percentage variants that go NULL, because dividing by it is undefined —
  and they divide through the compiler's own ``_ratio``, which is already a
  safe division.
* **D-062 — no ``attainment_pct`` where lower is better.** ``actual / target``
  is direction-blind: 120 % of an NPL-ratio target is a MISS that reads as
  over-achievement the moment the number is quoted away from its badge. A
  ``lower_better`` or ``magnitude_lower_better`` base therefore gets the other
  variants and not that one, by construction.
* **D-063 — ``variance_pct`` on a percentage-valued measure is in PERCENTAGE
  POINTS.** For a ``pct`` base the variant is ``actual − target`` in the
  measure's own unit (6.0 pp against 5.0 pp reads 1.0 pp); for ``amount`` and
  ``count`` bases the relative ``(actual − target) / target`` is kept. Each
  label names which convention is in force. A ``ratio``-valued base gets NO
  ``variance_pct`` at all: neither convention is settled for it, and
  ``.variance`` already states the difference unambiguously in the base's unit.
"""

from __future__ import annotations

from dataclasses import replace

from app.domain.bi.catalogue.members import (
    Aggregation,
    ColumnRef,
    FavourableDirection,
    MeasureDef,
    RowFilter,
    ValueType,
)

#: The mart the builder pre-matches targets into (``app/models/bi.py``).
TARGET_TABLE = "bi_fact_target"

#: ``bi_fact_target.scope_dimension`` / ``scope_value`` for a bank-wide target.
#: The variants read the bank-wide resolution and nothing else, so no query can
#: sum a bank-wide target together with the scoped ones beside it.
BANK_WIDE_SCOPE = ""

#: The register version the variants compare against. ``reforecast`` rows are
#: pre-matched and stored beside the budget, but which of the two a variance is
#: measured against is a statement about the bank's own governance, so the
#: catalogue names one rather than silently preferring the later revision.
TARGET_VERSION = "budget"

#: Id suffixes, in catalogue order. Every one is reserved: a base whose id ends
#: in one of them is not targetable, so there is no ``.target.target``.
ACTUAL_SUFFIX = "actual"
TARGET_SUFFIX = "target"
VARIANCE_SUFFIX = "variance"
VARIANCE_PCT_SUFFIX = "variance_pct"
ATTAINMENT_PCT_SUFFIX = "attainment_pct"
VARIANT_SUFFIXES: tuple[str, ...] = (
    ACTUAL_SUFFIX,
    TARGET_SUFFIX,
    VARIANCE_SUFFIX,
    VARIANCE_PCT_SUFFIX,
    ATTAINMENT_PCT_SUFFIX,
)

#: Aggregations that compute at a SECOND grouping level (the compiler's
#: ``_MeasurePlan.second_level``). A variance over one would need a third level
#: the compiler has no shape for, and a "target" for a concentration share is
#: not a figure a budget states.
_UNTARGETABLE_AGGREGATIONS: frozenset[str] = frozenset({"top_n_share", "hhi"})

#: Value types a target can be stated in. ``text`` / ``date`` / ``flag`` are not
#: comparable; no measure carries one today, but the ``Literal`` admits them, so
#: they are excluded by construction rather than by absence.
_COMPARABLE_VALUE_TYPES: frozenset[str] = frozenset({"amount", "pct", "ratio", "count"})

#: Favourable directions for which ``attainment_pct`` is not emitted (D-062).
NO_ATTAINMENT_DIRECTIONS: frozenset[str] = frozenset({"lower_better", "magnitude_lower_better"})

#: Value types whose ``variance_pct`` is the DIFFERENCE in the measure's own
#: unit rather than the relative overshoot (D-063).
_POINT_DIFFERENCE_VALUE_TYPES: frozenset[str] = frozenset({"pct"})

#: Value types whose ``variance_pct`` is the relative ``(actual − target) /
#: target``. A ``ratio`` base is in NEITHER set, so it gets no ``variance_pct``.
_RELATIVE_VALUE_TYPES: frozenset[str] = frozenset({"amount", "count"})

__all__ = [
    "BANK_WIDE_SCOPE",
    "TARGET_TABLE",
    "TARGET_VERSION",
    "VARIANT_SUFFIXES",
    "is_target_variant",
    "is_targetable",
    "target_variants",
    "variant_id",
]


def variant_id(base_id: str, suffix: str) -> str:
    return f"{base_id}.{suffix}"


def is_target_variant(member_id: str) -> bool:
    """Whether ``member_id`` is one of a base measure's target variants."""
    return any(member_id.endswith(f".{suffix}") for suffix in VARIANT_SUFFIXES)


def is_targetable(measure: MeasureDef) -> bool:
    """Whether a bank may state a budget figure for ``measure``.

    Derived from what the measure DECLARES, never from a list of ids, so a
    measure added tomorrow cannot arrive silently targetable — or silently
    un-targetable — because somebody forgot a table.

    Engine measures are targetable on the OFFICIAL tier only: a budget is
    compared against the figure the bank filed, not against the worker's
    continuously re-derived live view of the same metric, and the two tiers are
    never blended (H-008).
    """
    if measure.measure_kind not in ("portfolio", "certified_engine"):
        return False
    if measure.table == TARGET_TABLE or is_target_variant(measure.id):
        return False
    if measure.value_type not in _COMPARABLE_VALUE_TYPES:
        return False
    if measure.aggregation in _UNTARGETABLE_AGGREGATIONS:
        return False
    return measure.engine_rule is None or measure.engine_rule.tier == "official"


def emits_attainment(measure: MeasureDef) -> bool:
    """D-062: a measure where lower is better gets no ``attainment_pct``."""
    return measure.favourable_direction not in NO_ATTAINMENT_DIRECTIONS


def emits_variance_pct(measure: MeasureDef) -> bool:
    """Whether the base's unit settles what a ``variance_pct`` would mean (D-063)."""
    return measure.value_type in _POINT_DIFFERENCE_VALUE_TYPES | _RELATIVE_VALUE_TYPES


def variance_pct_is_percentage_points(measure: MeasureDef) -> bool:
    """D-063: a percentage-valued base's variance is stated in points."""
    return measure.value_type in _POINT_DIFFERENCE_VALUE_TYPES


def _population(base: MeasureDef) -> tuple[RowFilter, ...]:
    """The rows of ``bi_fact_target`` this base's variants are about.

    All three are POPULATION predicates, not selections: outside them a row
    contributes NULL, so a bank holding targets for OTHER measures on the same
    date does not turn this measure's missing target into a zero.
    """
    return (
        RowFilter("measure_id", "in", (base.id,)),
        RowFilter("scope_dimension", "in", (BANK_WIDE_SCOPE,)),
        RowFilter("target_version", "in", (TARGET_VERSION,)),
    )


def _variant(  # noqa: PLR0913 - one keyword per field the variant restates
    base: MeasureDef,
    suffix: str,
    *,
    column: str,
    label: str,
    description: str,
    aggregation: Aggregation = "sum",
    value_type: ValueType | None = None,
    favourable_direction: FavourableDirection | None = None,
    numerator: str | None = None,
    denominator: str | None = None,
    reads_the_actual: bool = True,
) -> MeasureDef:
    """One variant, derived from ``base`` so its scope is copied, not restated.

    ``time_behaviour`` is ``stock`` on every variant whatever the base's is:
    the pre-matched row is a SNAPSHOT of the comparison on its date (for a flow
    target, of the accumulation so far), so a range query must take the last
    date with data per grain. Summing the rows of a window would count one
    target once per business day.

    ``reads_the_actual`` is false for ``.target`` alone. A limit and a
    reconciliation check both describe how the PLATFORM derived a figure, and
    the target is the one variant that contains none of the platform's
    arithmetic — it is the number the bank stated. Every other variant carries
    the base's limit source and its checks, because every other variant is a
    function of the actual.
    """
    return replace(
        base,
        id=variant_id(base.id, suffix),
        label=label,
        description=description,
        source=ColumnRef(TARGET_TABLE, column),
        measure_kind="portfolio",
        aggregation=aggregation,
        time_behaviour="stock",
        thresholds_source=base.thresholds_source if reads_the_actual else None,
        reconciliation_checks=base.reconciliation_checks if reads_the_actual else (),
        engine_rule=None,
        fx_rule=None,
        value_type=base.value_type if value_type is None else value_type,
        favourable_direction=(
            base.favourable_direction if favourable_direction is None else favourable_direction
        ),
        row_filters=_population(base),
        numerator=numerator,
        denominator=denominator,
        weight=None,
        over=None,
    )


def _variance_pct_variant(base: MeasureDef) -> MeasureDef:
    """``variance_pct``, in whichever unit D-063 settles for this base."""
    if variance_pct_is_percentage_points(base):
        return _variant(
            base,
            VARIANCE_PCT_SUFFIX,
            column="variance_value",
            label=f"{base.label} · Variance against target (percentage points)",
            description=(
                "Actual minus target in the measure's own percentage points, not the "
                "relative overshoot: a six-point outcome against a five-point plan is "
                "one point off plan."
            ),
            value_type="pct",
        )
    return _variant(
        base,
        VARIANCE_PCT_SUFFIX,
        column="variance_value",
        label=f"{base.label} · Variance against target (%)",
        description=(
            "Actual minus target as a share of the target. Undefined, and therefore "
            "blank, when the target is zero or was never stated."
        ),
        aggregation="ratio_of_sums",
        value_type="pct",
        numerator=variant_id(base.id, VARIANCE_SUFFIX),
        denominator=variant_id(base.id, TARGET_SUFFIX),
    )


def variants_for(base: MeasureDef) -> tuple[MeasureDef, ...]:
    """Every target variant of one targetable base, in catalogue order."""
    out: list[MeasureDef] = [
        _variant(
            base,
            ACTUAL_SUFFIX,
            column="actual_value",
            label=f"{base.label} · Actual against target",
            description=(
                "The outcome the target is measured against, matched to the target's "
                "own period and basis so the two are read on the same footing."
            ),
        ),
        _variant(
            base,
            TARGET_SUFFIX,
            column="target_value",
            label=f"{base.label} · Target",
            description=(
                "The figure the bank set for the period through its own budget, in the "
                "measure's own unit. Blank when no target was stated — never zero."
            ),
            favourable_direction="neutral",
            reads_the_actual=False,
        ),
        _variant(
            base,
            VARIANCE_SUFFIX,
            column="variance_value",
            label=f"{base.label} · Variance against target",
            description=(
                "Actual minus target, in the measure's own unit. A target of zero is a "
                "real target, so the variance against it is a real number."
            ),
        ),
    ]
    if emits_variance_pct(base):
        out.append(_variance_pct_variant(base))
    if emits_attainment(base):
        out.append(
            _variant(
                base,
                ATTAINMENT_PCT_SUFFIX,
                column="actual_value",
                label=f"{base.label} · Target attainment",
                description=(
                    "Actual as a share of target. Undefined, and therefore blank, when "
                    "the target is zero or was never stated."
                ),
                aggregation="ratio_of_sums",
                value_type="pct",
                favourable_direction="higher_better",
                numerator=variant_id(base.id, ACTUAL_SUFFIX),
                denominator=variant_id(base.id, TARGET_SUFFIX),
            )
        )
    return tuple(out)


def targetable_measures(bases: tuple[MeasureDef, ...]) -> tuple[MeasureDef, ...]:
    return tuple(base for base in bases if is_targetable(base))


def target_variants(bases: tuple[MeasureDef, ...]) -> tuple[MeasureDef, ...]:
    """Every target variant of every targetable measure in ``bases``."""
    return tuple(variant for base in targetable_measures(bases) for variant in variants_for(base))
