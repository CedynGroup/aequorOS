"""Governed macro-scenario spine (docs/stress.md §3.1, Phase 1).

The authoritative, governed, macro-variable-driven scenario model the BoG
Guideline on Stress Testing (2026) requires: scenarios "built on macroeconomic
variables in a consistent manner" (¶34, ¶38(e), AppIII Table 6) rather than the
per-module direct-parameter overrides the platform carried before. It replaces
neither store in this phase — the frozen ``ParamStressShock`` register and the
quarantined ``StressScenario`` sandbox both remain — but it becomes the
first-class contract every later stress phase consumes:

- ``MacroScenario`` — one governed scenario. Tenant-scoped (``organization_id``,
  RLS-forced) and normally bank-scoped (``bank_id`` set), OR global-supervisory
  (``bank_id`` NULL — a BoG-provided scenario applied across the tenant, the
  bottom-up-supervisory path of ¶13). Maker-checker lifecycle:
  draft → pending_approval → approved (→ archived). Only an APPROVED scenario may
  be consumed by an official run (the Phase 2 orchestrator enforces this via the
  service guard); drafts are freely editable.
- ``MacroScenarioPath`` — the Appendix III Table 6 macro-variable paths that
  define a scenario: for each ``(variable, year_index)`` a ``base_value`` and a
  ``stress_value``. The translation layer (``app/domain/stress/translation.py``)
  turns the stress-vs-base deltas into the exact per-module shock vocabulary the
  engines consume.

Rates are decimal fractions, per the repo convention (a 28% policy rate is
``0.28``, 5% GDP growth is ``0.05``); FX and index variables are levels
(``fx_usd_ghs`` = GHS per USD, ``gse_index`` = index points).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV4PrimaryKeyMixin

# --- Vocabularies (the governed value sets; also the DB CHECK constraints) ---

#: Appendix III Table 6 macro drivers + the AppIII narrative risk drivers. This
#: is the authored INPUT vocabulary — the translation layer maps deltas of these
#: onto the per-module shock keys the engines consume. Rates are fractions.
MACRO_VARIABLES: tuple[str, ...] = (
    "gog_yield",  # Government-of-Ghana securities yield (fraction)
    "gdp_growth",  # Real GDP growth (fraction, YoY)
    "policy_rate",  # BoG Monetary Policy Rate (fraction)
    "interest_rate",  # Representative market/lending rate (fraction)
    "inflation",  # Headline CPI inflation (fraction, YoY)
    "unemployment",  # Unemployment rate (fraction)
    "fx_usd_ghs",  # USD→GHS spot (level, GHS per USD)
    "fx_gbp_ghs",  # GBP→GHS spot (level)
    "fx_eur_ghs",  # EUR→GHS spot (level)
    "gse_index",  # Ghana Stock Exchange Composite Index (level)
    "fiscal_deficit",  # Fiscal deficit as a fraction of GDP (fraction, signed)
    "cocoa_price",  # Cocoa price (level, USD/tonne)
    "gold_price",  # Gold price (level, USD/oz)
)

#: ¶5–6 scenario taxonomy.
SCENARIO_TYPES: tuple[str, ...] = (
    "base",
    "adverse",
    "historical",
    "hypothetical",
    "reverse",
    "supervisory",
)

#: Severity ladder (¶36 "various degrees of severity"). NULL for a base case.
SEVERITIES: tuple[str, ...] = ("mild", "moderate", "severe")

#: Maker-checker governance lifecycle (¶16 scenario approval).
SCENARIO_STATUSES: tuple[str, ...] = ("draft", "pending_approval", "approved", "archived")

# --- Management-actions plan vocabularies (docs/stress.md §3.7, Phase 3) ------

#: Credible management-action kinds (¶79, AppII Table 1).
ACTION_KINDS: tuple[str, ...] = (
    "raise_capital",
    "revise_dividend",
    "reduce_risk",
    "sell_assets",
    "change_strategy",
    "other",
)
#: The CRD tier a capital raise lands in (AppII Table 2 split).
ACTION_CAPITAL_TIERS: tuple[str, ...] = ("cet1", "at1", "tier2")
#: How a capital raise is sized: an authored amount, or sized to the residual gap.
ACTION_SIZING_MODES: tuple[str, ...] = ("fixed", "fill_residual")
#: When an action fires (¶80 triggered actions).
ACTION_TRIGGER_KINDS: tuple[str, ...] = ("always", "on_breach", "on_severity")
#: The maker-checker lifecycle a management-action plan shares with scenarios.
PLAN_STATUSES: tuple[str, ...] = ("draft", "pending_approval", "approved", "archived")

# --- Enterprise-stress sign-off / Board attestation (docs/stress.md §3.8, Phase 5) ---

#: The governance lifecycle of a stress-run sign-off (¶20, ¶57–63): an analyst /
#: CRO prepares the narrative + rationale (draft), submits it for Board review
#: (pending_attestation), and the Board attests framework + results with a
#: credibility rationale (attested). ``withdrawn`` retires a superseded sign-off.
STRESS_SIGNOFF_STATUSES: tuple[str, ...] = (
    "draft",
    "pending_attestation",
    "attested",
    "withdrawn",
)


def _in_clause(values: tuple[str, ...]) -> str:
    # "('a', 'b', ...)" — a valid SQL IN list; mirrors scenario_workbench.
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


class MacroScenario(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "macro_scenarios"
    __table_args__ = (
        CheckConstraint(
            f"scenario_type IN {_in_clause(SCENARIO_TYPES)}",
            name="ck_macro_scenarios_scenario_type",
        ),
        CheckConstraint(
            f"severity IS NULL OR severity IN {_in_clause(SEVERITIES)}",
            name="ck_macro_scenarios_severity",
        ),
        CheckConstraint(
            f"status IN {_in_clause(SCENARIO_STATUSES)}",
            name="ck_macro_scenarios_status",
        ),
        CheckConstraint("horizon_years >= 1", name="ck_macro_scenarios_horizon_years"),
        CheckConstraint("version >= 1", name="ck_macro_scenarios_version"),
        # One logical scenario per code within a bank scope. bank_id NULL
        # (supervisory) rows are NOT de-duplicated by this constraint (SQL NULLs
        # never compare equal); the service enforces code-uniqueness for those.
        UniqueConstraint(
            "organization_id", "bank_id", "code", name="uq_macro_scenarios_scope"
        ),
        Index("ix_macro_scenarios_org_bank_status", "organization_id", "bank_id", "status"),
        Index("ix_macro_scenarios_org_type", "organization_id", "scenario_type"),
    )

    organization_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("organizations.id"), nullable=False
    )
    # NULL = global-supervisory (org-wide, bank-agnostic — the bottom-up
    # supervisory path, ¶13). Set = bank-specific scenario.
    bank_id: Mapped[str | None] = mapped_column(
        String(16), ForeignKey("banks.id"), nullable=True
    )
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    scenario_type: Mapped[str] = mapped_column(String(20), nullable=False)
    severity: Mapped[str | None] = mapped_column(String(20), nullable=True)
    horizon_years: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    narrative: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    # Governance revision counter: 1 for the initial draft, incremented on each
    # approval so an approved scenario's version identifies its approved
    # generation (forward-compatible with a Phase 5 re-open → re-approve cycle).
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    # Set on approval; the maker-checker rule (approver != creator) is enforced
    # in the service, so these two user ids are never equal on an approved row.
    approved_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    approval_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Forward hook (documented, unused in Phase 1): a JSON list of
    # institution_type / institution_class codes this scenario applies to, for
    # the later SDI scoping (docs/sdi.md §7). NULL = applies to all types.
    institution_type_applicability: Mapped[list[Any] | None] = mapped_column(
        JSON, nullable=True
    )


class MacroScenarioPath(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "macro_scenario_paths"
    __table_args__ = (
        CheckConstraint(
            f"variable IN {_in_clause(MACRO_VARIABLES)}",
            name="ck_macro_scenario_paths_variable",
        ),
        CheckConstraint("year_index >= 0", name="ck_macro_scenario_paths_year_index"),
        UniqueConstraint(
            "scenario_id", "variable", "year_index", name="uq_macro_scenario_paths_point"
        ),
        Index("ix_macro_scenario_paths_scenario", "organization_id", "scenario_id"),
    )

    # Carried for RLS (the child table is FORCE-RLS by organization_id too) and
    # for the tenant-scoped index; the authoritative scope is the parent FK.
    organization_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("organizations.id"), nullable=False
    )
    scenario_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("macro_scenarios.id", ondelete="CASCADE"),
        nullable=False,
    )
    variable: Mapped[str] = mapped_column(String(40), nullable=False)
    # 0 = current/as-of year, 1..horizon_years = projection years.
    year_index: Mapped[int] = mapped_column(Integer, nullable=False)
    base_value: Mapped[Decimal] = mapped_column(Numeric(28, 6), nullable=False)
    stress_value: Mapped[Decimal] = mapped_column(Numeric(28, 6), nullable=False)


class ManagementActionPlan(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """A governed, documented management-actions plan (docs/stress.md §3.7, ¶78–81).

    The plan library the directive's "documented, credible, triggered" management
    actions require. Governed with the SAME maker-checker lifecycle as
    ``MacroScenario`` (draft → pending_approval → approved → archived); only an
    APPROVED plan may be consumed by an official enterprise-stress run. Tenant-
    scoped (RLS on ``organization_id``); ``bank_id`` NULL = an org-wide default
    plan, set = bank-specific.
    """

    __tablename__ = "management_action_plans"
    __table_args__ = (
        CheckConstraint(
            f"status IN {_in_clause(PLAN_STATUSES)}", name="ck_mgmt_action_plans_status"
        ),
        CheckConstraint("version >= 1", name="ck_mgmt_action_plans_version"),
        UniqueConstraint(
            "organization_id", "bank_id", "code", name="uq_mgmt_action_plans_scope"
        ),
        Index("ix_mgmt_action_plans_org_bank_status", "organization_id", "bank_id", "status"),
    )

    organization_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("organizations.id"), nullable=False
    )
    bank_id: Mapped[str | None] = mapped_column(
        String(16), ForeignKey("banks.id"), nullable=True
    )
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    approved_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    approval_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ManagementActionItem(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """One credible action in a plan (docs/stress.md §3.7).

    Mirrors the pure ``app.domain.stress.management_actions.ManagementAction``
    contract one-for-one so the service converts a row to a domain action with no
    inference. Rates are percentages; GHS amounts are absolute (Numeric(28,4)).
    """

    __tablename__ = "management_action_items"
    __table_args__ = (
        CheckConstraint(f"kind IN {_in_clause(ACTION_KINDS)}", name="ck_mgmt_action_items_kind"),
        CheckConstraint(
            f"capital_raise_tier IN {_in_clause(ACTION_CAPITAL_TIERS)}",
            name="ck_mgmt_action_items_tier",
        ),
        CheckConstraint(
            f"sizing IN {_in_clause(ACTION_SIZING_MODES)}", name="ck_mgmt_action_items_sizing"
        ),
        CheckConstraint(
            f"trigger_kind IN {_in_clause(ACTION_TRIGGER_KINDS)}",
            name="ck_mgmt_action_items_trigger_kind",
        ),
        CheckConstraint("effective_year >= 1", name="ck_mgmt_action_items_effective_year"),
        UniqueConstraint("plan_id", "action_id", name="uq_mgmt_action_items_action"),
        Index("ix_mgmt_action_items_plan", "organization_id", "plan_id"),
    )

    organization_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("organizations.id"), nullable=False
    )
    plan_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("management_action_plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    action_id: Mapped[str] = mapped_column(String(60), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    # Ordering within the plan (deterministic evaluation / display).
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Trigger (¶80).
    trigger_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    watch_minima: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    min_severity: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Timeline (¶79).
    effective_year: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # Capital raise.
    capital_raise_ghs: Mapped[Decimal] = mapped_column(
        Numeric(28, 4), default=Decimal("0"), nullable=False
    )
    capital_raise_tier: Mapped[str] = mapped_column(String(10), default="cet1", nullable=False)
    counts_as_paid_up: Mapped[bool] = mapped_column(default=False, nullable=False)
    sizing: Mapped[str] = mapped_column(String(20), default="fixed", nullable=False)
    # Dividend / distribution reduction.
    dividend_reduction_pct: Mapped[Decimal] = mapped_column(
        Numeric(9, 4), default=Decimal("0"), nullable=False
    )
    # RWA relief.
    rwa_reduction_ghs: Mapped[Decimal] = mapped_column(
        Numeric(28, 4), default=Decimal("0"), nullable=False
    )
    shrinks_leverage_exposure: Mapped[bool] = mapped_column(default=False, nullable=False)
    # ¶81 severity differentiation ({severity: factor}); NULL = engine default.
    severity_factors: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)


class EnterpriseStressSignoff(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """Stress-run governance / Board attestation (docs/stress.md §3.8, Phase 5).

    The directive's governance spine for stress testing (Part II ¶10–29, ¶57–63):
    the Board holds ultimate responsibility and must **attest that it has reviewed
    and challenged both the framework and the results, with a rationale for their
    credibility** (¶20). This row binds ONE immutable enterprise-stress
    ``RegulatoryRun`` to that governance record and carries the analyst/CRO
    narrative & assumptions rationale the annual submission requires (¶67(b)(c)).

    Maker-checker, mirroring ``MacroScenario``: an analyst prepares the narrative
    + rationale (``draft``), submits it (``pending_attestation``), and a DIFFERENT
    Board/approver officer attests (``attested``) with a credibility rationale.
    Only an ``attested`` sign-off makes the run eligible to feed the ICAAP
    Appendix II submission (the reporting generator enforces this). Tenant-scoped
    (RLS on ``organization_id``); the ``(run_id, organization_id, bank_id)``
    composite FK ties the sign-off to a run within the same tenant + bank.
    """

    __tablename__ = "enterprise_stress_signoffs"
    __table_args__ = (
        CheckConstraint(
            f"status IN {_in_clause(STRESS_SIGNOFF_STATUSES)}",
            name="ck_enterprise_stress_signoffs_status",
        ),
        CheckConstraint("version >= 1", name="ck_enterprise_stress_signoffs_version"),
        ForeignKeyConstraint(
            ["run_id", "organization_id", "bank_id"],
            [
                "regulatory_runs.id",
                "regulatory_runs.organization_id",
                "regulatory_runs.bank_id",
            ],
        ),
        # Exactly one sign-off per run (a withdrawn sign-off stays the row for
        # that run; a fresh run gets a fresh sign-off). The service refuses a
        # duplicate before this constraint would fire.
        UniqueConstraint(
            "organization_id", "run_id", name="uq_enterprise_stress_signoffs_run"
        ),
        Index(
            "ix_enterprise_stress_signoffs_scope",
            "organization_id",
            "bank_id",
            "reporting_period_id",
            "status",
        ),
    )

    organization_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("organizations.id"), nullable=False
    )
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    # The enterprise-stress RegulatoryRun this sign-off governs.
    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    # Denormalized from the run for the reporting generator's period lookup.
    reporting_period_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    scenario_code: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # ¶67(a)(b): risks / exposures / entities covered + macro conditions and
    # assumptions — the analyst's narrative on the run.
    scenario_narrative: Mapped[str] = mapped_column(Text, nullable=False)
    # ¶67(d), ¶45: methodologies + justification of expert-judgement overlays —
    # the CRO/analyst assumptions rationale.
    assumptions_rationale: Mapped[str] = mapped_column(Text, nullable=False)
    methodology_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ¶16 reporting & challenge / ¶20: the Board's challenge and review record.
    board_challenge: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ¶20: the Board's rationale for the credibility of framework + results.
    credibility_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Headline outcomes captured from the run at sign-off time (the attestation
    # records what the Board actually attested to).
    stays_above_all_minima: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    with_actions_stays_above_all_minima: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    created_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    submitted_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Set on attestation; the maker-checker rule (attester != preparer) is enforced
    # in the service, so these user ids are never equal on an attested row.
    attested_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    attested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
