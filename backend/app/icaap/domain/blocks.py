"""The ICAAP data-block catalogue and staleness rules (pure domain).

A data block is the only way a number reaches ICAAP prose. Each block declares
which sealed source it may bind to and which facts it exposes; the section
editor then quotes a fact by key rather than typing a figure, so a refreshed
block updates every sentence that cites it and the frozen report carries the
figures the Board actually approved.

Two rules are load-bearing and live here rather than in a service:

* **Blocks never read the live plane.** Every resolver binds a sealed
  ``RegulatoryRun``, package, sign-off, approved plan, snapshot or register
  digest for the cycle's exact as-of date. ``live_metrics`` is a monitoring
  surface that the worker rewrites continuously; a filed report cannot cite it.
  ``tests/architecture/test_icaap_boundaries.py`` enforces the import ban.
* **Staleness is computed on read, never stored.** A binding records what it
  pinned; :func:`evaluate_status` compares that with what would bind now, so a
  new capital run makes every cycle that cites it say so immediately.

No regulatory number appears in this module (D-024): a block exposes fact
*keys* and *kinds*, and the values come from the engines and the governed
parameter control plane.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from types import MappingProxyType
from typing import Literal

FactKind = Literal["ratio_pct", "amount", "count", "years", "boolean", "date", "text", "multiplier"]
AsOfRule = Literal["exact", "none"]
BlockPhase = Literal["P1", "P2", "P3", "P5"]

#: What a binding pinned. ``computed`` covers a resolution with no sealed row of
#: its own (a concentration read recomputed from the canonical book), which is
#: why the binding also stores the inputs digest.
SOURCE_KINDS: tuple[str, ...] = (
    "run",
    "package",
    "signoff",
    "plan",
    "register",
    "snapshot",
    "computed",
    "manual",
)


@dataclass(frozen=True)
class FactSpec:
    """One quotable figure a block exposes."""

    key: str
    label: str
    kind: FactKind


@dataclass(frozen=True)
class BlockSpec:
    type: str
    title: str
    phase: BlockPhase
    source_kinds: tuple[str, ...]
    facts: tuple[FactSpec, ...]
    #: Manual tables declare their fact keys on the binding, not here.
    dynamic_facts: bool
    as_of_rule: AsOfRule
    manual: bool
    #: Never publishable under the disclosure regime, whatever the bank selects
    #: (a supervisory add-on is the regulator's private instruction).
    never_public: bool

    @property
    def fact_keys(self) -> frozenset[str]:
        return frozenset(fact.key for fact in self.facts)


def _spec(  # noqa: PLR0913 - the catalogue row is an explicit tuple
    type_: str,
    title: str,
    phase: BlockPhase,
    *,
    source_kinds: tuple[str, ...],
    facts: tuple[FactSpec, ...] = (),
    dynamic_facts: bool = False,
    as_of_rule: AsOfRule = "exact",
    manual: bool = False,
    never_public: bool = False,
) -> BlockSpec:
    return BlockSpec(
        type=type_,
        title=title,
        phase=phase,
        source_kinds=source_kinds,
        facts=facts,
        dynamic_facts=dynamic_facts,
        as_of_rule=as_of_rule,
        manual=manual,
        never_public=never_public,
    )


def _f(key: str, label: str, kind: FactKind) -> FactSpec:
    return FactSpec(key=key, label=label, kind=kind)


_P1_SPECS: tuple[BlockSpec, ...] = (
    _spec(
        "capital_position",
        "Capital position",
        "P1",
        source_kinds=("run",),
        facts=(
            _f("car_pct", "Total capital ratio", "ratio_pct"),
            _f("cet1_ratio_pct", "CET1 ratio", "ratio_pct"),
            _f("tier1_ratio_pct", "Tier 1 ratio", "ratio_pct"),
            _f("leverage_ratio_pct", "Leverage ratio", "ratio_pct"),
            _f("total_capital", "Total regulatory capital", "amount"),
            _f("total_rwa", "Total risk-weighted assets", "amount"),
        ),
    ),
    _spec(
        "pillar1_rwa",
        "Pillar 1 risk-weighted assets",
        "P1",
        source_kinds=("run",),
        facts=(
            _f("credit_rwa", "Credit risk-weighted assets", "amount"),
            _f("market_rwa", "Market risk-weighted assets", "amount"),
            _f("operational_rwa", "Operational risk-weighted assets", "amount"),
            _f("total_rwa", "Total risk-weighted assets", "amount"),
        ),
    ),
    _spec(
        "appendix_ii",
        "Stress test results (Appendix II)",
        "P1",
        source_kinds=("run", "signoff", "package"),
        facts=(
            _f("horizon_years", "Projection horizon", "years"),
            _f("car_target_pct", "Capital ratio target used", "ratio_pct"),
            _f("with_management_actions", "Management actions applied", "boolean"),
            _f("stays_above_all_minima", "Stays above every minimum", "boolean"),
            _f("scenario_code", "Scenario", "text"),
        ),
    ),
    _spec(
        "stress_narratives",
        "Stress test narratives and attestation",
        "P1",
        source_kinds=("signoff",),
        facts=(
            _f("attested_on", "Attested on", "date"),
            _f("stays_above_all_minima", "Stays above every minimum", "boolean"),
            _f(
                "with_actions_stays_above_all_minima",
                "Stays above every minimum after management actions",
                "boolean",
            ),
        ),
    ),
    _spec(
        "reverse_stress",
        "Reverse stress test",
        "P1",
        source_kinds=("run",),
        facts=(
            _f("capital_breached", "Capital breach reached", "boolean"),
            _f("liquidity_breached", "Liquidity breach reached", "boolean"),
            _f("capital_breach_multiplier", "Capital breach severity multiple", "multiplier"),
            _f("liquidity_breach_multiplier", "Liquidity breach severity multiple", "multiplier"),
            _f("cet1_floor_pct", "CET1 floor tested", "ratio_pct"),
            _f("lcr_floor_pct", "Liquidity coverage floor tested", "ratio_pct"),
        ),
    ),
    _spec(
        "capital_plan",
        "Capital plan",
        "P1",
        source_kinds=("plan",),
        facts=(
            _f("plan_version", "Approved plan version", "count"),
            _f("approval_expires_on", "Approval expires on", "date"),
            _f("approval_overdue", "Approval overdue", "boolean"),
            _f("pillar2_addon_total_pct", "Total Pillar 2 add-on", "ratio_pct"),
            _f("projection_y1_car_pct", "Projected total capital ratio, year 1", "ratio_pct"),
            _f("projection_y2_car_pct", "Projected total capital ratio, year 2", "ratio_pct"),
            _f("projection_y3_car_pct", "Projected total capital ratio, year 3", "ratio_pct"),
            _f("projection_y4_car_pct", "Projected total capital ratio, year 4", "ratio_pct"),
            _f("projection_y5_car_pct", "Projected total capital ratio, year 5", "ratio_pct"),
        ),
    ),
    _spec(
        "ilaap",
        "Liquidity adequacy (ILAAP)",
        "P1",
        source_kinds=("snapshot",),
        facts=(
            _f("ilaap_adequate", "Liquidity assessed adequate", "boolean"),
            _f("cfp_approved", "Contingency funding plan approved", "boolean"),
            _f("cfp_active", "Contingency funding plan activated", "boolean"),
            _f("lcr_pct", "Liquidity coverage ratio", "ratio_pct"),
            _f("nsfr_pct", "Net stable funding ratio", "ratio_pct"),
            _f("worst_stressed_lcr_pct", "Worst stressed liquidity coverage ratio", "ratio_pct"),
            _f("ewi_escalation_state", "Early-warning escalation state", "text"),
        ),
    ),
    _spec(
        "concentration",
        "Credit concentration",
        "P1",
        source_kinds=("computed",),
        facts=(
            _f("hhi_single_name", "Single-name concentration index", "ratio_pct"),
            _f("hhi_sector", "Sector concentration index", "ratio_pct"),
            _f("hhi_geography", "Geographic concentration index", "ratio_pct"),
            _f("hhi_product", "Product concentration index", "ratio_pct"),
            _f("hhi_collateral", "Collateral concentration index", "ratio_pct"),
            _f("hhi_employer", "Employer concentration index", "ratio_pct"),
            _f("coverage_single_name_pct", "Single-name coverage of the book", "ratio_pct"),
            _f("coverage_sector_pct", "Sector coverage of the book", "ratio_pct"),
            _f("coverage_geography_pct", "Geographic coverage of the book", "ratio_pct"),
            _f("coverage_product_pct", "Product coverage of the book", "ratio_pct"),
            _f("coverage_collateral_pct", "Collateral coverage of the book", "ratio_pct"),
            _f("coverage_employer_pct", "Employer coverage of the book", "ratio_pct"),
            _f("breach_count", "Limits breached", "count"),
            _f("capital_basis", "Capital basis used for limits", "text"),
        ),
    ),
    _spec(
        "irrbb",
        "Interest rate risk in the banking book",
        "P1",
        source_kinds=("run",),
        facts=(
            _f("eve_base", "Economic value of equity, base", "amount"),
            _f("tier1", "Tier 1 capital", "amount"),
            _f("nii_base", "Net interest income, base", "amount"),
            _f("worst_scenario", "Worst scenario", "text"),
        ),
    ),
    _spec(
        "institution_profile",
        "Institution profile",
        "P1",
        source_kinds=("register",),
        facts=(
            _f("legal_entity_structure", "Legal entity structure", "text"),
            _f("institution_type", "Licence type", "text"),
            _f("parent_country_code", "Parent country", "text"),
            _f("ownership_local_pct", "Local ownership", "ratio_pct"),
            _f("ownership_foreign_pct", "Foreign ownership", "ratio_pct"),
        ),
        as_of_rule="none",
    ),
    _spec(
        "management_actions",
        "Management actions",
        "P1",
        source_kinds=("run", "plan"),
        facts=(
            _f("action_count", "Actions in the plan", "count"),
            _f(
                "with_actions_stays_above_all_minima",
                "Stays above every minimum after management actions",
                "boolean",
            ),
        ),
    ),
    _spec(
        "financials",
        "Financial summary",
        "P1",
        source_kinds=("manual",),
        dynamic_facts=True,
        as_of_rule="none",
        manual=True,
    ),
    _spec(
        "manual_table",
        "Manual table",
        "P1",
        source_kinds=("manual",),
        dynamic_facts=True,
        as_of_rule="none",
        manual=True,
    ),
)

#: The registers the ICAAP builds for itself (P2). These have no sealed run to
#: bind: the binding pins a value-based DIGEST of the register's own content,
#: so a refresh that changes nothing writes nothing and a changed figure makes
#: every sentence quoting it stale. ``as_of_rule="none"`` because a register is
#: the cycle's own state, not a position at a date.
_P2_SPECS: tuple[BlockSpec, ...] = (
    _spec(
        "risk_register",
        "Risk register",
        "P2",
        source_kinds=("computed",),
        facts=(
            _f("category_count", "Risk categories assessed", "count"),
            _f("assessed_risk_count", "Risks scored", "count"),
            _f("material_risk_count", "Material risks", "count"),
        ),
        as_of_rule="none",
    ),
    _spec(
        "risk_appetite",
        "Risk appetite",
        "P2",
        source_kinds=("computed",),
        facts=(
            _f("ras_metric_count", "Appetite metrics", "count"),
            _f("ras_breach_count", "Metrics beyond tolerance", "count"),
            _f("ras_amber_count", "Metrics beyond appetite", "count"),
        ),
        as_of_rule="none",
    ),
    _spec(
        "pillar2_summary",
        "Pillar 2 capital summary",
        "P2",
        source_kinds=("computed",),
        facts=(
            _f("pillar2_total_baseline", "Total Pillar 2 capital", "amount"),
            _f("pillar2_total_stressed", "Total Pillar 2 capital, stressed", "amount"),
            _f("pillar2_item_count", "Pillar 2 figures", "count"),
            _f("pillar2_all_approved", "Every figure approved", "boolean"),
            _f("irrbb_outlier_measure_pct", "Economic-value loss against Tier 1", "ratio_pct"),
            _f("irrbb_outlier", "Interest-rate outlier", "boolean"),
        ),
        as_of_rule="none",
    ),
    _spec(
        "table5_pillar2",
        "Appendix II Table 5 (capital requirement)",
        "P2",
        source_kinds=("computed",),
        facts=(
            _f("pillar2_total_current", "Pillar 2 requirement, current", "amount"),
            _f("pillar2_total_stress_y1", "Pillar 2 requirement, stress year 1", "amount"),
            _f(
                "total_capital_requirement_current",
                "Total capital requirement, current",
                "amount",
            ),
            _f(
                "total_capital_requirement_stress_y1",
                "Total capital requirement, stress year 1",
                "amount",
            ),
            _f("table5_partial", "Grid has a partially covered row", "boolean"),
        ),
        as_of_rule="none",
    ),
    _spec(
        "capital_reconciliation",
        "Internal and regulatory capital reconciliation",
        "P2",
        source_kinds=("computed",),
        facts=(
            _f("total_internal_requirement", "Total internal capital requirement", "amount"),
            _f("total_regulatory_requirement", "Total regulatory requirement", "amount"),
            _f("available_internal_capital", "Available internal capital", "amount"),
            _f("recognised_regulatory_capital", "Recognised regulatory capital", "amount"),
            _f("internal_capital_surplus", "Internal capital surplus", "amount"),
            _f("internal_capital_coverage_pct", "Internal capital coverage", "ratio_pct"),
        ),
        as_of_rule="none",
    ),
    _spec(
        "capital_allocation",
        "Internal capital allocation",
        "P2",
        source_kinds=("computed",),
        facts=(_f("allocation_unit_count", "Allocation units", "count"),),
        as_of_rule="none",
    ),
    _spec(
        "capital_triggers",
        "Capital plan triggers",
        "P2",
        source_kinds=("computed",),
        facts=(
            _f("trigger_count", "Triggers in the plan", "count"),
            _f("triggers_breached_now", "Triggers breached now", "count"),
            _f("first_action_year", "First year an action level is crossed", "count"),
        ),
        as_of_rule="none",
    ),
    _spec(
        "audit_review",
        "Independent review",
        "P2",
        source_kinds=("computed",),
        facts=(
            _f("latest_review_date", "Most recent review", "date"),
            _f("latest_review_opinion", "Overall opinion", "text"),
            _f("open_findings_count", "Open findings", "count"),
        ),
        as_of_rule="none",
    ),
    _spec(
        "challenge_log",
        "Challenge and adoption log",
        "P2",
        source_kinds=("computed",),
        facts=(
            _f("challenge_count", "Challenges recorded", "count"),
            _f("open_challenge_count", "Challenges unanswered", "count"),
            _f("board_challenge_count", "Board-level challenges", "count"),
        ),
        as_of_rule="none",
    ),
    _spec(
        "supervisory_addons",
        "Supervisory capital add-ons",
        "P2",
        source_kinds=("computed",),
        facts=(_f("supervisory_addon_total", "Supervisory add-ons in force", "amount"),),
        as_of_rule="none",
        never_public=True,
    ),
    _spec(
        "fx_position",
        "Net open foreign exchange position",
        "P2",
        source_kinds=("run",),
        facts=(
            _f("nop", "Net open position", "amount"),
            _f("nop_pct_tier1", "Net open position against Tier 1", "ratio_pct"),
            _f("tier1", "Tier 1 capital", "amount"),
        ),
    ),
    _spec(
        "sovereign_exposures",
        "Sovereign exposures",
        "P2",
        source_kinds=("computed",),
        facts=(
            _f("sovereign_exposure_total", "Sovereign exposure", "amount"),
            _f("sovereign_rwa_total", "Sovereign risk-weighted assets", "amount"),
        ),
    ),
)

#: The IRRBB Standardised Framework, bound from a sealed ``irr_sf`` run.
#:
#: Every scenario figure is declared twice — the measure and its ``_prior``
#: twin — because the framework's own disclosure grid is a year-on-year table,
#: and a sentence that quotes this year's number without last year's is not the
#: disclosure the guideline asks for.
#:
#: ``assumption_defaults_applied`` and ``representative_parameters`` are COUNTS
#: on purpose. A modelling default applied to forty positions is a different
#: exposure from one applied to a single position, so the count travels with
#: the figure into the Pillar 2 register and into the report; the per-marker
#: breakdown rides the payload beside it and is never summed away.
_P5_SPECS: tuple[BlockSpec, ...] = (
    _spec(
        "irrbb_sf",
        "IRRBB standardised framework",
        "P5",
        source_kinds=("run",),
        facts=(
            _f("eve_risk_measure", "Economic value risk measure", "amount"),
            _f("eve_risk_measure_pct_tier1", "Risk measure against Tier 1", "ratio_pct"),
            _f("outlier_threshold_pct", "Supervisory outlier threshold", "ratio_pct"),
            _f("outlier", "Outlier test breached", "boolean"),
            _f("tier1", "Tier 1 capital", "amount"),
            _f("worst_scenario", "Worst scenario", "text"),
            _f("delta_eve_parallel_up", "Change in economic value — parallel up", "amount"),
            _f("delta_eve_parallel_down", "Change in economic value — parallel down", "amount"),
            _f("delta_eve_max", "Largest change in economic value", "amount"),
            _f("delta_nii_parallel_up", "Change in net interest income — parallel up", "amount"),
            _f(
                "delta_nii_parallel_down",
                "Change in net interest income — parallel down",
                "amount",
            ),
            _f("delta_nii_max", "Largest change in net interest income", "amount"),
            _f(
                "delta_eve_parallel_up_prior",
                "Change in economic value — parallel up, prior period",
                "amount",
            ),
            _f(
                "delta_eve_parallel_down_prior",
                "Change in economic value — parallel down, prior period",
                "amount",
            ),
            _f("delta_eve_max_prior", "Largest change in economic value, prior period", "amount"),
            _f(
                "delta_nii_parallel_up_prior",
                "Change in net interest income — parallel up, prior period",
                "amount",
            ),
            _f(
                "delta_nii_parallel_down_prior",
                "Change in net interest income — parallel down, prior period",
                "amount",
            ),
            _f(
                "delta_nii_max_prior",
                "Largest change in net interest income, prior period",
                "amount",
            ),
            _f("sf_mandatory", "Standardised framework required", "boolean"),
            _f("currencies_in_scope", "Currencies measured", "text"),
            _f(
                "nmd_avg_repricing_maturity_years",
                "Average repricing maturity of banking-book positions",
                "years",
            ),
            _f(
                "nmd_longest_repricing_maturity_years",
                "Longest repricing maturity of banking-book positions",
                "years",
            ),
            _f(
                "parameters_pending_confirmation",
                "Governed figures awaiting confirmation",
                "count",
            ),
            _f("representative_parameters", "Representative calibrations applied", "count"),
            _f("assumption_defaults_applied", "Modelling defaults applied", "count"),
        ),
    ),
)

#: Declared now so a framework JSON may cite them as evidence before the phase
#: that builds them ships. ``available`` is False for these in the API, and a
#: block of one of these types cannot be created yet.
_LATER_SPECS: tuple[BlockSpec, ...] = (
    _spec("funding_profile", "Funding profile", "P2", source_kinds=("run",)),
    _spec(
        "internal_models_inventory",
        "Internal models inventory",
        "P2",
        source_kinds=("computed",),
    ),
    _spec("workflow_summary", "Review and approval trail", "P3", source_kinds=("computed",)),
)

BLOCK_CATALOGUE: Mapping[str, BlockSpec] = MappingProxyType(
    {spec.type: spec for spec in (*_P1_SPECS, *_P2_SPECS, *_P5_SPECS, *_LATER_SPECS)}
)
BLOCK_TYPES: frozenset[str] = frozenset(BLOCK_CATALOGUE)
P1_BLOCK_TYPES: frozenset[str] = frozenset(spec.type for spec in _P1_SPECS)
P2_BLOCK_TYPES: frozenset[str] = frozenset(spec.type for spec in _P2_SPECS)
P5_BLOCK_TYPES: frozenset[str] = frozenset(spec.type for spec in _P5_SPECS)
#: Every type a cycle may actually create a block of today.
AVAILABLE_BLOCK_TYPES: frozenset[str] = P1_BLOCK_TYPES | P2_BLOCK_TYPES | P5_BLOCK_TYPES
MANUAL_BLOCK_TYPES: frozenset[str] = frozenset(
    spec.type for spec in (*_P1_SPECS, *_P2_SPECS, *_P5_SPECS) if spec.manual
)


class BlockStatus(StrEnum):
    """What the cycle's copy of a figure is worth right now."""

    UNBOUND = "unbound"
    FRESH = "fresh"
    STALE = "stale"
    AS_OF_MISMATCH = "as_of_mismatch"
    SOURCE_WITHDRAWN = "source_withdrawn"
    SOURCE_MISSING = "source_missing"
    PINNED = "pinned"


@dataclass(frozen=True)
class BindingSnapshot:
    """The part of the current binding staleness depends on."""

    seq: int
    source_key: str
    source_as_of: date | None
    manual: bool


@dataclass(frozen=True)
class SourceProbe:
    """What would bind if the block were refreshed now."""

    current_key: str | None
    withdrawn: bool = False
    reason: str | None = None


def evaluate_status(  # noqa: PLR0911 - one return per state reads better than a chain
    spec: BlockSpec,
    binding: BindingSnapshot | None,
    probe: SourceProbe,
    *,
    cycle_as_of: date,
    pinned_seq: int | None,
) -> BlockStatus:
    """The status of one block, from its binding and a probe of its source.

    Order matters. A withdrawn source beats a pin, because a pin says "I have
    reviewed the newer figures and chose the older ones", and nobody can choose
    figures whose inputs have been withdrawn.
    """
    if binding is None:
        return BlockStatus.UNBOUND
    if probe.withdrawn:
        return BlockStatus.SOURCE_WITHDRAWN
    if spec.manual or binding.manual:
        # A manual table is exactly what the preparer typed; it cannot go stale.
        # Its evidence attachment is a readiness rule, not a staleness rule.
        return BlockStatus.FRESH
    pinned = pinned_seq is not None and pinned_seq == binding.seq
    if spec.as_of_rule == "exact" and binding.source_as_of != cycle_as_of:
        return BlockStatus.PINNED if pinned else BlockStatus.AS_OF_MISMATCH
    if probe.current_key is None:
        return BlockStatus.SOURCE_MISSING
    if probe.current_key != binding.source_key:
        return BlockStatus.PINNED if pinned else BlockStatus.STALE
    return BlockStatus.FRESH


_FREEZING = frozenset(
    {
        BlockStatus.UNBOUND,
        BlockStatus.STALE,
        BlockStatus.AS_OF_MISMATCH,
        BlockStatus.SOURCE_WITHDRAWN,
        BlockStatus.SOURCE_MISSING,
    }
)


def blocks_freeze(status: BlockStatus) -> bool:
    """True when a referenced block in this state must stop a freeze."""
    return status in _FREEZING


__all__ = [
    "AVAILABLE_BLOCK_TYPES",
    "BLOCK_CATALOGUE",
    "BLOCK_TYPES",
    "MANUAL_BLOCK_TYPES",
    "P1_BLOCK_TYPES",
    "P2_BLOCK_TYPES",
    "P5_BLOCK_TYPES",
    "SOURCE_KINDS",
    "AsOfRule",
    "BindingSnapshot",
    "BlockPhase",
    "BlockSpec",
    "BlockStatus",
    "FactKind",
    "FactSpec",
    "SourceProbe",
    "blocks_freeze",
    "evaluate_status",
]
