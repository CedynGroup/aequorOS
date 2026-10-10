"""Remove non-unique indexes covered by retained uniqueness indexes.

Only ordinary B-tree indexes with the same leading keys as an unconditional
unique constraint are removed. Tenant filters, parent joins and FK checks retain
that complete access path; primary keys and correctness constraints stay intact.
The served queries remain in capital.py (run/projection children), capital_plan.py
and liquidity_cfp.py (bank/version lists), fact_derivation.py (bank/fact group),
freshness.py (bank metrics), market_data_connections.py and market_data_sources.py
(bank connections, quotas and preferences), market_desk/entitlements.py (dataset),
regulatory_reporting/{channel_config,reporting_settings,workflow}.py (bank settings
and package artifacts), regulatory_{capital,forecasting,irr}.py (run children),
stress_scenarios.py (bank/module list), system_of_record.py (effective declaration),
temenos_connections.py (bank connections), bi/content.py (dashboard versions),
manage_bi_notifications.py (alert evaluations) and identity/api/list_organization_users.py
(tenant directory). None needs a key absent from the retained covering index.

Production usage statistics were unavailable: uncertain standalone, differently
ordered and partitioned indexes are deliberately retained.

Concurrent drops avoid blocking writers and are safe before the model changes.
Each statement commits independently; IF EXISTS makes interrupted upgrades
restartable. Downgrade recreates the original non-unique indexes concurrently.
"""

from __future__ import annotations

from alembic import op

revision = "202610100087"
down_revision = "202610080086"
branch_labels = None
depends_on = None

# (table, index, original keys, retained constraint-backed index)
COVERED_INDEXES: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    (
        "bi_alert_events",
        "ix_bi_alert_events_org_alert_evaluated",
        ("organization_id", "alert_id", "as_of_date"),
        "uq_bi_alert_events_alert_as_of_fingerprint",
    ),
    (
        "bi_dashboard_versions",
        "ix_bi_dashboard_versions_org_dashboard_version",
        ("organization_id", "dashboard_id", "version"),
        "uq_bi_dashboard_versions_dashboard_version",
    ),
    (
        "calculation_forecast_periods",
        "ix_calculation_forecast_periods_run_id",
        ("run_id",),
        "uq_calculation_forecast_run_period",
    ),
    (
        "capital_indicators",
        "ix_capital_indicators_projection",
        ("projection_id", "period_number"),
        "uq_capital_indicator_projection_period",
    ),
    (
        "capital_plans",
        "ix_capital_plans_bank",
        ("organization_id", "bank_id"),
        "uq_capital_plans_version",
    ),
    (
        "capital_projection_findings",
        "ix_capital_projection_findings_projection",
        ("projection_id",),
        "uq_capital_projection_finding",
    ),
    (
        "contingency_funding_plans",
        "ix_contingency_funding_plans_bank",
        ("organization_id", "bank_id"),
        "uq_contingency_funding_plans_version",
    ),
    (
        "current_financial_facts",
        "ix_current_financial_facts_org_bank_group",
        ("organization_id", "bank_id", "fact_group"),
        "uq_current_financial_facts_bank_group_category",
    ),
    (
        "database_direct_connections",
        "ix_database_direct_connections_org_bank",
        ("organization_id", "bank_id"),
        "uq_database_direct_connections_scope_name",
    ),
    (
        "liquidity_ewi_indicators",
        "ix_liquidity_ewi_indicators_bank",
        ("organization_id", "bank_id"),
        "uq_liquidity_ewi_indicators_scope",
    ),
    (
        "live_metrics",
        "ix_live_metrics_org_bank",
        ("organization_id", "bank_id"),
        "uq_live_metrics_org_bank_module",
    ),
    (
        "market_data_connections",
        "ix_market_data_connections_org_bank",
        ("organization_id", "bank_id"),
        "uq_market_data_connections_scope_name",
    ),
    (
        "market_data_entitlements",
        "ix_market_data_entitlements_org_dataset",
        ("organization_id", "dataset_code"),
        "uq_market_data_entitlements_org_dataset_from",
    ),
    (
        "market_data_quota_usage",
        "ix_market_data_quota_usage_org_bank",
        ("organization_id", "bank_id"),
        "uq_market_data_quota_usage_scope_month",
    ),
    (
        "market_data_source_preferences",
        "ix_market_data_source_preferences_bank",
        ("organization_id", "bank_id"),
        "uq_market_data_source_preferences_bank",
    ),
    (
        "regulatory_channel_configs",
        "ix_regulatory_channel_configs_org_bank",
        ("organization_id", "bank_id"),
        "uq_regulatory_channel_configs_scope",
    ),
    (
        "regulatory_line_items",
        "ix_regulatory_line_items_run_id",
        ("run_id",),
        "uq_regulatory_line_items_run_section_line",
    ),
    (
        "regulatory_metric_results",
        "ix_regulatory_metric_results_run_id",
        ("run_id",),
        "uq_regulatory_metric_results_run_metric",
    ),
    (
        "regulatory_package_artifacts",
        "ix_regulatory_package_artifacts_org_package",
        ("organization_id", "package_id"),
        "uq_regulatory_package_artifacts_pkg_kind",
    ),
    (
        "regulatory_reporting_settings",
        "ix_regulatory_reporting_settings_org_bank",
        ("organization_id", "bank_id"),
        "uq_regulatory_reporting_settings_scope",
    ),
    (
        "regulatory_validations",
        "ix_regulatory_validations_run_id",
        ("run_id",),
        "uq_regulatory_validations_run_rule",
    ),
    (
        "stress_scenarios",
        "ix_stress_scenarios_bank",
        ("organization_id", "bank_id", "module"),
        "uq_stress_scenarios_scope",
    ),
    (
        "system_of_record_declarations",
        "ix_system_of_record_declarations_resolution",
        ("organization_id", "bank_id", "position_type", "effective_from"),
        "uq_system_of_record_declarations_generation",
    ),
    (
        "temenos_connections",
        "ix_temenos_connections_org_bank",
        ("organization_id", "bank_id"),
        "uq_temenos_connections_scope_name",
    ),
    ("users", "ix_users_organization_id", ("organization_id",), "uq_users_organization_id_email"),
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for table, index, _columns, _covering in COVERED_INDEXES:
            op.drop_index(index, table_name=table, postgresql_concurrently=True, if_exists=True)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for table, index, columns, _covering in COVERED_INDEXES:
            op.create_index(
                index, table, list(columns), postgresql_concurrently=True, if_not_exists=True
            )
