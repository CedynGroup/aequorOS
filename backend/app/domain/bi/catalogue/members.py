"""Catalogue member types: what a BI measure, dimension or hierarchy declares.

The catalogue is the ONE place a queryable member is defined. Every attribute
the compiler, the authorization layer and the UI need is declared here, once,
per member — its table and column (as strings; the wave-2 compiler resolves
them against ``app.models.bi``), its authorization module and sensitivity
(D-028), its FX rule (D-015), its favourable direction, the register key its
thresholds come from (never a number) and, for engine figures, the registry
rule it is a copy of (D-022).

Conventions
-----------
* Ids are stable snake_case tokens joined with dots (``loans.balance_rc``,
  ``branch.region``). They are wire keys: renaming one is a catalogue version
  bump, and a saved dashboard that references the old id breaks loudly.
* Labels are production copy — no raw enums, no wire keys.
* ``module`` is an ``app.core.authorization.Module`` VALUE (``"cap"``,
  ``"credit"``), carried as a string so this package stays import-free of the
  authorization core; ``tests/architecture/test_bi_catalogue_authority.py``
  resolves every value through the enum.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.domain.bi.authority import AdvisoryDesignation

MemberKind = Literal["measure", "dimension"]
MeasureKind = Literal["certified_engine", "portfolio", "calculated"]
Aggregation = Literal[
    "sum",
    "count",
    "weighted_avg",
    "ratio_of_sums",
    "share",
    "top_n_share",
    "hhi",
    "flow_sum",
    "last_value",
]
TimeBehaviour = Literal["stock", "flow"]
Grain = Literal["institution", "portfolio"]
Sensitivity = Literal["published", "aggregated", "confidential", "restricted"]
FxRule = Literal["derivation", "classification"]
FavourableDirection = Literal["higher_better", "lower_better", "magnitude_lower_better", "neutral"]
Tier = Literal["live", "official"]
ValueType = Literal["amount", "pct", "ratio", "count", "text", "date", "flag"]

#: The four sensitivity levels, in ascending order of restriction.
SENSITIVITIES: tuple[Sensitivity, ...] = ("published", "aggregated", "confidential", "restricted")

#: The completeness check every DPD-dependent measure carries (D-042, D-046). A bank
#: that never supplied ``days_past_due`` has a NULL band on every mart loan row AND a
#: genuine engine ``0`` for portfolio-at-risk, so either figure would read 0 % —
#: indistinguishable from a clean book. The compiler returns NULL for the mart
#: population and the copied engine value stays the engine's; R10
#: (``dpd_completeness``) is what stops the trust badge reading green over both.
DPD_COMPLETENESS = "R10"


@dataclass(frozen=True, slots=True)
class ColumnRef:
    """A mart column named as strings; resolved to a mapped column in wave 2."""

    table: str
    column: str


@dataclass(frozen=True, slots=True)
class EnumValue:
    """One enumerated dimension value with its production label."""

    code: str
    label: str


FilterOp = Literal["in", "is_true", "is_false", "not_null"]


@dataclass(frozen=True, slots=True)
class RowFilter:
    """A predicate a measure applies to its own table before aggregating.

    ``values`` are wire codes (a position type, a band code), never numbers:
    a band predicate is spelled as the SET of band codes it covers, derived from
    the domain vocabulary, so no threshold literal ever enters the catalogue.
    """

    column: str
    op: FilterOp
    values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EngineRule:
    """Which engine figure a ``certified_engine`` measure is a copy of.

    ``metric_id`` is the registry / run key verbatim; ``module`` is the live
    module that produces it (``LIVE_MODULES`` vocabulary, not the authorization
    module); ``tier`` names which of the platform's two computation tiers the
    row is copied from — they are never blended.
    """

    metric_id: str
    module: str
    tier: Tier


@dataclass(frozen=True, slots=True)
class MemberDef:
    """What every catalogue member declares."""

    id: str
    kind: MemberKind
    module: str
    sensitivity: Sensitivity
    label: str
    source: ColumnRef
    description: str = ""

    @property
    def table(self) -> str:
        return self.source.table

    @property
    def column(self) -> str:
        return self.source.column


@dataclass(frozen=True, slots=True)
class DimensionDef(MemberDef):
    """A groupable / filterable attribute.

    ``values`` enumerates a closed vocabulary (with labels) when the dimension
    has one — grades, bands, buckets, position types — so the UI never shows a
    raw enum and Top-N / pivots have a stable order. Open vocabularies (branch
    codes, product codes, counterparties) leave it empty.
    """

    kind: MemberKind = field(default="dimension", init=False)
    value_type: ValueType = "text"
    values: tuple[EnumValue, ...] = ()


@dataclass(frozen=True, slots=True)
class MeasureDef(MemberDef):
    """A numeric member and everything that governs how it may be computed."""

    kind: MemberKind = field(default="measure", init=False)
    measure_kind: MeasureKind = "portfolio"
    aggregation: Aggregation = "sum"
    time_behaviour: TimeBehaviour = "stock"
    allowed_dimensions: tuple[str, ...] = ()
    grain: Grain = "portfolio"
    entitlement: str = ""
    """``institution_types.default_modules`` slug that entitles the tenant."""
    favourable_direction: FavourableDirection = "neutral"
    thresholds_source: str | None = None
    """Register / parameter CODE the measure's limit resolves from — never a number."""
    reconciliation_checks: tuple[str, ...] = ()
    engine_rule: EngineRule | None = None
    fx_rule: FxRule | None = None
    advisory_designation: AdvisoryDesignation | None = None
    value_type: ValueType = "amount"
    row_filters: tuple[RowFilter, ...] = ()
    numerator: str | None = None
    """Measure id of the numerator for ``ratio_of_sums`` / ``share``."""
    denominator: str | None = None
    """Measure id of the denominator for ``ratio_of_sums`` / ``share``."""
    weight: str | None = None
    """Measure id whose sum weights ``source`` for ``weighted_avg``."""
    over: str | None = None
    """Dimension id a ``top_n_share`` / ``hhi`` measure concentrates over."""

    @property
    def certified(self) -> bool:
        """Whether the UI may badge this figure "Platform-certified" (D-022).

        Only an engine figure copied from the SEALED tier under a FILED
        authority qualifies. A live-tier copy of the same metric is the worker's
        continuously re-derived view and is never filed; an advisory /
        supervisory-monitoring authority is analysis, not a filing; an
        unregistered figure has no declared owner at all (H-009).
        """
        return (
            self.measure_kind == "certified_engine"
            and self.engine_rule is not None
            and self.engine_rule.tier == "official"
            and self.advisory_designation == "filed"
        )


@dataclass(frozen=True, slots=True)
class HierarchyDef:
    """An ordered drill path over dimension ids, coarsest first."""

    id: str
    label: str
    levels: tuple[str, ...]
