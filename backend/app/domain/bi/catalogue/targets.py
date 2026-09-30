"""Target variants: every targetable measure's ``.actual`` / ``.target`` /
``.variance`` / ``.variance_pct`` / ``.attainment_pct``, once per register
version the bank may state (spec §Phase 2 "Targets").

A variant is derived from its base with :func:`dataclasses.replace`, so
``module``, ``sensitivity``, ``entitlement``, ``grain``, ``allowed_dimensions``
and ``advisory_designation`` are COPIED rather than restated: a variant cannot
be born broader than the measure it budgets, because nobody writes those fields
again. ``engine_rule`` is explicitly nulled and ``measure_kind`` becomes
``portfolio`` — a target is the BANK's own number and must never badge
"Platform-certified" (D-022), even when the figure it is compared against is a
sealed engine copy.

Every variant reads ONE table, ``bi_fact_target``, whose rows the mart builder
writes already matched to their actual (D-064). That is why a variance needs no
second fact and no join: the compiler's one-fact-table rule holds unchanged, and
a bank-wide target can never fan out across a scoped one.

Four rules the numbers depend on
--------------------------------
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
* **D-063 / D-071 — ``variance_pct`` is a DIFFERENCE for every unit-bearing
  measure and a RELATIVE change only for amounts and counts.** For a ``pct``
  base the variant is ``actual − target`` in the measure's own percentage points
  (6.0 pp against 5.0 pp reads 1.0 pp); D-071 settles the same convention for
  the three types the old single ``ratio`` type used to hide — a ``fraction``
  (read in percentage points once scaled), an ``index`` (index points) and a
  ``duration_years`` (years). For ``amount`` and ``count`` the relative
  ``(actual − target) / target`` is kept, because those have no natural unit for
  a difference to be quoted in beside the amount itself. **The label carries the
  unit, and that is the whole safety mechanism:** a difference quoted as if it
  were a relative change is a wrong number, and on a fractional rate the two
  conventions differ by roughly twenty times. ``_variance_pct_variant`` is the
  one place either label is written, and
  ``test_every_variance_pct_label_names_its_convention`` holds the label to the
  arithmetic per measure.
* **D-072 — both register versions are exposed, and a variant NAMES its
  version.** The register admits a ``budget`` (the approved plan) and a
  ``reforecast`` (the in-year revision) as coexisting rows, and the mart builder
  already stores both, so this is a catalogue-only exposure. Each version gets
  its own members — ``loans.balance_rc.budget.variance`` and
  ``loans.balance_rc.reforecast.variance`` — rather than one member that quietly
  prefers the later revision, because which plan a bank holds itself to is a
  statement about its own governance and not a default the platform may pick.
  The version rides in the member id, in its label, and in the population filter
  that selects its rows; nothing infers it.
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

#: Every register version a bank may state, each exposed as its own set of
#: members (D-072). Mirrors ``performance_targets.VERSIONS`` rather than importing
#: it — ``app/domain/ingestion`` is upstream of ``app/domain/bi`` and the register
#: that feeds the catalogue must not depend on the catalogue that reads it — and
#: the parity is asserted from the catalogue side, which is the side that may see
#: both (``tests/domain/bi/test_targets.py``). ``app/models/bi.py::TARGET_VERSIONS``
#: and the mart's ``ck_bi_fact_target_version`` are the third side of the same
#: vocabulary: a version added here without them is accepted on ingestion and then
#: refused when the builder tries to store its comparison.
TARGET_VERSIONS: tuple[str, ...] = ("budget", "reforecast")

#: The approved plan. Kept as a named constant because it is what a caller writing
#: one id by hand almost always means, and it is ``variant_id``'s default — but the
#: CATALOGUE never takes that default: ``variants_for`` names the version for both,
#: and every emitted member's id, label and population filter agree about which one
#: it is.
TARGET_VERSION = "budget"

#: How each version is named in production copy. A figure measured against a plan
#: has to say WHICH revision of the plan, in words a board reads, because "target"
#: alone is the ambiguity D-072 exists to remove.
VERSION_PHRASES: dict[str, str] = {
    "budget": "the approved budget",
    "reforecast": "the in-year reforecast",
}

#: The same two, as the leading noun of a label ("Approved budget target").
VERSION_NOUNS: dict[str, str] = {
    "budget": "Approved budget",
    "reforecast": "In-year reforecast",
}

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

#: Value types a target can be stated in — every value type that is a NUMBER.
#: ``text`` / ``date`` / ``flag`` are not comparable; no measure carries one today,
#: but the ``Literal`` admits them, so they are excluded by construction rather
#: than by absence. Stated rather than derived from ``NUMERIC_VALUE_TYPES`` because
#: "may a bank budget this?" is a decision and not a consequence of being numeric;
#: a test asserts the two coincide, so a divergence has to be deliberate.
_COMPARABLE_VALUE_TYPES: frozenset[str] = frozenset(
    {"amount", "pct", "fraction", "index", "duration_years", "count"}
)

#: Favourable directions for which ``attainment_pct`` is not emitted (D-062).
NO_ATTAINMENT_DIRECTIONS: frozenset[str] = frozenset({"lower_better", "magnitude_lower_better"})

#: Value types whose ``variance_pct`` is the DIFFERENCE in the measure's own unit
#: rather than the relative overshoot (D-063, extended to the other three
#: unit-bearing types by D-071) — mapped to the unit the reader sees the
#: difference in, which every such label must carry. A ``fraction`` is rendered
#: ×100 with a percent sign, so the difference between two of them is read in
#: percentage points, exactly as for a ``pct``.
DIFFERENCE_UNIT_LABELS: dict[str, str] = {
    "pct": "percentage points",
    "fraction": "percentage points",
    "index": "index points",
    "duration_years": "years",
}

#: Value types whose ``variance_pct`` is the relative ``(actual − target) /
#: target``: an amount and a count have no unit of their own for a difference to
#: be quoted in, so the share of the target is what "pct" can mean for them.
_RELATIVE_VALUE_TYPES: frozenset[str] = frozenset({"amount", "count"})

#: The label a relative variance carries. Deliberately unlike every entry in
#: ``DIFFERENCE_UNIT_LABELS``: the two conventions must not be confusable.
RELATIVE_UNIT_LABEL = "%"

__all__ = [
    "BANK_WIDE_SCOPE",
    "DIFFERENCE_UNIT_LABELS",
    "RELATIVE_UNIT_LABEL",
    "TARGET_TABLE",
    "TARGET_VERSION",
    "TARGET_VERSIONS",
    "VARIANT_SUFFIXES",
    "VERSION_NOUNS",
    "VERSION_PHRASES",
    "base_id_of",
    "is_target_variant",
    "is_targetable",
    "target_variants",
    "variant_id",
    "version_of",
]


def variant_id(base_id: str, suffix: str, *, version: str = TARGET_VERSION) -> str:
    """The member id of one variant: ``{base}.{version}.{suffix}`` (D-072).

    The version sits BEFORE the suffix so the suffix stays the last segment and
    remains reserved: a base whose id ends in one is still refused targetability,
    and there is no ``.budget.target.budget.target``.
    """
    return f"{base_id}.{version}.{suffix}"


def base_id_of(member_id: str) -> str:
    """The id of the measure a variant budgets — the inverse of :func:`variant_id`."""
    return member_id.rsplit(".", 2)[0]


def version_of(member_id: str) -> str:
    """The register version a variant names in its own id."""
    return member_id.rsplit(".", 2)[1]


def ends_in_reserved_suffix(member_id: str) -> bool:
    """Whether ``member_id`` ends in a variant suffix, whatever precedes it.

    Kept separate from :func:`is_target_variant`, which now means "is one of the
    members this module emits". The suffixes stay RESERVED against a base that
    merely ends in one, which the stricter test would no longer catch.
    """
    return any(member_id.endswith(f".{suffix}") for suffix in VARIANT_SUFFIXES)


def is_target_variant(member_id: str) -> bool:
    """Whether ``member_id`` is one of a base measure's target variants.

    A variant names its version as well as its suffix (D-072), so the test is over
    the whole ``.{version}.{suffix}`` tail.
    """
    return any(
        member_id.endswith(f".{version}.{suffix}")
        for version in TARGET_VERSIONS
        for suffix in VARIANT_SUFFIXES
    )


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
    if measure.table == TARGET_TABLE or ends_in_reserved_suffix(measure.id):
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
    """Whether the base's unit settles what a ``variance_pct`` means (D-063, D-071).

    True for every comparable base since D-071: the two conventions partition the
    comparable value types, so no measure is left without one and none has both.
    """
    return measure.value_type in set(DIFFERENCE_UNIT_LABELS) | _RELATIVE_VALUE_TYPES


def variance_pct_is_a_difference(measure: MeasureDef) -> bool:
    """D-063 / D-071: a unit-bearing base states its variance as a difference."""
    return measure.value_type in DIFFERENCE_UNIT_LABELS


def variance_pct_unit_label(measure: MeasureDef) -> str:
    """The unit the base's ``variance_pct`` label must carry, per its convention."""
    return DIFFERENCE_UNIT_LABELS.get(measure.value_type, RELATIVE_UNIT_LABEL)


def _population(base: MeasureDef, version: str) -> tuple[RowFilter, ...]:
    """The rows of ``bi_fact_target`` this variant is about.

    All three are POPULATION predicates, not selections: outside them a row
    contributes NULL, so a bank holding targets for OTHER measures — or for the
    other register version — on the same date does not turn this measure's
    missing target into a zero.
    """
    return (
        RowFilter("measure_id", "in", (base.id,)),
        RowFilter("scope_dimension", "in", (BANK_WIDE_SCOPE,)),
        RowFilter("target_version", "in", (version,)),
    )


def _variant(  # noqa: PLR0913 - one keyword per field the variant restates
    base: MeasureDef,
    suffix: str,
    version: str,
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

    ``reads_the_actual`` is false for ``.target`` alone. A limit describes how
    the PLATFORM derived a figure, and the target is the one variant that
    contains none of the platform's arithmetic — it is the number the bank
    stated. Every other variant carries the base's limit source, because every
    other variant is a function of the actual.
    """
    return replace(
        base,
        id=variant_id(base.id, suffix, version=version),
        label=label,
        description=description,
        source=ColumnRef(TARGET_TABLE, column),
        measure_kind="portfolio",
        aggregation=aggregation,
        time_behaviour="stock",
        thresholds_source=base.thresholds_source if reads_the_actual else None,
        engine_rule=None,
        fx_rule=None,
        value_type=base.value_type if value_type is None else value_type,
        favourable_direction=(
            base.favourable_direction if favourable_direction is None else favourable_direction
        ),
        row_filters=_population(base, version),
        numerator=numerator,
        denominator=denominator,
        weight=None,
        over=None,
    )


def _variance_pct_variant(base: MeasureDef, version: str) -> MeasureDef:
    """``variance_pct``, in whichever unit D-063 and D-071 settle for this base.

    The DIFFERENCE branch keeps the base's own value type, which is what makes the
    figure render in the base's own unit wherever it is read: the pre-computed
    ``variance_value`` column already holds ``actual − target`` in that unit, so a
    fraction's difference is a fraction (and reads as percentage points once a
    surface scales it), a duration's is years, an index's is index points. Typing
    it ``pct`` instead would make a surface treat ``0.01`` as one hundredth of a
    percentage point — wrong by a hundred, and invisibly so.
    """
    phrase = VERSION_PHRASES[version]
    if variance_pct_is_a_difference(base):
        unit = DIFFERENCE_UNIT_LABELS[base.value_type]
        return _variant(
            base,
            VARIANCE_PCT_SUFFIX,
            version,
            column="variance_value",
            label=f"{base.label} · Variance against {phrase} ({unit})",
            description=(
                f"Actual minus the target set in {phrase}, stated as a difference in "
                f"{unit} rather than as a relative overshoot: an outcome of six against "
                f"a plan of five is one {unit.removesuffix('s')} off plan."
            ),
        )
    return _variant(
        base,
        VARIANCE_PCT_SUFFIX,
        version,
        column="variance_value",
        label=f"{base.label} · Variance against {phrase} ({RELATIVE_UNIT_LABEL})",
        description=(
            f"Actual minus target as a share of the target set in {phrase}. Undefined, "
            "and therefore blank, when the target is zero or was never stated."
        ),
        aggregation="ratio_of_sums",
        value_type="pct",
        numerator=variant_id(base.id, VARIANCE_SUFFIX, version=version),
        denominator=variant_id(base.id, TARGET_SUFFIX, version=version),
    )


def variants_for_version(base: MeasureDef, version: str) -> tuple[MeasureDef, ...]:
    """Every variant of one targetable base against ONE register version."""
    phrase = VERSION_PHRASES[version]
    noun = VERSION_NOUNS[version]
    out: list[MeasureDef] = [
        _variant(
            base,
            ACTUAL_SUFFIX,
            version,
            column="actual_value",
            label=f"{base.label} · Actual against {phrase}",
            description=(
                f"The outcome {phrase} is measured against, matched to that plan's own "
                "period and basis so the two are read on the same footing."
            ),
        ),
        _variant(
            base,
            TARGET_SUFFIX,
            version,
            column="target_value",
            label=f"{base.label} · {noun} target",
            description=(
                f"The figure the bank set for the period in {phrase}, in the measure's "
                "own unit. Blank when no target was stated — never zero."
            ),
            favourable_direction="neutral",
            reads_the_actual=False,
        ),
        _variant(
            base,
            VARIANCE_SUFFIX,
            version,
            column="variance_value",
            label=f"{base.label} · Variance against {phrase}",
            description=(
                f"Actual minus the target set in {phrase}, in the measure's own unit. A "
                "target of zero is a real target, so the variance against it is a real "
                "number."
            ),
        ),
    ]
    if emits_variance_pct(base):
        out.append(_variance_pct_variant(base, version))
    if emits_attainment(base):
        out.append(
            _variant(
                base,
                ATTAINMENT_PCT_SUFFIX,
                version,
                column="actual_value",
                label=f"{base.label} · {noun} attainment",
                description=(
                    f"Actual as a share of the target set in {phrase}. Undefined, and "
                    "therefore blank, when the target is zero or was never stated."
                ),
                aggregation="ratio_of_sums",
                value_type="pct",
                favourable_direction="higher_better",
                numerator=variant_id(base.id, ACTUAL_SUFFIX, version=version),
                denominator=variant_id(base.id, TARGET_SUFFIX, version=version),
            )
        )
    return tuple(out)


def variants_for(base: MeasureDef) -> tuple[MeasureDef, ...]:
    """Every target variant of one targetable base, in catalogue order.

    Grouped by register version so a client reading the catalogue sees one
    coherent block per revision of the plan rather than the two interleaved.
    """
    return tuple(
        variant for version in TARGET_VERSIONS for variant in variants_for_version(base, version)
    )


def targetable_measures(bases: tuple[MeasureDef, ...]) -> tuple[MeasureDef, ...]:
    return tuple(base for base in bases if is_targetable(base))


def target_variants(bases: tuple[MeasureDef, ...]) -> tuple[MeasureDef, ...]:
    """Every target variant of every targetable measure in ``bases``."""
    return tuple(variant for base in targetable_measures(bases) for variant in variants_for(base))
