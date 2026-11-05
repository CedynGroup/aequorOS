"""BoG CRD (June 2018) ¶98, ¶117–124: public metadata and bank debt claims.

Primary authority:
https://www.bog.gov.gh/wp-content/uploads/2022/05/Basel-II-BOG-CRD-Final-27-June-2018-Basel-Committee-BSD.pdf

This module's other-suite cases live in ``test_crd_counterparty_and_bank_claims.py``.
"""

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.capital.domain.engine import RiskWeightUnavailable
from app.credit.service.credit_exposure_book import load_exposure_rows
from app.models import (
    Bank,
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
)
from app.stress.service.enterprise_stress import EnterpriseStressError
from tests.capital.domain.test_capital_engine import bog_capital_params
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID
from tests.fixtures.capital_structure import MAKER, REPORTING_DATE, seed_book
from tests.services.test_crd_counterparty_and_bank_claims import (
    _build_credit_exposures,
    _capital_facts,
    _credit_rwa,
    _position_row,
)

pytestmark = pytest.mark.requirement("BoG CRD (June 2018) ¶98, ¶117–124")


@pytest.mark.parametrize("position_type", ["LOAN", "SECURITY_HOLDING", "INTERBANK_PLACEMENT"])
@pytest.mark.parametrize("override", ["absent", "public_enterprise", "unknown", None, ""])
@pytest.mark.parametrize("foreign_domicile", [False, True])
def test_counterparty_public_classes_reach_both_flatteners_with_position_precedence(
    db_session: Session, position_type: str, override: str | None, foreign_domicile: bool
) -> None:
    """BoG CRD (June 2018) ¶117–122: inherited public classes retain precedence and domicile."""
    seed_book(db_session)
    snapshot = db_session.scalar(
        select(CanonicalPositionSnapshot).where(
            CanonicalPositionSnapshot.source_reference == "LOAN/1"
        )
    )
    assert snapshot is not None
    position = db_session.get(CanonicalPosition, snapshot.position_id)
    counterparty = db_session.get(CanonicalCounterparty, snapshot.counterparty_id)
    product = db_session.get(CanonicalProduct, snapshot.product_id)
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert (
        position is not None
        and counterparty is not None
        and product is not None
        and bank is not None
    )
    key = "borrower_class" if position_type == "LOAN" else "issuer_class"
    counterparty.counterparty_type = "GOVERNMENT_ENTITY"
    counterparty.country_code = "NG" if foreign_domicile else "GH"
    counterparty.resident = not foreign_domicile
    counterparty.attributes = {key: "public_institution", "specific_provision_ghs": "999"}
    position.position_type = position_type
    position.currency = "GHS"
    product.regulatory_category = "CORPORATE_UNRATED"
    snapshot.balance = Decimal("1000")
    snapshot.ifrs9_stage = 1
    snapshot.attributes = {} if override == "absent" else {key: override}
    db_session.flush()

    original_position_attributes = dict(snapshot.attributes)
    original_counterparty_attributes = dict(counterparty.attributes)
    row = _position_row(snapshot, position, product, counterparty, "GHS")
    exposure = next(
        row
        for row in load_exposure_rows(db_session, MAKER, bank, REPORTING_DATE, (position_type,))
        if row.source_reference == snapshot.source_reference
    )
    params = bog_capital_params()
    if foreign_domicile or override not in ("absent", "public_enterprise"):
        with pytest.raises(RiskWeightUnavailable):
            _credit_rwa(row)
        with pytest.raises(EnterpriseStressError):
            _build_credit_exposures(
                [exposure], params, domestic_country=bank.jurisdiction_code, capital_facts=()
            )
    else:
        expected = Decimal("500" if override == "absent" else "1000")
        assert _credit_rwa(row) == expected
        book = _build_credit_exposures(
            [exposure],
            params,
            domestic_country=bank.jurisdiction_code,
            capital_facts=_capital_facts([row]),
        )
        assert book[0].risk_weight_pct == expected / Decimal("10")
        assert book[0].ead == book[0].credit_amount == Decimal("1000")
        assert book[0].crd_class == "public_sector_entities"
    assert snapshot.attributes == original_position_attributes
    assert counterparty.attributes == original_counterparty_attributes
