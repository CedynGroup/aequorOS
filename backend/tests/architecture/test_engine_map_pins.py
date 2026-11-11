"""Characterization pins for the live-engine authorization maps.

Every map keyed by a live calculation engine is frozen here as the literal the
service serves today. A consolidation may re-home these maps onto one shared
table; these pins prove that such a move changes no key, no value and no
observable order. Vocabulary that stays a literal by design (``LIVE_MODULES``,
the DB CHECK vocabulary, and ``freshness._MODULES``) is pinned here too, so
any drift shows up in one place.
"""

from __future__ import annotations

from app.bi.domain.authority import (
    AUTHORIZATION_MODULE,
    ENGINE_MODULES,
    ENTITLEMENT_SLUG,
)
from app.core.authorization import Module
from app.live.models.live import LIVE_MODULES
from app.live.service import data_activation, freshness, live_access, live_view
from app.policy.service import module_scope

_ENGINE_ORDER = ["liquidity", "capital", "credit", "irr", "fx", "ftp", "rating", "forecast"]


def test_live_module_vocabulary_is_pinned() -> None:
    assert list(LIVE_MODULES) == _ENGINE_ORDER
    assert list(freshness._MODULES) == _ENGINE_ORDER


def test_freshness_view_modules_are_pinned() -> None:
    assert live_access.FRESHNESS_VIEW_MODULES == {
        "liquidity": Module.LIQUIDITY,
        "capital": Module.CAPITAL,
        "credit": Module.CREDIT,
        "irr": Module.IRRBB,
        "fx": Module.FX,
        "ftp": Module.FTP,
        "rating": Module.MARKETS,
        "forecast": Module.FORECASTING,
    }
    assert list(live_access.FRESHNESS_VIEW_MODULES) == _ENGINE_ORDER
    assert all(isinstance(module, Module) for module in live_access.FRESHNESS_VIEW_MODULES.values())


def test_module_scope_key_is_pinned() -> None:
    assert module_scope.MODULE_SCOPE_KEY == {
        "liquidity": "liquidity",
        "capital": "capital",
        "irr": "irrbb",
        "fx": "fx",
        "ftp": "ftp",
        "rating": "markets",
        "credit": "credit",
        "implied_rating": "markets",
        "forecast": "forecasting",
    }


def test_bi_engine_modules_are_pinned() -> None:
    assert list(ENGINE_MODULES) == _ENGINE_ORDER


def test_bi_authorization_module_is_pinned() -> None:
    assert AUTHORIZATION_MODULE == {
        "liquidity": "liq",
        "capital": "cap",
        "credit": "credit",
        "irr": "irrbb",
        "fx": "fx",
        "ftp": "ftp",
        "rating": "markets",
        "forecast": "fcst",
    }
    assert list(AUTHORIZATION_MODULE) == _ENGINE_ORDER


def test_bi_entitlement_slug_is_pinned() -> None:
    assert ENTITLEMENT_SLUG == {
        "liquidity": "liquidity",
        "capital": "capital",
        "credit": "credit",
        "irr": "irrbb",
        "fx": "fx",
        "ftp": "ftp",
        "rating": "markets",
        "forecast": "forecasting",
    }
    assert list(ENTITLEMENT_SLUG) == _ENGINE_ORDER


def test_mint_run_engines_are_pinned() -> None:
    assert live_view.MINT_RUN_ENGINES == (
        ("liquidity", Module.LIQUIDITY),
        ("capital", Module.CAPITAL),
        ("rating", Module.MARKETS),
        ("credit", Module.CREDIT),
        ("irr", Module.IRRBB),
        ("fx", Module.FX),
        ("ftp", Module.FTP),
        ("forecast", Module.FORECASTING),
    )


def test_official_modules_are_pinned() -> None:
    assert data_activation.OFFICIAL_MODULES == (
        "liquidity",
        "capital",
        "credit",
        "irr",
        "fx",
        "ftp",
        "forecast",
    )
