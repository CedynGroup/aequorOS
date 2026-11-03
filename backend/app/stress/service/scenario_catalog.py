"""Scenario Workbench catalogue: system scenarios + shock vocabularies.

Pure module (no DB models of its own). Two responsibilities:

- The SYSTEM scenario definitions per module — the regulatory/directive
  scenario set the official runs iterate. Codes are sourced from the existing
  ``*_SCENARIO_CODES`` tuples so the catalogue cannot drift from the engines;
  labels and descriptions live here so the dashboard stops hardcoding them.
  System scenarios are never database rows: their shock values resolve from
  the effective-dated ``ParamStressShock`` register at read time.

- The shock-key VOCABULARY per module, built from the engine constants (never
  string literals), so a user-authored scenario is validated at creation time
  with an error that names the allowed keys — not at the moment an analyst
  presses run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.capital.domain.engine import (
    REQUIRED_STRESS_SHOCK_KEYS as CAPITAL_REQUIRED_KEYS,
)
from app.capital.public import regulatory_capital
from app.ftp.public import (
    FTP_RUN_SCENARIO_CODES,
    SHOCK_CURVE_SHIFT_BP,
    SHOCK_FUNDING_ADD_BP,
    SHOCK_MIN_MARGIN_PCT,
    SHOCK_NMD_CORE_MAX_PCT,
    SHOCK_NMD_CORE_MIN_PCT,
)
from app.fx.public import FX_RUN_SCENARIO_CODES, SHOCK_DEPRECIATION
from app.identity.public import Bank
from app.irrbb.domain.engine import (
    SHOCK_DECAY_YEARS,
    SHOCK_LONG_BP,
    SHOCK_PARALLEL_BP,
    SHOCK_SHORT_BP,
)
from app.irrbb.public import IRR_RUN_SCENARIO_CODES
from app.liquidity.domain.engine import (
    SHOCK_ASF_PREFIX,
    SHOCK_FX_DEPRECIATION,
    SHOCK_HQLA_SECURITIES_HAIRCUT,
    SHOCK_INFLOW_MULTIPLIER,
    SHOCK_NMD_RUNOFF_PREFIX,
    SHOCK_RSF_SECURITIES_OVERRIDE,
    SHOCK_RUNOFF_PREFIX,
)
from app.liquidity.public import regulatory_liquidity
from app.policy.public import ParamStressShock, get_active_params

CAPITAL_SCENARIO_CODES = regulatory_capital.CAPITAL_SCENARIO_CODES
ECL_CONDITIONING_KEYS = regulatory_capital.ECL_CONDITIONING_KEYS
LIQUIDITY_SCENARIO_CODES = regulatory_liquidity.LIQUIDITY_SCENARIO_CODES

WORKBENCH_MODULES = ("liquidity", "capital", "irr", "fx", "ftp")


@dataclass(frozen=True)
class SystemScenarioDef:
    code: str
    label: str
    description: str


_LIQUIDITY_DEFS = {
    "baseline": SystemScenarioDef(
        "baseline", "Baseline", "Current positions under the active regulatory parameter set."
    ),
    "idiosyncratic": SystemScenarioDef(
        "idiosyncratic",
        "Idiosyncratic stress",
        "Counterparty-specific funding shock — stressed deposit run-off rates.",
    ),
    "market_wide": SystemScenarioDef(
        "market_wide",
        "Market-wide stress",
        "System-wide stress — inflow haircuts and HQLA securities haircuts.",
    ),
    "combined": SystemScenarioDef(
        "combined",
        "Combined stress",
        "Concurrent idiosyncratic and market-wide shock (supervisory severe).",
    ),
    "usd_funding_stress": SystemScenarioDef(
        "usd_funding_stress",
        "USD funding stress",
        "Foreign-currency funding squeeze with currency-depreciation coupling.",
    ),
}

_CAPITAL_DEFS = {
    "baseline": SystemScenarioDef(
        "baseline", "Baseline", "Current capital position under the active parameter set."
    ),
    "mild": SystemScenarioDef(
        "mild", "Mild stress", "Four-quarter path under a mild credit and RWA-growth shock."
    ),
    "moderate": SystemScenarioDef(
        "moderate", "Moderate stress", "Four-quarter path under a moderate downturn."
    ),
    "severe": SystemScenarioDef(
        "severe", "Severe stress", "Supervisory severe four-quarter capital path."
    ),
}

_IRR_DEFS = {
    "baseline": SystemScenarioDef("baseline", "Baseline", "Current yield curve, no shock."),
    "parallel_up_200": SystemScenarioDef(
        "parallel_up_200", "Parallel +200bp", "Parallel upward rate shock of 200bp."
    ),
    "parallel_down_200": SystemScenarioDef(
        "parallel_down_200", "Parallel −200bp", "Parallel downward rate shock of 200bp."
    ),
    "short_up_250": SystemScenarioDef(
        "short_up_250", "Short rates +250bp", "Short-end shock, decaying along the curve."
    ),
    "short_down_250": SystemScenarioDef(
        "short_down_250", "Short rates −250bp", "Short-end downward shock."
    ),
    "steepener": SystemScenarioDef("steepener", "Steepener", "Short rates down, long rates up."),
    "flattener": SystemScenarioDef("flattener", "Flattener", "Short rates up, long rates down."),
    "parallel_up_450": SystemScenarioDef(
        "parallel_up_450", "Parallel +450bp", "Local-rate severity shock (informational)."
    ),
    "parallel_down_450": SystemScenarioDef(
        "parallel_down_450", "Parallel −450bp", "Local-rate severity shock (informational)."
    ),
}

_FX_DEFS = {
    "baseline": SystemScenarioDef("baseline", "Baseline", "Current net open position."),
    "mild_depreciation": SystemScenarioDef(
        "mild_depreciation", "Mild depreciation", "Mild local-currency depreciation shock."
    ),
    "severe_depreciation": SystemScenarioDef(
        "severe_depreciation", "Severe depreciation", "Severe depreciation shock."
    ),
    "cedi_crisis": SystemScenarioDef(
        "cedi_crisis", "Currency crisis", "Crisis-level depreciation shock."
    ),
}

_FTP_DEFS = {
    "baseline": SystemScenarioDef("baseline", "Baseline", "Current FTP curve, no overlay."),
    "rates_up_200": SystemScenarioDef(
        "rates_up_200", "Rates +200bp", "Parallel FTP curve shift upward."
    ),
    "funding_stress": SystemScenarioDef(
        "funding_stress", "Funding stress", "Funding-spread widening overlay."
    ),
}


def _defs_for(codes: tuple[str, ...], table: dict[str, SystemScenarioDef]):
    return tuple(
        table.get(code, SystemScenarioDef(code, code.replace("_", " ").title(), ""))
        for code in codes
    )


SYSTEM_SCENARIOS: dict[str, tuple[SystemScenarioDef, ...]] = {
    "liquidity": _defs_for(LIQUIDITY_SCENARIO_CODES, _LIQUIDITY_DEFS),
    "capital": _defs_for(CAPITAL_SCENARIO_CODES, _CAPITAL_DEFS),
    "irr": _defs_for(IRR_RUN_SCENARIO_CODES, _IRR_DEFS),
    "fx": _defs_for(FX_RUN_SCENARIO_CODES, _FX_DEFS),
    "ftp": _defs_for(FTP_RUN_SCENARIO_CODES, _FTP_DEFS),
}


@dataclass(frozen=True)
class ShockVocabulary:
    exact_keys: frozenset[str]
    prefixes: tuple[str, ...] = ()


SHOCK_VOCABULARY: dict[str, ShockVocabulary] = {
    "liquidity": ShockVocabulary(
        exact_keys=frozenset(
            {
                SHOCK_INFLOW_MULTIPLIER,
                SHOCK_HQLA_SECURITIES_HAIRCUT,
                SHOCK_RSF_SECURITIES_OVERRIDE,
                SHOCK_FX_DEPRECIATION,
            }
        ),
        prefixes=(SHOCK_RUNOFF_PREFIX, SHOCK_ASF_PREFIX, SHOCK_NMD_RUNOFF_PREFIX),
    ),
    "capital": ShockVocabulary(
        exact_keys=frozenset(CAPITAL_REQUIRED_KEYS) | frozenset(ECL_CONDITIONING_KEYS)
    ),
    "irr": ShockVocabulary(
        exact_keys=frozenset({SHOCK_PARALLEL_BP, SHOCK_SHORT_BP, SHOCK_LONG_BP, SHOCK_DECAY_YEARS})
    ),
    "fx": ShockVocabulary(exact_keys=frozenset({SHOCK_DEPRECIATION})),
    "ftp": ShockVocabulary(
        exact_keys=frozenset(
            {
                SHOCK_CURVE_SHIFT_BP,
                SHOCK_FUNDING_ADD_BP,
                SHOCK_MIN_MARGIN_PCT,
                SHOCK_NMD_CORE_MIN_PCT,
                SHOCK_NMD_CORE_MAX_PCT,
            }
        )
    ),
}


def _allowed_detail(module: str) -> dict[str, object]:
    vocabulary = SHOCK_VOCABULARY[module]
    return {
        "allowed_keys": sorted(vocabulary.exact_keys),
        "allowed_prefixes": list(vocabulary.prefixes),
    }


def validate_shocks(module: str, shocks: dict[str, Decimal]) -> None:
    """Reject unknown shock keys with an error naming the vocabulary.

    Also enforces the engines' structural rules at authoring time so a bad
    scenario fails when it is saved, not when an analyst presses run:
    the capital stress engine hard-requires all four quarterly-path keys, and
    the IRR shift builder pairs long/short/decay the same way the Basel
    scenario table does.
    """
    vocabulary = SHOCK_VOCABULARY[module]
    for key in shocks:
        if key in vocabulary.exact_keys:
            continue
        if any(key.startswith(prefix) for prefix in vocabulary.prefixes):
            continue
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error_code": "unsupported_shock_key",
                "module": module,
                "shock_key": key,
                **_allowed_detail(module),
            },
        )
    if module == "ftp":
        for key in (SHOCK_MIN_MARGIN_PCT, SHOCK_NMD_CORE_MIN_PCT, SHOCK_NMD_CORE_MAX_PCT):
            value = shocks.get(key)
            if value is not None and not Decimal(0) <= value <= Decimal(100):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail={
                        "error_code": "invalid_ftp_rule",
                        "message": f"{key} must be between 0 and 100.",
                    },
                )
    if module == "capital" and shocks:
        quarterly = [key for key in CAPITAL_REQUIRED_KEYS if key in shocks]
        if quarterly and len(quarterly) != len(CAPITAL_REQUIRED_KEYS):
            missing = sorted(set(CAPITAL_REQUIRED_KEYS) - set(quarterly))
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "error_code": "incomplete_capital_shock_set",
                    "message": (
                        "The capital stress path requires all four quarterly keys "
                        "together: " + ", ".join(CAPITAL_REQUIRED_KEYS) + "."
                    ),
                    "missing_keys": missing,
                },
            )
    if module == "irr" and shocks:
        if SHOCK_LONG_BP in shocks and SHOCK_SHORT_BP not in shocks:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "error_code": "incomplete_irr_shock_set",
                    "message": f"'{SHOCK_LONG_BP}' requires '{SHOCK_SHORT_BP}'.",
                },
            )
        if (
            SHOCK_SHORT_BP in shocks
            and SHOCK_PARALLEL_BP not in shocks
            and SHOCK_LONG_BP not in shocks
            and SHOCK_DECAY_YEARS not in shocks
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "error_code": "incomplete_irr_shock_set",
                    "message": (
                        f"A short-end-only shock needs '{SHOCK_DECAY_YEARS}' to "
                        "describe how it decays along the curve."
                    ),
                },
            )


def system_scenario_codes(module: str) -> tuple[str, ...]:
    return tuple(entry.code for entry in SYSTEM_SCENARIOS[module])


def system_shocks(  # noqa: PLR0913 - one lookup carries its full scoping
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    module: str,
    scenario_code: str,
    as_of: date,
) -> dict[str, Decimal]:
    """The effective-dated shock set for one system scenario (baseline = {})."""
    if scenario_code == "baseline":
        return {}
    rows = get_active_params(
        db, ctx.organization_id, bank.jurisdiction_code, ParamStressShock, as_of
    )
    return {
        row.shock_key: Decimal(str(row.shock_value))
        for row in rows
        if row.module == module and row.scenario_code == scenario_code
    }
