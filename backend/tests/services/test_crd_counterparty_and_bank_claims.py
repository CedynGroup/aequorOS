"""BoG CRD (June 2018) ¶98, ¶117–124: public metadata and bank debt claims.

Primary authority:
https://www.bog.gov.gh/wp-content/uploads/2022/05/Basel-II-BOG-CRD-Final-27-June-2018-Basel-Committee-BSD.pdf
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.capital.engine import (
    RiskWeightUnavailable,
    _credit_line_items,  # pyright: ignore[reportPrivateUsage]
)
from app.domain.stress.appendix_ii import _crd_class  # pyright: ignore[reportPrivateUsage]
from app.models import (
    Bank,
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
)
from app.services.credit_exposure_book import load_exposure_rows
from app.services.enterprise_stress import (
    EnterpriseStressError,
    _build_credit_exposures,  # pyright: ignore[reportPrivateUsage]
)
from app.services.fact_derivation import _position_row  # pyright: ignore[reportPrivateUsage]
from tests.domain.test_capital_engine import bog_capital_params
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID
from tests.fixtures.capital_structure import MAKER, REPORTING_DATE, seed_book
from tests.services.test_crd_credit_exposures import (
    _credit_rwa,  # pyright: ignore[reportPrivateUsage]
    _stress_row,  # pyright: ignore[reportPrivateUsage]
)
from tests.services.test_crd_credit_stress_repairs import (
    _capital_facts,  # pyright: ignore[reportPrivateUsage]
)
from tests.services.test_derivation_fail_closed_defaults import (
    _row,  # pyright: ignore[reportPrivateUsage]
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


BANK_CASES = [
    (None, None, date(2026, 7, 1), False, "500"),
    ("1", date(2025, 1, 1), date(2027, 1, 1), False, "200"),
    ("2", date(2025, 1, 1), date(2027, 1, 1), False, "500"),
    ("6", date(2025, 1, 1), date(2027, 1, 1), False, "1500"),
    (None, date(2026, 4, 1), date(2026, 7, 1), False, "200"),
    ("2", date(2026, 4, 1), date(2026, 7, 1), False, "200"),
    ("6", date(2026, 4, 1), date(2026, 7, 1), False, "1500"),
    (None, date(2026, 4, 1), date(2026, 7, 1), True, "500"),
    ("2", date(2026, 4, 1), date(2026, 7, 1), True, "500"),
    ("6", date(2025, 1, 1), date(2027, 1, 1), True, "1500"),
    ("7", date(2026, 4, 1), date(2026, 7, 1), False, None),
    ("invalid", None, None, True, None),
]


@pytest.mark.parametrize("counterparty", ["BANK_OECD", "BANK_NON_OECD"])
@pytest.mark.parametrize("assessment", BANK_CASES)
def test_bank_debt_securities_share_the_bank_assessment(
    counterparty: str,
    assessment: tuple[str | None, date | None, date | None, bool, str | None],
) -> None:
    """BoG CRD (June 2018) ¶123–124: bank debt uses ERG and original domestic term."""
    grade, origination, maturity, foreign, expected = assessment
    attributes = {"instrument": "certificate_of_deposit"}
    if grade is not None:
        attributes["external_rating_grade"] = grade
    row = replace(
        _row(
            "BANK/CD",
            "SECURITY_HOLDING",
            balance="1000",
            balance_ghs="1000",
            currency="USD" if foreign else "GHS",
            counterparty_type=counterparty,
            maturity=maturity,
            attributes=attributes,
        ),
        origination_date=origination,
    )
    params = bog_capital_params()
    if expected is None:
        with pytest.raises(RiskWeightUnavailable):
            _credit_rwa(row)
        with pytest.raises(EnterpriseStressError):
            _build_credit_exposures([_stress_row(row)], params, capital_facts=())
    else:
        assert _credit_rwa(row) == Decimal(expected)
        book = _build_credit_exposures(
            [_stress_row(row)], params, capital_facts=_capital_facts([row])
        )
        assert book[0].risk_weight_pct == Decimal(expected) / Decimal("10")
        assert book[0].ead == Decimal("1000")
        assert book[0].crd_class == "banks"
        line = next(
            line
            for line in _credit_line_items(_capital_facts([row]), params)
            if line.weighted_amount > 0
        )
        assert _crd_class(line) == "banks"


def test_bank_security_specific_deduction_preserves_gross_expected_loss() -> None:
    """BoG CRD (June 2018) ¶98, ¶123: specific provision nets bank debt once."""
    row = _row(
        "BANK/NET",
        "SECURITY_HOLDING",
        balance="1000",
        counterparty_type="BANK_NON_OECD",
        regulatory_category="CORPORATE_BOND",
        attributes={"external_rating_grade": "6", "specific_provision_ghs": "200"},
    )
    assert _credit_rwa(row) == Decimal("1200")
    book = _build_credit_exposures(
        [_stress_row(row)], bog_capital_params(), capital_facts=_capital_facts([row])
    )
    assert book[0].ead == Decimal("1000")
    assert book[0].credit_amount == Decimal("800")


def test_inherited_public_borrower_retains_specific_deduction_and_shared_crm() -> None:
    """BoG CRD (June 2018) ¶98, ¶117: inherited public class shares net measurement and CRM."""
    row = _position_row(
        CanonicalPositionSnapshot(
            source_reference="PUBLIC/CRM",
            source_system="API_PUSH",
            balance=Decimal("1000"),
            ifrs9_stage=1,
            attributes={
                "specific_provision_ghs": "200",
                "crm_collateral_ghs": "200",
                "crm_collateral_class": "corporate_debt",
            },
        ),
        CanonicalPosition(position_type="LOAN", currency="GHS"),
        CanonicalProduct(product_code="LN.CORPORATE", regulatory_category="CORPORATE_UNRATED"),
        CanonicalCounterparty(
            counterparty_type="GOVERNMENT_ENTITY",
            country_code="GH",
            resident=True,
            attributes={"borrower_class": "public_institution"},
        ),
        "GHS",
    )
    params = replace(bog_capital_params(), crm_haircuts={"CORPORATE_DEBT": Decimal("0")})
    assert _credit_rwa(row, params=params) == Decimal("300")
    book = _build_credit_exposures(
        [_stress_row(row)], params, domestic_country="GH", capital_facts=_capital_facts([row])
    )
    assert book[0].ead == Decimal("1000")
    assert book[0].credit_amount == Decimal("800")
    assert book[0].collateral_amount == Decimal("200")


@pytest.mark.parametrize("counterparty", ["BANK_OECD", "BANK_NON_OECD"])
@pytest.mark.parametrize("foreign", [False, True])
@pytest.mark.parametrize("grade", [None, "6", "invalid"])
@pytest.mark.parametrize(
    "holding",
    [
        ("EQUITY_GSE_LISTED", None),
        ("EQUITY_GSE_LISTED", "certificate_of_deposit"),
        ("EQUITY_UNLISTED", None),
        (None, "equity"),
        (None, "unknown"),
        ("OTHER_SECURITY", None),
    ],
)
def test_bank_equity_and_unknown_holdings_keep_securities_fallback(
    counterparty: str, foreign: bool, grade: str | None, holding: tuple[str | None, str | None]
) -> None:
    """BoG CRD (June 2018) ¶123–124: bank debt preference cannot cover non-debt holdings."""
    category, instrument = holding
    attributes: dict[str, str] = {}
    if grade is not None:
        attributes["external_rating_grade"] = grade
    if instrument is not None:
        attributes["instrument"] = instrument
    row = _row(
        "BANK/NONDEBT",
        "SECURITY_HOLDING",
        balance="1000",
        balance_ghs="1000",
        currency="USD" if foreign else "GHS",
        counterparty_type=counterparty,
        regulatory_category=category,
        attributes=attributes,
    )
    assert _credit_rwa(row) == Decimal("1000")
    book = _build_credit_exposures(
        [_stress_row(row)], bog_capital_params(), capital_facts=_capital_facts([row])
    )
    assert book[0].risk_weight_pct == Decimal("100")
    assert book[0].credit_category == "securities:other_securities:RW100"
    assert book[0].ead == book[0].credit_amount == Decimal("1000")


@pytest.mark.parametrize("category", ["BOND", "CORPORATE_BOND"])
@pytest.mark.parametrize("counterparty", ["BANK_OECD", "BANK_NON_OECD"])
@pytest.mark.parametrize("foreign", [False, True])
def test_bank_bond_product_establishes_debt_without_instrument_override(
    category: str, counterparty: str, foreign: bool
) -> None:
    """BoG CRD (June 2018) ¶123: typed bond products establish bank debt claims."""
    row = _row(
        "BANK/BOND",
        "SECURITY_HOLDING",
        balance="1000",
        balance_ghs="1000",
        currency="USD" if foreign else "GHS",
        counterparty_type=counterparty,
        regulatory_category=category,
        attributes={"external_rating_grade": "6"},
    )
    assert _credit_rwa(row) == Decimal("1500")
    book = _build_credit_exposures(
        [_stress_row(row)], bog_capital_params(), capital_facts=_capital_facts([row])
    )
    assert book[0].risk_weight_pct == Decimal("150")
    assert book[0].credit_category == "securities:banks:RW150"
