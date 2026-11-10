"""``Module.CREDIT`` is a first-class institution module with a gated shared surface.

Credit shipped under the ``risk`` label with no server-side consumer, and the
shared live feeds served its rows to every tenant reader while liquidity,
IRRBB, FX and FTP were filtered by binding (D-017). These pins keep the module
in the vocabulary, in the projection, in the cutover gate's module set, in the
Members composer's sentence, and in the one closed live-read authority map —
so a future edit cannot quietly drop credit back to "served ungated".
"""

from __future__ import annotations

import inspect

from app.core.authorization import Module, ModuleScope, RoleBundle, SensitivityScope
from app.identity.service.authorization import (
    _INSTITUTION_MODULES,  # noqa: PLC2701 - projection oracle
)
from app.identity.service.grant_administration import (
    _MODULE_LABELS,  # noqa: PLC2701 - sentence oracle
    compose_authority_sentence,
)
from app.live.models.live import LIVE_MODULES
from app.live.service import live_access, live_view
from scripts.authorization_access_impact import INSTITUTION_MODULES


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
    """Credit rides the one closed live-read map every surface filters with."""
    assert live_access.module_for("credit") is Module.CREDIT
    # Every live engine is mapped; none is unmapped.
    assert set(live_access.FRESHNESS_VIEW_MODULES) == {
        "liquidity",
        "credit",
        "capital",
        "rating",
        "irr",
        "fx",
        "ftp",
        "forecast",
    }
    assert set(live_access.FRESHNESS_VIEW_MODULES) == set(LIVE_MODULES)
    assert all(isinstance(module, Module) for module in live_access.FRESHNESS_VIEW_MODULES.values())


def test_explicit_credit_snapshot_ladders_require_the_same_view() -> None:
    """``list_live_snapshots`` gates every ladder through the shared map."""
    assert live_access.module_for("credit") is Module.CREDIT
    assert "module_for" in inspect.getsource(live_view.list_live_snapshots)
