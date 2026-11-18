"""The SDI readiness surface shows each caller only the rows it may view.

The readiness report names what each module still needs to be fed, module by
module — so each row is disclosed to exactly the authority that may view that
module at aggregated sensitivity. A module name outside the map is denied for
every actor: it is filtered before any authority is consulted, the same
default-deny the live freshness map applies.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any, cast

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.capital.service import sdi_readiness
from app.capital.service.sdi_readiness import ModuleReadiness
from app.core.authorization import Module
from app.identity.public import Bank

#: The evaluator is stubbed in these tests, so the session, context and bank
#: are never touched; the Nones only satisfy the call signature.
_NO_DB = cast(Session, None)
_NO_CTX = cast(TenantContext, None)
_NO_BANK = cast(Bank, None)


def _allow_everything(calls: list[object]) -> Any:
    def evaluate(*args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs.get("module"))
        return SimpleNamespace(allowed=True)

    return evaluate


def test_the_module_map_covers_exactly_the_reported_rows() -> None:
    assert set(sdi_readiness.READINESS_VIEW_MODULES) == {
        "liquidity_table1",
        "maturity_ladder",
        "funding_concentration",
        "capital",
        "exposures",
        "provisioning",
    }
    assert sdi_readiness.module_for("liquidity_table1") is Module.LIQUIDITY
    assert sdi_readiness.module_for("maturity_ladder") is Module.LIQUIDITY
    assert sdi_readiness.module_for("funding_concentration") is Module.LIQUIDITY
    assert sdi_readiness.module_for("capital") is Module.CAPITAL
    assert sdi_readiness.module_for("exposures") is Module.CREDIT
    assert sdi_readiness.module_for("provisioning") is Module.CREDIT


def test_an_unknown_module_denies_without_consulting_any_authority(
    monkeypatch: Any,
) -> None:
    """Even an evaluator that would allow everything must never be asked."""
    calls: list[object] = []
    monkeypatch.setattr(
        "app.identity.public.evaluate_bank_permission", _allow_everything(calls)
    )
    assert sdi_readiness.can_view_module(_NO_DB, _NO_CTX, _NO_BANK, "mystery_module") is False
    assert calls == [], "an unknown module must be denied before any evaluation"
    assert sdi_readiness.can_view_module(_NO_DB, _NO_CTX, _NO_BANK, "capital") is True
    assert calls == [Module.CAPITAL]


def test_rows_for_unknown_modules_are_filtered_for_every_actor(
    monkeypatch: Any,
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(
        "app.identity.public.evaluate_bank_permission", _allow_everything(calls)
    )
    rows = [
        ModuleReadiness(module="mystery_module", status="ready", reasons=[]),
        ModuleReadiness(module="liquidity_table1", status="ready", reasons=[]),
        ModuleReadiness(module="capital", status="blocked", reasons=["x"]),
        ModuleReadiness(module="provisioning", status="partial", reasons=["y"]),
    ]
    monkeypatch.setattr(
        sdi_readiness,
        "assess_sdi_readiness",
        lambda *args, **kwargs: rows,
    )
    visible = sdi_readiness.readiness_for_caller(_NO_DB, _NO_CTX, _NO_BANK, date(2026, 6, 30))
    assert [row.module for row in visible] == ["liquidity_table1", "capital", "provisioning"]
