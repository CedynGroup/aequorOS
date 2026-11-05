"""The BI domain restates a few service/model facts; these tests keep them true.

``app.bi.domain`` may not import ``app.models`` or ``app.services``, so it
carries its own copies of: the live-module vocabulary, the module → entitlement
slug map, the module → authorization-module map, the read-computed methodologies
a live module publishes, and the DPD / FX reading rules of the classification
service. Each copy is pinned here against its owner.

This module's other-suite cases live in ``test_catalogue_parity.py``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.bi.domain.authority import (
    READ_COMPUTED_LIVE_MODULES,
    TEXT_VALUED_METRIC_IDS,
)
from app.capital.service import regulatory_capital
from app.credit.service import loan_classification
from app.data_engine.domain.ingestion.optional_position_fields import (
    OPTIONAL_POSITION_ATTRIBUTE_KEYS,
)
from app.domain.authority.registry import REGISTRY
from app.live.service import fact_derivation
from app.models import Bank, BankFinancialFact, BankReportingPeriod, CanonicalPositionSnapshot
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book
from tests.support.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture
from tests.support.helpers import ORG_1, USER_1


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
