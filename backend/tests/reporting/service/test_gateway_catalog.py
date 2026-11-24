"""The AequorOS Reporting Gateway catalogue: routing, formula reading and freshness."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from app.reporting.service import gateway_catalog
from app.reporting.service.gateway_catalog import (
    _linear_terms,  # pyright: ignore[reportPrivateUsage]
)
from app.reporting.service.registry import get_definition
from app.reporting.service.reporting_settings import effective_channel

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "export_gateway_catalog.py"


def _exporter() -> ModuleType:
    spec = importlib.util.spec_from_file_location("export_gateway_catalog", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_catalogue_matches_the_templates() -> None:
    """The gateway's practice definitions are the AequorOS templates, exported."""
    stale = _exporter().stale()
    assert stale == [], (
        f"{len(stale)} catalogue file(s) are stale; run "
        "`uv run python scripts/export_gateway_catalog.py` from backend/"
    )


def _route(code: str, bank_default: str | None) -> str:
    definition = get_definition(code)
    assert definition is not None
    return effective_channel(
        definition.default_channel,
        bank_default,  # type: ignore[arg-type]
        return_code=code,
        allowed=definition.allowed_channels,
    )


@pytest.mark.parametrize(
    ("code", "bank_default", "expected"),
    [
        ("CAR-RWA", "reporting_gateway", "reporting_gateway"),
        ("BSD2", "reporting_gateway", "reporting_gateway"),
        ("CAR-RWA", None, "orass_sandbox"),
        # Not allowed through the gateway: the return keeps its own channel.
        ("ICAAP-REPORT", "reporting_gateway", "orass_sandbox"),
        # Allowed, but the gateway carries no definition for an event-driven pack.
        ("LRT-PROFILE", "reporting_gateway", "orass_sandbox"),
        # Other bank defaults replace only an electronic registry default.
        ("CAR-RWA", "orass_api", "orass_api"),
        ("BSD2", "orass_api", "email"),
    ],
)
def test_the_bank_default_routes_only_where_the_return_allows(
    code: str, bank_default: str | None, expected: str
) -> None:
    assert _route(code, bank_default) == expected


def test_gateway_carries_the_official_forms_and_tabular_platform_returns() -> None:
    accepted = gateway_catalog.accepted_codes()
    assert {"BSD2", "BSD4", "CAR-RWA", "LMT", "DBK-DAILY"} <= accepted
    assert not {"ICAAP-REPORT", "LRT-PROFILE", "STRESS-PACK"} & accepted


def _signed(formula: str) -> list[tuple[int, str]] | None:
    terms = _linear_terms(formula, "S")
    return None if terms is None else [(t.sign, t.ref) for t in terms]


def test_linear_template_formulas_become_signed_cells() -> None:
    assert _signed("=+B25+B26") == [(1, "B25"), (1, "B26")]
    assert _signed("=B68-SUM(B69:B71)") == [(1, "B68"), (-1, "B69"), (-1, "B70"), (-1, "B71")]
    assert _signed("=E10-(E12+E13)") == [(1, "E10"), (-1, "E12"), (-1, "E13")]
    assert _signed("=") == []


@pytest.mark.parametrize("formula", ["=C36/C37", "=B3+9", "=[1]BSD2!D38", '=IF(B10<0,"S","L")'])
def test_other_formulas_are_left_to_the_workbook(formula: str) -> None:
    assert _signed(formula) is None
