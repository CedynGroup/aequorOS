"""``Module.CREDIT`` is a first-class institution module with a gated shared surface.

Credit shipped under the ``risk`` label with no server-side consumer, and the
shared live feeds served its rows to every tenant reader while liquidity,
IRRBB, FX and FTP were filtered by binding (D-017). These pins keep the module
in the vocabulary, in the projection, in the cutover gate's module set, in the
Members composer's sentence, and in each shared surface's gate list — so a
future edit cannot quietly drop credit back to "served ungated".
"""

from __future__ import annotations

import ast
import inspect

from app.core.authorization import Module, ModuleScope, RoleBundle, SensitivityScope
from app.services import alerts, live_view, window_analytics
from app.services.authorization import _INSTITUTION_MODULES  # noqa: PLC2701 - projection oracle
from app.services.grant_administration import (
    _MODULE_LABELS,  # noqa: PLC2701 - sentence oracle
    compose_authority_sentence,
)
from scripts.authorization_access_impact import INSTITUTION_MODULES

_CREDIT_GATE = ("credit", Module.CREDIT)


def test_credit_is_a_concrete_module_and_a_binding_scope() -> None:
    assert Module.CREDIT.value == "credit"
    assert ModuleScope.CREDIT.value == Module.CREDIT.value
    # ``ModuleScope`` is ``all`` plus every concrete module, in the same order.
    assert [scope.value for scope in ModuleScope][1:] == [module.value for module in Module]


def test_credit_is_an_institution_module_in_the_projection_and_the_cutover_gate() -> None:
    assert Module.CREDIT in _INSTITUTION_MODULES
    assert "credit" in INSTITUTION_MODULES
    # Both derive from the enum; a hand-maintained copy would drift.
    assert tuple(module.value for module in _INSTITUTION_MODULES) == INSTITUTION_MODULES


def test_every_module_scope_has_a_production_label() -> None:
    assert set(_MODULE_LABELS) == set(ModuleScope)
    assert _MODULE_LABELS[ModuleScope.CREDIT] == "Credit"


def test_a_credit_grant_reads_as_one_plain_sentence() -> None:
    sentence = compose_authority_sentence(
        principal_name="Ama Mensah",
        role_bundle=RoleBundle.VIEWER,
        institution_name="Sample Bank",
        module_scope=ModuleScope.CREDIT,
        sensitivity_scope=SensitivityScope.AGGREGATED,
    )
    assert sentence == "Ama Mensah is a Viewer in Credit for Sample Bank, covering Aggregated data."
    blotter = compose_authority_sentence(
        principal_name="Ama Mensah",
        role_bundle=RoleBundle.ANALYST,
        institution_name="Sample Bank",
        module_scope=ModuleScope.CREDIT,
        sensitivity_scope=SensitivityScope.RESTRICTED,
    )
    assert blotter == (
        "Ama Mensah is an Analyst in Credit for Sample Bank, covering Restricted data."
    )


def test_shared_surfaces_gate_credit_alongside_the_other_engines() -> None:
    """Live summary, alerts and window analytics name CREDIT in their gate list."""
    for surface in (live_view, alerts, window_analytics):
        gated = surface._GATED_ENGINE_MODULES  # noqa: SLF001 - the pin reads the gate itself
        assert _CREDIT_GATE in gated, surface.__name__
        # Every live engine names its module authority.
        assert {engine for engine, _module in gated} == {
            "liquidity",
            "credit",
            "capital",
            "rating",
            "irr",
            "fx",
            "ftp",
            "forecast",
        }
        assert all(isinstance(module, Module) for _engine, module in gated)


def test_explicit_credit_snapshot_ladders_require_the_same_view() -> None:
    """``list_live_snapshots`` maps ``credit`` to ``Module.CREDIT`` in its guard dict."""
    tree = ast.parse(inspect.getsource(live_view.list_live_snapshots))
    protected: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=True):
            if (
                isinstance(key, ast.Constant)
                and isinstance(value, ast.Attribute)
                and isinstance(value.value, ast.Name)
                and value.value.id == "Module"
            ):
                protected[str(key.value)] = value.attr
    assert protected.get("credit") == "CREDIT", protected
