"""Where each metric's calculation engine lives, keyed by its frozen identifier.

``MetricAuthority.calculation_engine`` is written into the provenance of every
regulatory package, and the attestation content digest covers it, so it is an
identifier and not a pointer: it keeps the ``module:callable`` path the engine had
when it was registered. Moving the engine's code must not change it, or the same
data would file a different package. ``ENGINE_LOCATIONS`` maps each identifier to
the engine's current ``(module, callable)``. ``scripts/feature_moves.py`` rewrites
the locations when code moves and never touches the identifiers.
"""

from __future__ import annotations

from collections.abc import Mapping

ENGINE_LOCATIONS: Mapping[str, tuple[str, str]] = {
    "app.domain.capital.ecl:compute_ecl": ("app.domain.capital.ecl", "compute_ecl"),
    "app.domain.capital.engine:compute_capital_ratios": (
        "app.domain.capital.engine",
        "compute_capital_ratios",
    ),
    "app.domain.capital.engine:compute_rwa": ("app.domain.capital.engine", "compute_rwa"),
    "app.domain.capital.engine:tier1_capital": ("app.domain.capital.engine", "tier1_capital"),
    "app.domain.capital.loan_classification:classify_book": (
        "app.domain.capital.loan_classification",
        "classify_book",
    ),
    "app.domain.credit.pd:estimate_pd": ("app.domain.credit.pd", "estimate_pd"),
    "app.domain.forecasting.engine:project": ("app.domain.forecasting.engine", "project"),
    "app.domain.ftp.engine:branch_profitability": ("app.domain.ftp.engine", "branch_profitability"),
    "app.domain.ftp.engine:build_curve": ("app.domain.ftp.engine", "build_curve"),
    "app.domain.ftp.engine:nmd_split": ("app.domain.ftp.engine", "nmd_split"),
    "app.domain.ftp.engine:product_profitability": (
        "app.domain.ftp.engine",
        "product_profitability",
    ),
    "app.domain.fx.engine:compute_nop": ("app.domain.fx.engine", "compute_nop"),
    "app.domain.fx.engine:compute_stressed_var": ("app.domain.fx.engine", "compute_stressed_var"),
    "app.domain.fx.engine:compute_var": ("app.domain.fx.engine", "compute_var"),
    "app.domain.irr.engine:compute_duration": ("app.domain.irr.engine", "compute_duration"),
    "app.domain.irr.engine:compute_ear": ("app.domain.irr.engine", "compute_ear"),
    "app.domain.irr.engine:compute_eve": ("app.domain.irr.engine", "compute_eve"),
    "app.domain.irr.engine:compute_gap": ("app.domain.irr.engine", "compute_gap"),
    "app.domain.irr.engine:compute_nii": ("app.domain.irr.engine", "compute_nii"),
    "app.domain.irr.engine:run_irr_scenarios": ("app.domain.irr.engine", "run_irr_scenarios"),
    "app.domain.irr.standardised:run": ("app.domain.irr.standardised", "run"),
    "app.domain.liquidity.engine:compute_currency_gaps": (
        "app.domain.liquidity.engine",
        "compute_currency_gaps",
    ),
    "app.domain.liquidity.engine:compute_lcr": ("app.domain.liquidity.engine", "compute_lcr"),
    "app.domain.liquidity.engine:compute_nsfr": ("app.domain.liquidity.engine", "compute_nsfr"),
    "app.domain.liquidity.engine:compute_stressed_ladder": (
        "app.domain.liquidity.engine",
        "compute_stressed_ladder",
    ),
    "app.domain.rating.engine:compute_rating": ("app.domain.rating.engine", "compute_rating"),
    "app.domain.rating.engine:ddep_stress": ("app.domain.rating.engine", "ddep_stress"),
    "app.domain.stress.orchestrator:run_enterprise_stress": (
        "app.domain.stress.orchestrator",
        "run_enterprise_stress",
    ),
    "app.services.regulatory_capital:capital_breach_multiplier": (
        "app.services.regulatory_capital",
        "capital_breach_multiplier",
    ),
    "app.services.regulatory_liquidity:liquidity_breach_multiplier": (
        "app.services.regulatory_liquidity",
        "liquidity_breach_multiplier",
    ),
    "app.services.regulatory_reporting.bog_forms.engine:compute_form": (
        "app.services.regulatory_reporting.bog_forms.engine",
        "compute_form",
    ),
    "app.services.regulatory_reporting.le_generation:_table11_section": (
        "app.services.regulatory_reporting.le_generation",
        "_table11_section",
    ),
    "app.services.regulatory_reporting.le_generation:_table1_inputs": (
        "app.services.regulatory_reporting.le_generation",
        "_table1_inputs",
    ),
    "app.services.sdi_capital:compute_sdi_capital_summary": (
        "app.services.sdi_capital",
        "compute_sdi_capital_summary",
    ),
    "app.services.sdi_capital_checks:check_paid_up_capital": (
        "app.services.sdi_capital_checks",
        "check_paid_up_capital",
    ),
    "app.services.sdi_capital_checks:check_statutory_reserve_fund": (
        "app.services.sdi_capital_checks",
        "check_statutory_reserve_fund",
    ),
    "app.services.sdi_views:get_sdi_liquidity_position": (
        "app.services.sdi_views",
        "get_sdi_liquidity_position",
    ),
}
