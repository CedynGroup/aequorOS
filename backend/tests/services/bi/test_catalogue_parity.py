"""The BI domain restates a few service/model facts; these tests keep them true.

``app.domain.bi`` may not import ``app.models`` or ``app.services``, so it
carries its own copies of: the live-module vocabulary, the module → entitlement
slug map, the module → authorization-module map, the read-computed methodologies
a live module publishes, and the DPD / FX reading rules of the classification
service. Each copy is pinned here against its owner.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from app.core.authorization import Module
from app.domain.authority.registry import REGISTRY
from app.domain.bi import extract
from app.domain.bi.authority import (
    AUTHORIZATION_MODULE,
    ENGINE_MODULES,
    ENTITLEMENT_SLUG,
    READ_COMPUTED_LIVE_MODULES,
    TEXT_VALUED_METRIC_IDS,
)
from app.domain.positions.families import loan_family
from app.models.live import LIVE_MODULES
from app.services import fact_derivation, loan_classification, module_scope, pipeline

BACKEND = Path(__file__).parents[3]

#: Which service publishes each read-computed methodology's metrics, by source file.
_PUBLISHER_SOURCE: dict[str, str] = {
    "aequoros_implied_rating_scorecard": "app/services/implied_rating.py",
    "act930_s29_nof_rwa": "app/services/regulatory_capital.py",
}


def test_engine_modules_are_the_live_modules() -> None:
    assert ENGINE_MODULES == LIVE_MODULES
    assert set(ENGINE_MODULES) == {module for module, _ in pipeline._CHEAP_MODULES}


def test_entitlement_slugs_mirror_module_scope() -> None:
    for module in ENGINE_MODULES:
        assert ENTITLEMENT_SLUG[module] == module_scope.MODULE_SCOPE_KEY[module]
        assert ENTITLEMENT_SLUG[module] == pipeline._MODULE_SCOPE_KEY.get(module, module)


def test_authorization_modules_are_enum_values() -> None:
    for module in ENGINE_MODULES:
        assert Module(AUTHORIZATION_MODULE[module]).value == AUTHORIZATION_MODULE[module]
    assert AUTHORIZATION_MODULE["credit"] == Module.CREDIT.value
    assert AUTHORIZATION_MODULE["irr"] == Module.IRRBB.value
    assert AUTHORIZATION_MODULE["forecast"] == Module.FORECASTING.value
    assert AUTHORIZATION_MODULE["rating"] == Module.MARKETS.value


@pytest.mark.parametrize(("methodology_id", "module"), sorted(READ_COMPUTED_LIVE_MODULES.items()))
def test_read_computed_methodologies_are_published_by_the_named_live_module(
    methodology_id: str, module: str
) -> None:
    """Every metric key of the methodology appears in the publishing service's source."""
    assert module in ENGINE_MODULES
    source = (BACKEND / _PUBLISHER_SOURCE[methodology_id]).read_text(encoding="utf-8")
    entries = [e for e in REGISTRY if e.methodology_id == methodology_id and e.is_primary]
    assert entries, methodology_id
    for entry in entries:
        # A run-sealed metric is not read-computed and does not belong in the map.
        assert entry.authoritative_run_type is None, entry.key
        assert re.search(rf'"{re.escape(entry.metric_id)}"', source), (
            f"{entry.metric_id} is not written by {_PUBLISHER_SOURCE[methodology_id]}"
        )


def test_text_valued_metric_ids_are_registered_and_written_as_text() -> None:
    source = (BACKEND / "app/services/implied_rating.py").read_text(encoding="utf-8")
    for metric_id in TEXT_VALUED_METRIC_IDS:
        assert REGISTRY.for_metric(metric_id)
        assert re.search(rf'"{re.escape(metric_id)}"', source), metric_id


@pytest.mark.parametrize("raw", [None, "", " 12 ", "12.0", 7, -1, "-3", "abc", "0", 0, "1e2", True])
def test_days_past_due_coercion_matches_the_classification_service(raw: Any) -> None:
    assert extract.coerce_days_past_due(raw) == loan_classification._coerce_dpd(raw)


@pytest.mark.parametrize("raw", [None, "", "1250.75", 3, "abc", " 1 "])
def test_decimal_reading_matches_the_classification_service(raw: Any) -> None:
    assert extract._dec_or_none(raw) == loan_classification._dec_or_none(raw)


def test_loan_product_family_is_the_lifted_domain_function() -> None:
    assert fact_derivation._loan_family is loan_family
    assert extract.product_family(
        "LOAN", regulatory_category="SME_RETAIL", deposit_account_type=None
    ) == (loan_family("sme_retail"))
    assert fact_derivation._unclassified_category is extract.unclassified_category


def test_the_extract_reads_the_same_wire_keys_the_services_read() -> None:
    """Each attribute key the extractor reads is one a calculation service reads."""
    services = "".join(
        (BACKEND / path).read_text(encoding="utf-8")
        for path in (
            "app/services/fact_derivation.py",
            "app/services/loan_classification.py",
            "app/services/regulatory_credit.py",
            "app/services/credit_concentration.py",
            "app/services/enterprise_stress.py",
        )
    )
    extract_source = (BACKEND / "app/domain/bi/extract.py").read_text(encoding="utf-8")
    keys = set(re.findall(r'attributes\.get\("([a-z0-9_]+)"\)', extract_source))
    assert keys, "the extractor reads no attributes?"
    for key in sorted(keys):
        assert f'"{key}"' in services, f"{key} is not an attribute any calculation service reads"
