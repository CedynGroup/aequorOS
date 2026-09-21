"""ICAAP risk & capital (M2): register, appetite, Pillar 2, reconciliation, review.

Where M1 (``app/models/icaap.py``) holds the REPORT, this holds the ASSESSMENT
behind it: which risks the bank judged material, what appetite the Board set,
what capital each Pillar 2 risk needs, how that reconciles to the regulatory
requirement and to available resources, what an independent reviewer found, and
what the Board challenged. The same three-tier discipline applies, and for the
same reason — an ICAAP is evidence, and evidence that can be edited after the
fact is not evidence:

* **mutable** — the register, appetite metrics, the Pillar 2 head rows,
  allocations, reconciliation lines and control explanations. These are working
  state; the frozen truth is the block bindings and P3's package snapshot, and
  the service locks them by cycle status.
* **UNALTERABLE** — Pillar 2 item revisions, challenges and challenge
  responses. Every Pillar 2 figure ever computed or saved is kept, because an
  approval pins a revision number and "what did the approver approve" must stay
  answerable. UPDATE is blocked by trigger; DELETE stays reachable so deleting
  a draft cycle still cascades.
* **SEALED** — supervisory add-ons once active, audit reviews once finalised.
  A supervisory add-on is the REGULATOR's number, confirmed by a second person
  (the database refuses ``confirmed_by = created_by``, so maker-checker holds
  even against a direct SQL write); a finalised independent review is corrected
  by a superseding review, never by an edit.

Supervisory add-ons are scoped to the BANK, not to a cycle: a letter imposing an
add-on outlives the ICAAP that first recorded it, and the next cycle must see
the same row rather than a copy that can drift. Everything else is cycle-scoped.

Every table carries ``organization_id`` and ``bank_id`` and is ENABLE+FORCE RLS
on the tenant (``202609190057``). Columns are ``sa.JSON``, never JSONB (D-014):
the hermetic suite and the Playwright stack build this schema with
``create_all`` on SQLite.

No regulatory number appears here (D-024). The vocabularies below are
structural — the names of methods, bases and outcomes — and every threshold the
engine applies is a governed parameter row.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV7PrimaryKeyMixin, utc_now

# --- vocabularies (structural; used by CHECKs, schemas and the domain) ------

#: How a Pillar 2 figure is expressed before it becomes an amount (B4, D-009).
#:
#: **Name collision, deliberate and spec-mandated.** ``app.models.icaap`` also
#: exports ``ICAAP_BASES``, meaning the REPORTING basis (solo / consolidated).
#: This one is the AMOUNT basis. Neither is re-exported from ``app.models``, so
#: an importer must name the module and cannot pick one up by accident;
#: ``tests/domain/policy/test_parameter_shapes.py`` pins the two apart.
ICAAP_BASES: tuple[str, ...] = (
    "pct_total_rwa",
    "pct_credit_rwa",
    "pct_pillar1_credit_capital",
    "absolute",
)
ICAAP_P2_SOURCES: tuple[str, ...] = (
    "icaap_method",
    "capital_plan",
    "stress_overlay",
    "supervisory",
    "judgemental",
)
#: Consolidated figures cannot be built from solo blocks (D-018), so the input
#: mode is recorded on the row rather than inferred.
ICAAP_P2_INPUT_MODES: tuple[str, ...] = ("bound_blocks", "manual_with_evidence")
ICAAP_P2_METHOD_STATUSES: tuple[str, ...] = (
    "not_computed",
    "computed",
    "interim_non_sf",
    "incomplete",
    "not_computable",
    "not_capitalised",
)
ICAAP_P2_DERIVATIONS: tuple[str, ...] = (
    "method",
    "adopted",
    "manual",
    "same_as_baseline",
    "same_as_stressed",
    "max_of_baseline_and_scenario",
    "not_assessed",
    # There is no derivation because there is no figure. Every method that
    # refuses from inside the domain answers with this (fx, irrbb, liquidity,
    # operational, sovereign and the granularity adjustment), so a stored
    # refusal reads the same wherever it came from. It was missing until the
    # first refusal was persisted, in P5-C's end-to-end path.
    "not_applicable",
)
#: Methods that describe a scenario and must therefore record its definition.
ICAAP_SCENARIO_METHODS: tuple[str, ...] = (
    "fx_nop_addon",
    "operational_scenario_net_p1",
    "sovereign_stress_addon",
    "irrbb_interim_delta_eve",
)
#: The one component whose amount is negative: an inter-risk diversification
#: benefit, which is judgemental and off by default (governed parameter).
ICAAP_DIVERSIFICATION_COMPONENT = "diversification_benefit"
ICAAP_RISK_TREATMENTS: tuple[str, ...] = (
    "undecided",
    "quantified",
    "fully_covered_by_pillar1",
    "not_capitalised",
    "not_material",
)
ICAAP_VERDICTS: tuple[str, ...] = ("unassessed", "material", "not_material")
ICAAP_VERDICT_SOURCES: tuple[str, ...] = ("matrix", "override")
ICAAP_PILLAR1_COVERAGE: tuple[str, ...] = ("full", "partial", "none")
ICAAP_APPETITE_UNITS: tuple[str, ...] = (
    "percent",
    "ratio",
    "amount",
    "count",
    "multiplier",
    "years",
)
ICAAP_MEASURE_KINDS: tuple[str, ...] = ("quantitative", "qualitative")
#: Same values as ``app.domain.policy.Direction``: a floor is breached downward,
#: a ceiling upward. The appetite/tolerance/capacity ordering flips with it.
ICAAP_DIRECTIONS: tuple[str, ...] = ("floor", "ceiling")
ICAAP_APPETITE_VALUE_SOURCES: tuple[str, ...] = ("block_fact", "manual")
ICAAP_RECON_GROUPS: tuple[str, ...] = (
    "pillar1",
    "pillar2",
    "buffer",
    "supervisory_unattributed",
    "diversification",
    "total",
)
ICAAP_CAPITAL_TIERS: tuple[str, ...] = ("cet1", "at1", "tier2", "deduction", "other")
ICAAP_RESOURCE_ORIGINS: tuple[str, ...] = ("regulatory_component", "manual")
ICAAP_ALLOCATION_UNIT_KINDS: tuple[str, ...] = ("business_line", "legal_entity", "risk_type")
ICAAP_ALLOCATION_DRIVERS: tuple[str, ...] = ("rwa_share", "exposure_share", "manual_pct")
ICAAP_REVIEW_KINDS: tuple[str, ...] = (
    "internal_audit",
    "external_audit",
    "independent_validation",
    "other_independent",
)
ICAAP_REVIEW_OPINIONS: tuple[str, ...] = (
    "satisfactory",
    "satisfactory_with_findings",
    "needs_improvement",
    "unsatisfactory",
)
ICAAP_REVIEW_STATUSES: tuple[str, ...] = ("draft", "finalised", "superseded")
#: Statuses at which the governed-row guard treats a review as authoritative.
ICAAP_REVIEW_SEAL_STATES: tuple[str, ...] = ("finalised", "superseded")
ICAAP_REVIEW_NEXT_STATES: tuple[str, ...] = ("superseded",)
ICAAP_CHALLENGE_FORUMS: tuple[str, ...] = (
    "board",
    "board_risk_committee",
    "board_audit_committee",
    "senior_management",
    "chief_risk_officer",
    "internal_audit",
    "other",
)
ICAAP_CHALLENGE_OUTCOMES: tuple[str, ...] = (
    "accepted_changed",
    "accepted_no_change",
    "rejected_with_rationale",
    "deferred",
)
ICAAP_CHALLENGE_TARGETS: tuple[str, ...] = (
    "cycle",
    "section",
    "risk",
    "appetite_metric",
    "pillar2_item",
    "reconciliation",
    "stress",
)
ICAAP_CHALLENGE_SEVERITIES: tuple[str, ...] = ("high", "medium", "low")
ICAAP_ADDON_STATUSES: tuple[str, ...] = ("draft", "active", "superseded", "withdrawn")
#: A supervisory add-on becomes authoritative when it is confirmed; from then on
#: only its supersession/withdrawal bookkeeping may move.
ICAAP_ADDON_SEAL_STATES: tuple[str, ...] = ("active", "superseded", "withdrawn")
ICAAP_ADDON_NEXT_STATES: tuple[str, ...] = ("superseded", "withdrawn")
ICAAP_ADDON_APPLIES_TO: tuple[str, ...] = ("solo", "consolidated", "both")
ICAAP_CONTROL_CODES: tuple[str, ...] = ("pillar2_source_consistency",)
ICAAP_REVISION_CHANGE_KINDS: tuple[str, ...] = (
    "created",
    "edited",
    "computed",
    "adopted",
    "retired",
)

#: Money: the engine's ``money()`` quantum is 0.0001.
_AMOUNT = Numeric(28, 4)
#: Percentages, ratios and basis values — the scale ``regulatory_parameter``
#: itself uses, so a governed value survives a round trip unchanged.
_RATIO = Numeric(18, 6)
#: Appetite thresholds may be amounts OR ratios, so they take the wider scale.
_APPETITE = Numeric(28, 6)


def _values(options: tuple[str, ...]) -> str:
    return ", ".join(f"'{option}'" for option in options)


_CYCLE_FK_COLUMNS = ["cycle_id", "organization_id", "bank_id"]
_CYCLE_FK_TARGETS = ["icaap_cycles.id", "icaap_cycles.organization_id", "icaap_cycles.bank_id"]
_ATTACHMENT_TARGETS = ["icaap_attachments.id", "icaap_attachments.organization_id"]
_ACTIVE = "retired_at IS NULL"


class IcaapRiskAssessment(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """One risk in the register: scored, rated, and judged material or not.

    Rows are created LAZILY on the first save — a GET merges the framework's
    categories with whatever has been stored — so a framework that gains a
    category never needs a data backfill of existing cycles (and a data step on
    a FORCE-RLS table would need a BYPASSRLS role to write at all).
    """

    __tablename__ = "icaap_risk_assessments"
    __table_args__ = (
        ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        UniqueConstraint("cycle_id", "risk_key", name="uq_icaap_risk_assessments_cycle_key"),
        UniqueConstraint("id", "organization_id", name="uq_icaap_risk_assessments_id_org"),
        CheckConstraint(
            "likelihood_score IS NULL OR likelihood_score >= 1",
            name="ck_icaap_risk_assessments_likelihood",
        ),
        CheckConstraint(
            "impact_score IS NULL OR impact_score >= 1", name="ck_icaap_risk_assessments_impact"
        ),
        CheckConstraint(
            f"matrix_verdict IN ({_values(ICAAP_VERDICTS)})",
            name="ck_icaap_risk_assessments_matrix_verdict",
        ),
        CheckConstraint(
            f"verdict IN ({_values(ICAAP_VERDICTS)})", name="ck_icaap_risk_assessments_verdict"
        ),
        CheckConstraint(
            f"verdict_source IN ({_values(ICAAP_VERDICT_SOURCES)})",
            name="ck_icaap_risk_assessments_verdict_source",
        ),
        # Overriding the matrix is allowed — the matrix is a tool, not the
        # judgement — but an unexplained override is not evidence of anything.
        CheckConstraint(
            "verdict_source <> 'override' OR override_reason IS NOT NULL",
            name="ck_icaap_risk_assessments_override_reason",
        ),
        CheckConstraint(
            f"pillar1_coverage IN ({_values(ICAAP_PILLAR1_COVERAGE)})",
            name="ck_icaap_risk_assessments_pillar1_coverage",
        ),
        CheckConstraint(
            f"pillar2_treatment IN ({_values(ICAAP_RISK_TREATMENTS)})",
            name="ck_icaap_risk_assessments_treatment",
        ),
        CheckConstraint(
            "pillar2_treatment <> 'fully_covered_by_pillar1' "
            "OR pillar1_coverage_rationale IS NOT NULL",
            name="ck_icaap_risk_assessments_coverage_rationale",
        ),
        CheckConstraint("row_rev >= 0", name="ck_icaap_risk_assessments_row_rev"),
        # A framework category cannot be retired away; only a bank's own
        # emerging-risk row can.
        CheckConstraint(
            "retired_at IS NULL OR is_custom", name="ck_icaap_risk_assessments_retire_custom"
        ),
        Index("ix_icaap_risk_assessments_org_cycle", "organization_id", "cycle_id"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    cycle_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    risk_key: Mapped[str] = mapped_column(String(80), nullable=False)
    category_key: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    is_custom: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sql_text("false"), nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    likelihood_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    impact_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    controls_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Derived likelihood x impact, stored so a snapshot and a query agree.
    materiality_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating_key: Mapped[str | None] = mapped_column(String(24), nullable=True)
    matrix_verdict: Mapped[str] = mapped_column(
        String(16), default="unassessed", server_default=sql_text("'unassessed'"), nullable=False
    )
    verdict: Mapped[str] = mapped_column(
        String(16), default="unassessed", server_default=sql_text("'unassessed'"), nullable=False
    )
    verdict_source: Mapped[str] = mapped_column(
        String(8), default="matrix", server_default=sql_text("'matrix'"), nullable=False
    )
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    materiality_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: sha256 of the governed materiality rows this verdict was reached under.
    #: When staff change a threshold in the console the digest stops matching
    #: and the row is shown as stale rather than silently re-judged.
    thresholds_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pillar1_coverage: Mapped[str] = mapped_column(String(8), nullable=False)
    p29_class: Mapped[str] = mapped_column(String(32), nullable=False)
    pillar2_treatment: Mapped[str] = mapped_column(
        String(24), default="undecided", server_default=sql_text("'undecided'"), nullable=False
    )
    pillar1_coverage_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_function: Mapped[str | None] = mapped_column(String(120), nullable=True)
    row_rev: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0"), nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    updated_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    retire_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class IcaapAppetiteMetric(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """One risk-appetite statement: the sentence, and where measured its limits.

    ¶76 asks for qualitative AND quantitative appetite, so both live in one
    table with a CHECK that a quantitative row carries its whole triple
    (appetite / tolerance / capacity) and a qualitative row carries none of it.
    The ordering CHECK is direction-aware because a floor and a ceiling order
    the triple in opposite directions; whether capacity is also inside the
    REGULATORY value is resolved at runtime against the governed row, so it
    cannot be a CHECK.
    """

    __tablename__ = "icaap_appetite_metrics"
    __table_args__ = (
        ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["manual_evidence_attachment_id", "organization_id"], _ATTACHMENT_TARGETS
        ),
        UniqueConstraint("id", "organization_id", name="uq_icaap_appetite_metrics_id_org"),
        CheckConstraint(
            f"measure_kind IN ({_values(ICAAP_MEASURE_KINDS)})",
            name="ck_icaap_appetite_metrics_measure_kind",
        ),
        CheckConstraint(
            f"unit IS NULL OR unit IN ({_values(ICAAP_APPETITE_UNITS)})",
            name="ck_icaap_appetite_metrics_unit",
        ),
        CheckConstraint(
            f"direction IS NULL OR direction IN ({_values(ICAAP_DIRECTIONS)})",
            name="ck_icaap_appetite_metrics_direction",
        ),
        CheckConstraint(
            f"value_source IS NULL OR value_source IN ({_values(ICAAP_APPETITE_VALUE_SOURCES)})",
            name="ck_icaap_appetite_metrics_value_source_vocab",
        ),
        CheckConstraint(
            "(measure_kind = 'qualitative' AND direction IS NULL AND appetite_value IS NULL "
            "AND tolerance_value IS NULL AND capacity_value IS NULL AND value_source IS NULL) "
            "OR (measure_kind = 'quantitative' AND direction IS NOT NULL AND unit IS NOT NULL "
            "AND appetite_value IS NOT NULL AND tolerance_value IS NOT NULL "
            "AND capacity_value IS NOT NULL AND value_source IS NOT NULL)",
            name="ck_icaap_appetite_metrics_measure",
        ),
        CheckConstraint(
            "measure_kind = 'qualitative' "
            "OR (direction = 'floor' AND appetite_value >= tolerance_value "
            "AND tolerance_value >= capacity_value) "
            "OR (direction = 'ceiling' AND appetite_value <= tolerance_value "
            "AND tolerance_value <= capacity_value)",
            name="ck_icaap_appetite_metrics_ordering",
        ),
        CheckConstraint(
            "value_source IS NULL "
            "OR (value_source = 'block_fact' AND source_block_type IS NOT NULL "
            "AND source_fact_key IS NOT NULL) "
            "OR (value_source = 'manual' AND manual_value IS NOT NULL)",
            name="ck_icaap_appetite_metrics_source",
        ),
        CheckConstraint("row_rev >= 0", name="ck_icaap_appetite_metrics_row_rev"),
        Index(
            "uq_icaap_appetite_metrics_active",
            "cycle_id",
            "metric_key",
            unique=True,
            postgresql_where=sql_text(_ACTIVE),
            sqlite_where=sql_text(_ACTIVE),
        ),
        Index("ix_icaap_appetite_metrics_org_cycle", "organization_id", "cycle_id"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    cycle_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    metric_key: Mapped[str] = mapped_column(String(60), nullable=False)
    #: null = institution-wide (capital adequacy), not tied to a register row.
    risk_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    is_custom: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sql_text("false"), nullable=False
    )
    measure_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    direction: Mapped[str | None] = mapped_column(String(8), nullable=True)
    appetite_value: Mapped[Decimal | None] = mapped_column(_APPETITE, nullable=True)
    tolerance_value: Mapped[Decimal | None] = mapped_column(_APPETITE, nullable=True)
    capacity_value: Mapped[Decimal | None] = mapped_column(_APPETITE, nullable=True)
    #: The governed code this metric is measured against (e.g. the CAR floor).
    regulatory_param_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The tenant's own board-register code, shown alongside as a warning only.
    board_register_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    value_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_block_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_fact_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manual_value: Mapped[Decimal | None] = mapped_column(_APPETITE, nullable=True)
    manual_evidence_attachment_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    prior_value: Mapped[Decimal | None] = mapped_column(_APPETITE, nullable=True)
    prior_value_label: Mapped[str | None] = mapped_column(String(80), nullable=True)
    #: The RAS sentence itself (¶76) — required whether or not it is measured.
    qualitative_statement: Mapped[str] = mapped_column(Text, nullable=False)
    board_approval_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    board_approved_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    row_rev: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0"), nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    updated_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    retire_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class IcaapPillar2Item(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """The live Pillar 2 figure for one component, and its approval stamp.

    The head row is a pointer: the figures themselves are kept as immutable
    revisions, and an approval names the revision number it approved. That is
    why ``current_revision_no``/``approved_revision_no`` are numbers and not
    foreign keys — a real FK between head and revisions would be circular.

    The canonical figure is ``baseline_amount`` in the reporting currency
    (D-009); ``basis``/``basis_value`` record how it was expressed so the Table 5
    grid can show the bank's own units without recomputing anything.
    """

    __tablename__ = "icaap_pillar2_items"
    __table_args__ = (
        ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        UniqueConstraint("id", "organization_id", name="uq_icaap_pillar2_items_id_org"),
        CheckConstraint(
            f"source IN ({_values(ICAAP_P2_SOURCES)})", name="ck_icaap_pillar2_items_source"
        ),
        CheckConstraint(
            f"input_mode IN ({_values(ICAAP_P2_INPUT_MODES)})",
            name="ck_icaap_pillar2_items_input_mode",
        ),
        CheckConstraint(
            f"method_status IN ({_values(ICAAP_P2_METHOD_STATUSES)})",
            name="ck_icaap_pillar2_items_method_status",
        ),
        CheckConstraint(
            f"basis IS NULL OR basis IN ({_values(ICAAP_BASES)})",
            name="ck_icaap_pillar2_items_basis",
        ),
        CheckConstraint(
            f"baseline_derivation IS NULL "
            f"OR baseline_derivation IN ({_values(ICAAP_P2_DERIVATIONS)})",
            name="ck_icaap_pillar2_items_baseline_derivation",
        ),
        CheckConstraint(
            f"stressed_derivation IS NULL "
            f"OR stressed_derivation IN ({_values(ICAAP_P2_DERIVATIONS)})",
            name="ck_icaap_pillar2_items_stressed_derivation",
        ),
        # Everything that carries capital must land on a Table 5 row, or the
        # grid and the register would not add up to the same number.
        CheckConstraint(
            "table5_row IS NOT NULL OR method = 'not_capitalised' "
            f"OR component_key = '{ICAAP_DIVERSIFICATION_COMPONENT}'",
            name="ck_icaap_pillar2_items_table5",
        ),
        CheckConstraint(
            "method_status <> 'not_capitalised' "
            "OR (baseline_amount IS NULL AND stressed_amount IS NULL)",
            name="ck_icaap_pillar2_items_not_capitalised",
        ),
        CheckConstraint(
            "method_status NOT IN ('computed', 'interim_non_sf') "
            "OR (baseline_amount IS NOT NULL AND basis IS NOT NULL)",
            name="ck_icaap_pillar2_items_computed_has_amount",
        ),
        CheckConstraint(
            f"component_key = '{ICAAP_DIVERSIFICATION_COMPONENT}' "
            "OR ((baseline_amount IS NULL OR baseline_amount >= 0) "
            "AND (stressed_amount IS NULL OR stressed_amount >= 0))",
            name="ck_icaap_pillar2_items_non_negative",
        ),
        CheckConstraint(
            f"component_key <> '{ICAAP_DIVERSIFICATION_COMPONENT}' "
            "OR ((baseline_amount IS NULL OR baseline_amount <= 0) "
            "AND source = 'judgemental')",
            name="ck_icaap_pillar2_items_diversification_negative",
        ),
        CheckConstraint(
            f"method NOT IN ({_values(ICAAP_SCENARIO_METHODS)}) "
            "OR method_status IN ('not_computed', 'not_computable') "
            "OR scenario_definition IS NOT NULL",
            name="ck_icaap_pillar2_items_scenario_required",
        ),
        CheckConstraint(
            "(approved_by IS NULL) = (approved_revision_no IS NULL) "
            "AND (approved_revision_no IS NULL "
            "OR approved_revision_no <= current_revision_no)",
            name="ck_icaap_pillar2_items_approval",
        ),
        CheckConstraint("current_revision_no >= 0", name="ck_icaap_pillar2_items_revision_no"),
        CheckConstraint(
            "inputs_digest IS NULL OR length(inputs_digest) = 64",
            name="ck_icaap_pillar2_items_inputs_digest",
        ),
        Index(
            "uq_icaap_pillar2_items_active",
            "cycle_id",
            "item_key",
            unique=True,
            postgresql_where=sql_text(_ACTIVE),
            sqlite_where=sql_text(_ACTIVE),
        ),
        Index("ix_icaap_pillar2_items_org_cycle", "organization_id", "cycle_id"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    cycle_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    item_key: Mapped[str] = mapped_column(String(80), nullable=False)
    risk_key: Mapped[str] = mapped_column(String(80), nullable=False)
    category_key: Mapped[str] = mapped_column(String(60), nullable=False)
    component_key: Mapped[str] = mapped_column(String(60), nullable=False)
    #: Copied from the framework component so a sealed cycle's grid keeps its
    #: layout even if a later framework version moves the row.
    table5_row: Mapped[str | None] = mapped_column(String(40), nullable=True)
    method: Mapped[str] = mapped_column(String(40), nullable=False)
    method_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    input_mode: Mapped[str] = mapped_column(
        String(24),
        default="bound_blocks",
        server_default=sql_text("'bound_blocks'"),
        nullable=False,
    )
    method_status: Mapped[str] = mapped_column(
        String(20),
        default="not_computed",
        server_default=sql_text("'not_computed'"),
        nullable=False,
    )
    status_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    basis: Mapped[str | None] = mapped_column(String(32), nullable=True)
    basis_value: Mapped[Decimal | None] = mapped_column(_RATIO, nullable=True)
    baseline_amount: Mapped[Decimal | None] = mapped_column(_AMOUNT, nullable=True)
    #: null = not assessed under stress, which is not the same as zero.
    stressed_amount: Mapped[Decimal | None] = mapped_column(_AMOUNT, nullable=True)
    baseline_derivation: Mapped[str | None] = mapped_column(String(32), nullable=True)
    stressed_derivation: Mapped[str | None] = mapped_column(String(32), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    inputs_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scenario_definition: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: "Material, but we hold no capital against it" needs saying out loud.
    zero_amount_justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_attachment_ids: Mapped[list[Any]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    evidence_reference: Mapped[str | None] = mapped_column(String(300), nullable=True)
    current_revision_no: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0"), nullable=False
    )
    approved_revision_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    updated_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    retire_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class IcaapPillar2ItemRevision(UuidV7PrimaryKeyMixin, Base):
    """Every state a Pillar 2 figure has ever been in. UNALTERABLE.

    An approval pins a revision number, so "what exactly did the approver
    approve, and from what inputs" must be re-readable as DATA, not inferred
    from an audit event. UPDATE is refused by the database.
    """

    __tablename__ = "icaap_pillar2_item_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["item_id", "organization_id"],
            ["icaap_pillar2_items.id", "icaap_pillar2_items.organization_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        UniqueConstraint("item_id", "revision_no", name="uq_icaap_pillar2_item_revisions_no"),
        UniqueConstraint("id", "organization_id", name="uq_icaap_pillar2_item_revisions_id_org"),
        CheckConstraint("revision_no >= 1", name="ck_icaap_pillar2_item_revisions_no"),
        CheckConstraint("round >= 1", name="ck_icaap_pillar2_item_revisions_round"),
        CheckConstraint(
            f"change_kind IN ({_values(ICAAP_REVISION_CHANGE_KINDS)})",
            name="ck_icaap_pillar2_item_revisions_change_kind",
        ),
        CheckConstraint("length(snapshot_sha256) = 64", name="ck_icaap_pillar2_item_revisions_sha"),
        CheckConstraint(
            "inputs_digest IS NULL OR length(inputs_digest) = 64",
            name="ck_icaap_pillar2_item_revisions_inputs_digest",
        ),
        Index(
            "ix_icaap_pillar2_item_revisions_org_cycle",
            "organization_id",
            "cycle_id",
        ),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    cycle_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    item_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    change_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    #: The review round this revision was written in (P3 maker-checker).
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    #: Method detail, block bindings with their payload digests, the canonical
    #: probe key and the governed rows consumed — the whole provenance.
    computation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    inputs_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class BankSupervisoryAddon(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """A capital add-on the regulator imposed by letter. SEALED once active.

    Bank-scoped, not cycle-scoped: the letter outlives the ICAAP that first
    recorded it, and next year's cycle must read the SAME row rather than a copy
    that can drift from it. Effective dating decides which add-ons a given
    as-of date sees.

    Two people are involved by construction: the database refuses
    ``confirmed_by = created_by``, so the maker-checker rule holds even against
    a direct SQL write, and an active row can only move to superseded or
    withdrawn. The letter itself is stored as an artifact with its digest.

    Never public: P1's ``supervisory_addons`` block is ``never_public``, and P3's
    disclosure refuses it. There is deliberately no flag here that could make it
    public.
    """

    __tablename__ = "bank_supervisory_addons"
    __table_args__ = (
        ForeignKeyConstraint(["bank_id", "organization_id"], ["banks.id", "banks.organization_id"]),
        ForeignKeyConstraint(
            ["supersedes_addon_id", "organization_id", "bank_id"],
            [
                "bank_supervisory_addons.id",
                "bank_supervisory_addons.organization_id",
                "bank_supervisory_addons.bank_id",
            ],
        ),
        UniqueConstraint("id", "organization_id", name="uq_bank_supervisory_addons_id_org"),
        UniqueConstraint(
            "id", "organization_id", "bank_id", name="uq_bank_supervisory_addons_id_org_bank"
        ),
        CheckConstraint(
            f"status IN ({_values(ICAAP_ADDON_STATUSES)})", name="ck_bank_supervisory_addons_status"
        ),
        CheckConstraint(
            f"applies_to_basis IN ({_values(ICAAP_ADDON_APPLIES_TO)})",
            name="ck_bank_supervisory_addons_applies_to",
        ),
        CheckConstraint(
            f"basis IN ({_values(ICAAP_BASES)})", name="ck_bank_supervisory_addons_basis"
        ),
        CheckConstraint("basis_value >= 0", name="ck_bank_supervisory_addons_basis_value"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_bank_supervisory_addons_effective",
        ),
        CheckConstraint(
            "status IN ('draft', 'withdrawn') OR confirmed_by IS NOT NULL",
            name="ck_bank_supervisory_addons_active_confirmed",
        ),
        CheckConstraint(
            "confirmed_by IS NULL OR confirmed_by <> created_by",
            name="ck_bank_supervisory_addons_four_eyes",
        ),
        CheckConstraint(
            "status <> 'withdrawn' OR (withdrawn_at IS NOT NULL AND withdrawal_reason IS NOT NULL)",
            name="ck_bank_supervisory_addons_withdrawn",
        ),
        CheckConstraint(
            "status <> 'superseded' OR superseded_at IS NOT NULL",
            name="ck_bank_supervisory_addons_superseded",
        ),
        CheckConstraint("letter_byte_size > 0", name="ck_bank_supervisory_addons_letter_size"),
        CheckConstraint("length(letter_sha256) = 64", name="ck_bank_supervisory_addons_letter_sha"),
        CheckConstraint(
            "letter_storage_tier = 'outputs'", name="ck_bank_supervisory_addons_letter_tier"
        ),
        Index("ix_bank_supervisory_addons_org_bank_status", "organization_id", "bank_id", "status"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(12), default="draft", server_default=sql_text("'draft'"), nullable=False
    )
    letter_reference: Mapped[str] = mapped_column(String(120), nullable=False)
    letter_date: Mapped[date] = mapped_column(Date, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    applies_to_basis: Mapped[str] = mapped_column(String(12), nullable=False)
    #: null = unattributed, i.e. imposed on the total rather than on one risk.
    table5_row: Mapped[str | None] = mapped_column(String(40), nullable=True)
    component_key: Mapped[str | None] = mapped_column(String(60), nullable=True)
    basis: Mapped[str] = mapped_column(String(32), nullable=False)
    basis_value: Mapped[Decimal] = mapped_column(_RATIO, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    letter_original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Sniffed from the bytes, never taken from the client's Content-Type.
    letter_media_type: Mapped[str] = mapped_column(String(120), nullable=False)
    letter_byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    letter_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    letter_storage_tier: Mapped[str] = mapped_column(String(16), nullable=False)
    letter_object_path: Mapped[str] = mapped_column(String(512), nullable=False)
    letter_storage_version_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    supersedes_addon_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    confirmed_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by_addon_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    withdrawn_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    withdrawal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class IcaapCapitalAllocation(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """How internal capital is pushed down to business lines or entities."""

    __tablename__ = "icaap_capital_allocations"
    __table_args__ = (
        ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        UniqueConstraint(
            "cycle_id", "unit_key", "risk_line_key", name="uq_icaap_capital_allocations_unit_line"
        ),
        UniqueConstraint("id", "organization_id", name="uq_icaap_capital_allocations_id_org"),
        CheckConstraint(
            f"unit_kind IN ({_values(ICAAP_ALLOCATION_UNIT_KINDS)})",
            name="ck_icaap_capital_allocations_unit_kind",
        ),
        CheckConstraint(
            f"driver_kind IN ({_values(ICAAP_ALLOCATION_DRIVERS)})",
            name="ck_icaap_capital_allocations_driver_kind",
        ),
        CheckConstraint("driver_value >= 0", name="ck_icaap_capital_allocations_driver_value"),
        CheckConstraint(
            "length(allocation_digest) = 64", name="ck_icaap_capital_allocations_digest"
        ),
        Index("ix_icaap_capital_allocations_org_cycle", "organization_id", "cycle_id"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    cycle_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    unit_key: Mapped[str] = mapped_column(String(60), nullable=False)
    unit_label: Mapped[str] = mapped_column(String(200), nullable=False)
    unit_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    #: A requirement-reconciliation line key, so an allocation always points at
    #: a figure that exists rather than at a name someone typed.
    risk_line_key: Mapped[str] = mapped_column(String(80), nullable=False)
    driver_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    driver_value: Mapped[Decimal] = mapped_column(_APPETITE, nullable=False)
    allocated_amount: Mapped[Decimal | None] = mapped_column(_AMOUNT, nullable=True)
    #: Drivers + the requirement snapshot they were allocated from; the PUT
    #: carries it back as an optimistic token.
    allocation_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    updated_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)


class IcaapRequirementReconciliationLine(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """¶49(l): internal capital requirement against the regulatory one, line by line.

    Recomputed as a whole (every line of one compute carries the same
    ``computed_digest``), but explanations are CARRIED across a recompute and
    marked stale when the values they were written against change — an
    explanation silently attached to different numbers would be worse than none.
    """

    __tablename__ = "icaap_requirement_reconciliation_lines"
    __table_args__ = (
        ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        UniqueConstraint(
            "cycle_id", "line_key", name="uq_icaap_requirement_reconciliation_lines_key"
        ),
        UniqueConstraint(
            "id", "organization_id", name="uq_icaap_requirement_reconciliation_lines_id_org"
        ),
        CheckConstraint(
            f"line_group IN ({_values(ICAAP_RECON_GROUPS)})",
            name="ck_icaap_requirement_reconciliation_lines_group",
        ),
        CheckConstraint("position >= 1", name="ck_icaap_requirement_reconciliation_lines_position"),
        CheckConstraint(
            "length(computed_digest) = 64", name="ck_icaap_requirement_reconciliation_lines_digest"
        ),
        Index("ix_icaap_requirement_reconciliation_lines_org_cycle", "organization_id", "cycle_id"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    cycle_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    line_key: Mapped[str] = mapped_column(String(80), nullable=False)
    line_group: Mapped[str] = mapped_column(String(24), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    table5_row: Mapped[str | None] = mapped_column(String(40), nullable=True)
    pillar1_amount: Mapped[Decimal | None] = mapped_column(_AMOUNT, nullable=True)
    pillar2_amount: Mapped[Decimal | None] = mapped_column(_AMOUNT, nullable=True)
    internal_amount: Mapped[Decimal | None] = mapped_column(_AMOUNT, nullable=True)
    regulatory_amount: Mapped[Decimal | None] = mapped_column(_AMOUNT, nullable=True)
    supervisory_amount: Mapped[Decimal | None] = mapped_column(_AMOUNT, nullable=True)
    difference: Mapped[Decimal | None] = mapped_column(_AMOUNT, nullable=True)
    explanation_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    explanation_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    explanation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    explanation_values_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    computed_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The governed values, bindings and item revisions this compute consumed.
    computation: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    computed_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IcaapResourcesReconciliationLine(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """REG-ICAAP-027: internal capital resources against regulatory capital.

    A component counted internally at a different amount from the regulatory
    one, or counted at all while regulatory-ineligible, must be explained — and
    that is a CHECK, not a service rule, because it is the whole point of the
    statement.
    """

    __tablename__ = "icaap_resources_reconciliation_lines"
    __table_args__ = (
        ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        ForeignKeyConstraint(["evidence_attachment_id", "organization_id"], _ATTACHMENT_TARGETS),
        UniqueConstraint(
            "cycle_id", "line_key", name="uq_icaap_resources_reconciliation_lines_key"
        ),
        UniqueConstraint(
            "id", "organization_id", name="uq_icaap_resources_reconciliation_lines_id_org"
        ),
        CheckConstraint(
            f"tier IN ({_values(ICAAP_CAPITAL_TIERS)})",
            name="ck_icaap_resources_reconciliation_lines_tier",
        ),
        CheckConstraint(
            f"origin IN ({_values(ICAAP_RESOURCE_ORIGINS)})",
            name="ck_icaap_resources_reconciliation_lines_origin",
        ),
        CheckConstraint("position >= 1", name="ck_icaap_resources_reconciliation_lines_position"),
        CheckConstraint(
            "(regulatory_eligible AND regulatory_amount IS NOT NULL "
            "AND internal_amount = regulatory_amount) OR explanation IS NOT NULL",
            name="ck_icaap_resources_reconciliation_lines_explained",
        ),
        CheckConstraint("row_rev >= 0", name="ck_icaap_resources_reconciliation_lines_row_rev"),
        Index("ix_icaap_resources_reconciliation_lines_org_cycle", "organization_id", "cycle_id"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    cycle_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    line_key: Mapped[str] = mapped_column(String(80), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    tier: Mapped[str] = mapped_column(String(16), nullable=False)
    origin: Mapped[str] = mapped_column(String(24), nullable=False)
    regulatory_component_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    regulatory_amount: Mapped[Decimal | None] = mapped_column(_AMOUNT, nullable=True)
    internal_amount: Mapped[Decimal] = mapped_column(_AMOUNT, nullable=False)
    regulatory_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_attachment_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    #: `{block_id, seq, payload_sha256}` when the line came from a capital run.
    source_binding_ref: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    row_rev: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0"), nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    updated_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)


class IcaapControlExplanation(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """Why an internal consistency control fires and is nonetheless accepted.

    Separate from ¶49(l) reconciliation on purpose (audit M1): the Pillar 2
    source-consistency control compares figures that are computed live, and its
    explanations must not be mistaken for the regulatory reconciliation's.
    """

    __tablename__ = "icaap_control_explanations"
    __table_args__ = (
        ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        UniqueConstraint(
            "cycle_id",
            "control_code",
            "comparison_key",
            name="uq_icaap_control_explanations_comparison",
        ),
        UniqueConstraint("id", "organization_id", name="uq_icaap_control_explanations_id_org"),
        CheckConstraint(
            f"control_code IN ({_values(ICAAP_CONTROL_CODES)})",
            name="ck_icaap_control_explanations_code",
        ),
        CheckConstraint("length(values_digest) = 64", name="ck_icaap_control_explanations_digest"),
        Index("ix_icaap_control_explanations_org_cycle", "organization_id", "cycle_id"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    cycle_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    control_code: Mapped[str] = mapped_column(String(40), nullable=False)
    comparison_key: Mapped[str] = mapped_column(String(120), nullable=False)
    values_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    explained_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    explained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IcaapAuditReview(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """¶49(n): the independent review of the ICAAP. SEALED once finalised.

    A finalised review is a reviewer's opinion on a date. It is corrected by
    recording a SUPERSEDING review, never by editing this one — which is why the
    guard admits only ``finalised -> superseded`` and the write-once supersession
    fields after the seal.

    Independence is enforced by the service (a preparer of this cycle cannot
    record its review), because who prepared what is a question about other
    tables and cannot be a CHECK here.
    """

    __tablename__ = "icaap_audit_reviews"
    __table_args__ = (
        ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["reviewed_cycle_id", "organization_id", "bank_id"], _CYCLE_FK_TARGETS
        ),
        ForeignKeyConstraint(["report_attachment_id", "organization_id"], _ATTACHMENT_TARGETS),
        ForeignKeyConstraint(
            ["supersedes_review_id", "organization_id"],
            ["icaap_audit_reviews.id", "icaap_audit_reviews.organization_id"],
        ),
        UniqueConstraint("id", "organization_id", name="uq_icaap_audit_reviews_id_org"),
        CheckConstraint(
            f"status IN ({_values(ICAAP_REVIEW_STATUSES)})", name="ck_icaap_audit_reviews_status"
        ),
        CheckConstraint(
            f"review_kind IN ({_values(ICAAP_REVIEW_KINDS)})", name="ck_icaap_audit_reviews_kind"
        ),
        CheckConstraint(
            f"overall_opinion IN ({_values(ICAAP_REVIEW_OPINIONS)})",
            name="ck_icaap_audit_reviews_opinion",
        ),
        CheckConstraint(
            "status = 'draft' OR finalised_at IS NOT NULL",
            name="ck_icaap_audit_reviews_finalised",
        ),
        CheckConstraint(
            "performed_from IS NULL OR performed_from <= performed_on",
            name="ck_icaap_audit_reviews_performed",
        ),
        CheckConstraint("row_rev >= 0", name="ck_icaap_audit_reviews_row_rev"),
        Index("ix_icaap_audit_reviews_org_cycle", "organization_id", "cycle_id"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    cycle_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(12), default="draft", server_default=sql_text("'draft'"), nullable=False
    )
    review_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    reviewer_function: Mapped[str] = mapped_column(String(120), nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    frequency_statement: Mapped[str] = mapped_column(Text, nullable=False)
    performed_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: Completion date — what the ¶42 staleness warning measures from.
    performed_on: Mapped[date] = mapped_column(Date, nullable=False)
    period_covered: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The cycle the review examined, when that is not the one recording it.
    reviewed_cycle_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    overall_opinion: Mapped[str] = mapped_column(String(32), nullable=False)
    findings: Mapped[list[Any]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    report_attachment_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    independence_statement: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    finalised_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finalised_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    supersedes_review_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by_review_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    row_rev: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0"), nullable=False
    )


class IcaapChallenge(UuidV7PrimaryKeyMixin, Base):
    """¶45: a challenge the Board or a committee put to the ICAAP. UNALTERABLE.

    ``raised_by_name`` is a NAME, not a user id: Board members need not be
    platform users, and the record is what the minutes say.
    """

    __tablename__ = "icaap_challenges"
    __table_args__ = (
        ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        ForeignKeyConstraint(["minutes_attachment_id", "organization_id"], _ATTACHMENT_TARGETS),
        UniqueConstraint("cycle_id", "challenge_no", name="uq_icaap_challenges_no"),
        UniqueConstraint("id", "organization_id", name="uq_icaap_challenges_id_org"),
        CheckConstraint("challenge_no >= 1", name="ck_icaap_challenges_no"),
        CheckConstraint("round >= 1", name="ck_icaap_challenges_round"),
        CheckConstraint(
            f"raised_in IN ({_values(ICAAP_CHALLENGE_FORUMS)})", name="ck_icaap_challenges_forum"
        ),
        CheckConstraint(
            f"target_kind IN ({_values(ICAAP_CHALLENGE_TARGETS)})",
            name="ck_icaap_challenges_target_kind",
        ),
        CheckConstraint(
            f"severity IN ({_values(ICAAP_CHALLENGE_SEVERITIES)})",
            name="ck_icaap_challenges_severity",
        ),
        Index("ix_icaap_challenges_org_cycle", "organization_id", "cycle_id"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    cycle_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    challenge_no: Mapped[int] = mapped_column(Integer, nullable=False)
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    raised_in: Mapped[str] = mapped_column(String(32), nullable=False)
    raised_by_name: Mapped[str] = mapped_column(String(200), nullable=False)
    raised_on: Mapped[date] = mapped_column(Date, nullable=False)
    meeting_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    target_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    target_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    challenge_text: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    minutes_attachment_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    recorded_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class IcaapChallengeResponse(UuidV7PrimaryKeyMixin, Base):
    """¶45: what management did about a challenge. UNALTERABLE.

    A challenge stays OPEN while it has no response, or while its latest
    response is ``deferred`` — so "we answered it" and "we put it off" cannot
    be confused.
    """

    __tablename__ = "icaap_challenge_responses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["challenge_id", "organization_id"],
            ["icaap_challenges.id", "icaap_challenges.organization_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        UniqueConstraint("challenge_id", "response_no", name="uq_icaap_challenge_responses_no"),
        UniqueConstraint("id", "organization_id", name="uq_icaap_challenge_responses_id_org"),
        CheckConstraint("response_no >= 1", name="ck_icaap_challenge_responses_no"),
        CheckConstraint(
            f"outcome IN ({_values(ICAAP_CHALLENGE_OUTCOMES)})",
            name="ck_icaap_challenge_responses_outcome",
        ),
        Index("ix_icaap_challenge_responses_org_cycle", "organization_id", "cycle_id"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    cycle_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    challenge_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    response_no: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    response_text: Mapped[str] = mapped_column(Text, nullable=False)
    #: What actually changed: section versions, item revisions, attachments.
    change_references: Mapped[list[Any]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    responder_function: Mapped[str] = mapped_column(String(120), nullable=False)
    responded_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


__all__ = [
    "ICAAP_ADDON_APPLIES_TO",
    "ICAAP_ADDON_NEXT_STATES",
    "ICAAP_ADDON_SEAL_STATES",
    "ICAAP_ADDON_STATUSES",
    "ICAAP_ALLOCATION_DRIVERS",
    "ICAAP_ALLOCATION_UNIT_KINDS",
    "ICAAP_APPETITE_UNITS",
    "ICAAP_APPETITE_VALUE_SOURCES",
    "ICAAP_BASES",
    "ICAAP_CAPITAL_TIERS",
    "ICAAP_CHALLENGE_FORUMS",
    "ICAAP_CHALLENGE_OUTCOMES",
    "ICAAP_CHALLENGE_SEVERITIES",
    "ICAAP_CHALLENGE_TARGETS",
    "ICAAP_CONTROL_CODES",
    "ICAAP_DIRECTIONS",
    "ICAAP_DIVERSIFICATION_COMPONENT",
    "ICAAP_MEASURE_KINDS",
    "ICAAP_P2_DERIVATIONS",
    "ICAAP_P2_INPUT_MODES",
    "ICAAP_P2_METHOD_STATUSES",
    "ICAAP_P2_SOURCES",
    "ICAAP_PILLAR1_COVERAGE",
    "ICAAP_RECON_GROUPS",
    "ICAAP_RESOURCE_ORIGINS",
    "ICAAP_REVIEW_KINDS",
    "ICAAP_REVIEW_NEXT_STATES",
    "ICAAP_REVIEW_OPINIONS",
    "ICAAP_REVIEW_SEAL_STATES",
    "ICAAP_REVIEW_STATUSES",
    "ICAAP_REVISION_CHANGE_KINDS",
    "ICAAP_RISK_TREATMENTS",
    "ICAAP_SCENARIO_METHODS",
    "ICAAP_VERDICT_SOURCES",
    "ICAAP_VERDICTS",
    "BankSupervisoryAddon",
    "IcaapAppetiteMetric",
    "IcaapAuditReview",
    "IcaapCapitalAllocation",
    "IcaapChallenge",
    "IcaapChallengeResponse",
    "IcaapControlExplanation",
    "IcaapPillar2Item",
    "IcaapPillar2ItemRevision",
    "IcaapRequirementReconciliationLine",
    "IcaapResourcesReconciliationLine",
    "IcaapRiskAssessment",
]
