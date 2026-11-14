"""The scenario-workbench policy table: every module gates on a scoped binding.

The shared workbench routes serve several engines. A module may ride those
routes only with an explicit scoped policy; a module without one is denied —
never silently passed to the scalar role ladder.
"""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import Module, Permission, Sensitivity
from app.identity.models.bank import Bank
from app.identity.public import DEFAULT_DENIAL_DETAIL
from app.stress.schemas.scenario_workbench import WorkbenchModule
from app.stress.service import scenario_workbench_authorization as workbench
from app.stress.service.scenario_catalog import WORKBENCH_MODULES

CTX = TenantContext(organization_id="OR-POLICY", actor_user_id=None, authorization_version=None)


def test_capital_policy_mirrors_the_engine_module_policies() -> None:
    policy = workbench._WORKBENCH_POLICIES["capital"]
    assert policy.module is Module.CAPITAL
    assert policy.surface_prefix == "capital"
    # Saved workbench analyses are engine snapshots; capital reads them at the
    # confidential sensitivity the capital rollout contract pins for the
    # workbench, like the IRR policy does.
    assert policy.saved_analysis_list_sensitivity is Sensitivity.CONFIDENTIAL
    assert workbench.surface("capital", "scenario_create") == "capital_scenario_create"
    assert workbench.surface("capital", "scenario_analysis_run") == "capital_scenario_analysis_run"


def test_every_workbench_module_has_an_explicit_policy() -> None:
    assert set(workbench._WORKBENCH_POLICIES) == set(WORKBENCH_MODULES)
    assert set(WORKBENCH_MODULES) == set(get_args(WorkbenchModule.__value__))


def test_module_policies_map_to_their_engine_modules() -> None:
    expected: dict[WorkbenchModule, Module] = {
        "liquidity": Module.LIQUIDITY,
        "capital": Module.CAPITAL,
        "irr": Module.IRRBB,
        "fx": Module.FX,
        "ftp": Module.FTP,
    }
    for module, engine_module in expected.items():
        assert workbench._WORKBENCH_POLICIES[module].module is engine_module


def test_unknown_module_denies_mutation_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(workbench._WORKBENCH_POLICIES, "ftp")
    with pytest.raises(HTTPException) as denied:
        workbench.mutation_context("ftp", CTX)
    assert denied.value.status_code == 403
    assert denied.value.detail == DEFAULT_DENIAL_DETAIL


def test_unknown_module_denies_require_module_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(workbench._WORKBENCH_POLICIES, "ftp")
    with pytest.raises(HTTPException) as denied:
        workbench.require_module_permission(
            Session(),
            CTX,
            Bank(),
            "ftp",
            permission=Permission.RUN,
            sensitivity=Sensitivity.CONFIDENTIAL,
            surface="ftp_scenario_analysis_run",
        )
    assert denied.value.status_code == 403
    assert denied.value.detail == DEFAULT_DENIAL_DETAIL


def test_unknown_module_denies_saved_analysis_list_sensitivity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(workbench._WORKBENCH_POLICIES, "ftp")
    with pytest.raises(HTTPException) as denied:
        workbench.saved_analysis_list_sensitivity("ftp")
    assert denied.value.status_code == 403


def test_scalar_mutation_ladder_is_not_part_of_the_workbench_gate() -> None:
    source = Path(workbench.__file__).read_text(encoding="utf-8")
    assert "get_mutation_tenant_context" not in source
    assert "MutationTenant" not in source
