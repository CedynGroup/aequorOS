"""The BI domain restates a few service/model facts; these tests keep them true.

``app.bi.domain`` may not import ``app.models`` or ``app.services``, so it
carries its own copies of: the live-module vocabulary, the module → entitlement
slug map, the module → authorization-module map, the read-computed methodologies
a live module publishes, and the DPD / FX reading rules of the classification
service. Each copy is pinned here against its owner.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.bi.domain import extract
from app.bi.domain.authority import (
    AUTHORIZATION_MODULE,
    ENGINE_MODULES,
    ENTITLEMENT_SLUG,
    READ_COMPUTED_LIVE_MODULES,
    TEXT_VALUED_METRIC_IDS,
    engine_authorities,
    engine_module_for,
)
from app.core.authorization import Module
from app.credit.service import loan_classification
from app.domain.authority.registry import REGISTRY
from app.domain.ingestion.optional_position_fields import OPTIONAL_POSITION_ATTRIBUTE_KEYS
from app.domain.positions.families import loan_family
from app.models import Bank, BankFinancialFact, BankReportingPeriod, CanonicalPositionSnapshot
from app.models.live import LIVE_MODULES
from app.policy.service import module_scope
from app.services import fact_derivation, pipeline, regulatory_capital
from tests.bi.domain.test_extract import Position, Snapshot
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book
from tests.support.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture
from tests.support.helpers import ORG_1, USER_1


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


def assert_published_metrics(module: str, metrics: Mapping[str, object]) -> None:
    methodologies = {
        methodology_id
        for methodology_id, publisher in READ_COMPUTED_LIVE_MODULES.items()
        if publisher == module
    }
    expected = {
        entry.metric_id
        for entry in REGISTRY
        if entry.methodology_id in methodologies and entry.is_primary
    }
    assert expected
    assert expected <= metrics.keys()
    if module == "rating":
        for metric_id in TEXT_VALUED_METRIC_IDS:
            value = metrics[metric_id]
            assert isinstance(value, str) and value
            with pytest.raises(InvalidOperation):
                Decimal(value)


@pytest.mark.parametrize(("methodology_id", "module"), sorted(READ_COMPUTED_LIVE_MODULES.items()))
def test_read_computed_methodologies_resolve_to_the_named_live_module(
    methodology_id: str, module: str
) -> None:
    entries = [
        entry for entry in REGISTRY if entry.methodology_id == methodology_id and entry.is_primary
    ]
    assert entries
    assert module in ENGINE_MODULES
    for entry in entries:
        assert entry.authoritative_run_type is None
        assert engine_module_for(entry) == module
        assert entry in engine_authorities()


@pytest.mark.parametrize("metric_id", sorted(TEXT_VALUED_METRIC_IDS))
def test_text_metrics_are_registered_and_projected_without_a_numeric_value(metric_id: str) -> None:
    assert REGISTRY.for_metric(metric_id)
    row = extract.engine_metric_row(
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
        as_of_date=FIXTURE_AS_OF,
        module="rating",
        metric_id=metric_id,
        raw_value="b-",
        tier="live",
        regime="crd",
        institution_class="bank",
        status="green",
        input_hash=None,
        engine_version=None,
        pipeline_state=None,
        reconciliation_blocked=False,
        run_id=None,
        reporting_period_id=None,
        computed_at=None,
    )
    assert row.value is None
    assert row.unit == "text"


def test_sdi_capital_publishes_the_registered_read_computed_metrics(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    bank.institution_type = "savings_and_loans"
    bank.license_type = "savings_and_loans"
    seed_canonical_fixture(db_session, organization_id=ORG_1, bank_id=bank.id)
    ctx = TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1)
    result = fact_derivation.derive_facts(db_session, ctx, bank.id, FIXTURE_AS_OF)
    period = db_session.get(BankReportingPeriod, result.reporting_period_id)
    assert period is not None
    fact_derivation.derive_current_facts(db_session, ctx, bank.id, FIXTURE_AS_OF)
    live = regulatory_capital.compute_live(db_session, ctx, bank, period)
    assert live.input_hash
    assert_published_metrics("capital", live.metrics)


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


@pytest.mark.parametrize(
    ("key", "raw", "field", "expected"),
    [
        ("balance_ghs", "123", "balance_rc", Decimal("123")),
        ("notional_ghs", "200", "notional_rc", Decimal("200")),
        ("days_past_due", 45, "days_past_due", 45),
        ("ecl_provision_ghs", "12", "provision_held_rc", Decimal("12")),
        ("interest_in_suspense_ghs", "3", "interest_in_suspense_rc", Decimal("3")),
        ("crm_collateral_ghs", "90", "collateral_rc", Decimal("90")),
        ("collateral_type", "cash", "collateral_type", "cash"),
        ("crm_collateral_class", "cash", "collateral_type", "cash"),
        ("restructured", True, "restructured", True),
        ("hqla_level", "LEVEL_1", "hqla_level", "LEVEL_1"),
        ("branch_id", "B001", "branch_code", "B001"),
        ("officer_id", "O001", "officer_id", "O001"),
        ("channel", "branch", "channel", "branch"),
        ("account_status", "active", "account_status", "active"),
        ("arrears_amount", "10", "arrears_amount_rc", Decimal("10")),
        ("sector", "trade", "sector", "trade"),
        ("industry", "trade", "sector", "trade"),
        ("employer", "EMP001", "employer", "EMP001"),
    ],
)
def test_ingested_attribute_values_reach_the_mart_without_inventing_missing_values(
    key: str, raw: object, field: str, expected: object
) -> None:
    snapshot = Snapshot(balance=Decimal("100"))
    position = Position(position_type="LOAN", currency="XRC")
    empty = extract.position_row(
        snapshot, position, None, None, None, base_currency="XRC", classified=None
    )
    projected = extract.position_row(
        replace(snapshot, attributes={key: raw}),
        position,
        None,
        None,
        None,
        base_currency="XRC",
        classified=None,
    )
    assert getattr(projected, field) == expected
    if key != "balance_ghs":
        assert getattr(empty, field) is None


def test_unknown_attributes_do_not_change_the_projection() -> None:
    snapshot = Snapshot(balance=Decimal("100"))
    position = Position(position_type="LOAN", currency="XRC")
    empty = extract.position_row(
        snapshot, position, None, None, None, base_currency="XRC", classified=None
    )
    unknown = extract.position_row(
        replace(snapshot, attributes={"invented_wire_key": "500"}),
        position,
        None,
        None,
        None,
        base_currency="XRC",
        classified=None,
    )
    assert unknown == empty


def test_optional_analytics_attributes_do_not_change_official_facts_or_classifications(
    db_session: Session,
) -> None:
    materialize_canonical_test_book(db_session)
    seed_canonical_fixture(db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    ctx = TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1)

    def values() -> set[tuple[object, ...]]:
        result = fact_derivation.derive_facts(db_session, ctx, bank.id, FIXTURE_AS_OF)
        return {
            (
                fact.fact_group,
                fact.category,
                fact.amount,
                fact.currency,
                fact.risk_weight_code,
                fact.hqla_level,
                fact.ccf_pct,
                fact.rate_pct,
                fact.income_year,
                fact.capital_tier,
                fact.is_deduction,
                json.dumps(fact.attributes, sort_keys=True),
            )
            for fact in db_session.scalars(
                select(BankFinancialFact).where(
                    BankFinancialFact.bank_id == bank.id,
                    BankFinancialFact.reporting_period_id == result.reporting_period_id,
                )
            )
        }

    original = values()
    classified = loan_classification.classified_loans(
        db_session, ctx, bank, FIXTURE_AS_OF, record=False
    )
    assert original
    assert classified
    attributes = {
        "officer_id": "O001",
        "channel": "branch",
        "account_status": "active",
        "arrears_amount": "10",
    }
    assert set(attributes) == set(OPTIONAL_POSITION_ATTRIBUTE_KEYS)
    for snapshot in db_session.scalars(
        select(CanonicalPositionSnapshot).where(
            CanonicalPositionSnapshot.bank_id == bank.id,
        )
    ):
        snapshot.attributes = {**(snapshot.attributes or {}), **attributes}
    db_session.flush()
    assert values() == original
    assert (
        loan_classification.classified_loans(db_session, ctx, bank, FIXTURE_AS_OF, record=False)
        == classified
    )
