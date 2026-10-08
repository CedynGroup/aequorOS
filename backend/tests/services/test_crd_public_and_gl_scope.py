"""BoG CRD (June 2018) ¶98, ¶117–122: covered deductions and public domicile."""

from dataclasses import replace
from decimal import Decimal
from itertools import combinations

import pytest

from app.domain.capital.engine import RiskWeightUnavailable
from app.domain.stress.credit_bottom_up import compute_bottom_up_credit
from app.models import (
    CanonicalCounterparty,
    CanonicalGlAccount,
    CanonicalPosition,
    CanonicalPositionSnapshot,
)
from app.services.enterprise_stress import (
    EnterpriseStressError,
    _build_credit_exposures,  # pyright: ignore[reportPrivateUsage]
)
from app.services.fact_derivation import _position_row  # pyright: ignore[reportPrivateUsage]
from tests.domain.test_capital_engine import bog_capital_params
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

pytestmark = pytest.mark.requirement("BoG CRD (June 2018) ¶98, ¶117–122")

CLAIMS = (
    ("LOAN", "Loan", "1300", "1"),
    ("SECURITY_HOLDING", "Security", "1200", "1"),
    ("INTERBANK_PLACEMENT", "Placement", "1100", "0.5"),
)
COVERAGE_CASES = [
    (uncovered, covered)
    for uncovered in CLAIMS
    for size in (1, 2)
    for covered in combinations([claim for claim in CLAIMS if claim != uncovered], size)
]


@pytest.mark.parametrize(("uncovered", "covered"), COVERAGE_CASES)
@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize(
    ("deduction", "amount"), [("impairment", "200"), ("suspended interest", "50")]
)
def test_uncovered_gl_deductions_survive_other_credit_subledgers(
    uncovered: tuple[str, str, str, str],
    covered: tuple[tuple[str, str, str, str], ...],
    named: bool,
    deduction: str,
    amount: str,
) -> None:
    """BoG CRD (June 2018) ¶98: coverage of another claim cannot remove this deduction."""
    _, label, block, _ = uncovered
    rows = [
        _row(
            position_type,
            position_type,
            balance="1000",
            regulatory_category="CORPORATE_UNRATED",
            counterparty_type="BANK_NON_OECD"
            if position_type == "INTERBANK_PLACEMENT"
            else "CORPORATE",
        )
        for position_type, _, _, _ in covered
    ]
    accounts = [
        CanonicalGlAccount(
            account_code=block, name=label, account_class="ASSET", balance=Decimal("1000")
        ),
        CanonicalGlAccount(
            account_code="1900" if named else block,
            name=f"{label} {deduction}" if named else deduction,
            account_class="ASSET",
            balance=-Decimal(amount),
        ),
    ]
    expected = sum((Decimal("1000") * Decimal(weight) for _, _, _, weight in covered), Decimal("0"))
    assert _credit_rwa(*rows, gl_accounts=accounts) == expected + Decimal("1000") - Decimal(amount)


@pytest.mark.parametrize(
    "borrower_class,weight", [("public_institution", "0.5"), ("public_enterprise", "1")]
)
@pytest.mark.parametrize("foreign", [False, True])
@pytest.mark.parametrize("collateral", ["0", "200"])
def test_public_borrower_class_drives_capital_crm_and_stress(
    borrower_class: str, weight: str, foreign: bool, collateral: str
) -> None:
    """BoG CRD (June 2018) ¶117–119: public borrower class overrides corporate product."""
    params = replace(bog_capital_params(), crm_haircuts={"CORPORATE_DEBT": Decimal("0")})
    row = replace(
        _row(
            "PUBLIC/LOAN",
            "LOAN",
            balance="1000",
            balance_ghs="1000",
            currency="USD" if foreign else "GHS",
            counterparty_type="GOVERNMENT_ENTITY",
            regulatory_category="CORPORATE_UNRATED",
            attributes={
                "borrower_class": borrower_class,
                "issuer_class": "private",
                "crm_collateral_ghs": collateral,
                "crm_collateral_class": "corporate_debt",
            },
        ),
        counterparty_country="GH",
        counterparty_resident=True,
    )
    expected = (Decimal("1000") - Decimal(collateral)) * (
        Decimal(weight) + (Decimal("0.2") if foreign else 0)
    )
    assert _credit_rwa(row, params=params) == expected
    book = _build_credit_exposures(
        [_stress_row(row)], params, domestic_country="GH", capital_facts=_capital_facts([row])
    )
    result = compute_bottom_up_credit(
        book, pd_multiplier=Decimal("1"), lgd_multiplier=Decimal("1"), fx_fraction=Decimal("0.1")
    )
    assert result.base_credit_rwa == expected
    assert result.stressed_credit_rwa == expected * (Decimal("1.1") if foreign else 1)
    assert book[0].ead == Decimal("1000")
    assert book[0].crd_class == "public_sector_entities"


@pytest.mark.parametrize("position_type", [claim[0] for claim in CLAIMS])
@pytest.mark.parametrize("instrument", [None, "cocoa_bill", "tor_bond", "gog_bond"])
@pytest.mark.parametrize("domicile", [("NG", True), ("GH", False), (None, False)])
def test_foreign_public_claim_refuses_domestic_preferences(
    position_type: str, instrument: str | None, domicile: tuple[str | None, bool]
) -> None:
    """BoG CRD (June 2018) ¶120–122: foreign PSEs require their sovereign assessment."""
    attributes = {
        "borrower_class" if position_type == "LOAN" else "issuer_class": "public_institution"
    }
    if instrument is not None:
        attributes["instrument"] = instrument
    country, resident = domicile
    row = replace(
        _row(
            "FOREIGN/PUBLIC",
            position_type,
            balance="1000",
            balance_ghs="1000",
            currency="USD",
            counterparty_type="GOVERNMENT_ENTITY",
            regulatory_category="CORPORATE_UNRATED",
            attributes=attributes,
        ),
        counterparty_country=country,
        counterparty_resident=resident,
    )
    with pytest.raises(RiskWeightUnavailable):
        _credit_rwa(row)
    with pytest.raises(EnterpriseStressError):
        _build_credit_exposures(
            [_stress_row(row)], bog_capital_params(), domestic_country="GH", capital_facts=()
        )


@pytest.mark.parametrize("position_type", [claim[0] for claim in CLAIMS])
@pytest.mark.parametrize("country,resident", [("GH", None), (None, True)])
def test_domestic_public_domicile_preserves_foreign_currency_addon(
    position_type: str, country: str | None, resident: bool | None
) -> None:
    """BoG CRD (June 2018) ¶117–119: domicile and claim currency are separate axes."""
    row = replace(
        _row(
            "DOMESTIC/PUBLIC",
            position_type,
            currency="USD",
            balance="1000",
            balance_ghs="1000",
            counterparty_type="GOVERNMENT_ENTITY",
            regulatory_category="CORPORATE_UNRATED",
            attributes={
                "borrower_class"
                if position_type == "LOAN"
                else "issuer_class": "public_institution"
            },
        ),
        counterparty_country=country,
        counterparty_resident=resident,
    )
    assert _credit_rwa(row) == Decimal("700")
    book = _build_credit_exposures(
        [_stress_row(row)],
        bog_capital_params(),
        domestic_country="GH",
        capital_facts=_capital_facts([row]),
    )
    assert book[0].risk_weight_pct == Decimal("70")


def test_missing_public_domicile_refuses_unestablished_preference() -> None:
    """BoG CRD (June 2018) ¶117–122: a class label alone cannot establish domicile."""
    row = _row(
        "UNKNOWN/PUBLIC",
        "SECURITY_HOLDING",
        balance="1000",
        attributes={"issuer_class": "public_institution"},
    )
    with pytest.raises(RiskWeightUnavailable):
        _credit_rwa(row)


@pytest.mark.parametrize("position_type", [claim[0] for claim in CLAIMS])
def test_flattened_counterparty_domicile_reaches_capital_refusal(position_type: str) -> None:
    """BoG CRD (June 2018) ¶120–122: canonical typed domicile reaches the classifier."""
    row = _position_row(
        CanonicalPositionSnapshot(
            balance=Decimal("1000"),
            source_reference="FOREIGN",
            source_system="API_PUSH",
            attributes={
                "borrower_class"
                if position_type == "LOAN"
                else "issuer_class": "public_institution"
            },
        ),
        CanonicalPosition(position_type=position_type, currency="GHS"),
        None,
        CanonicalCounterparty(
            counterparty_type="GOVERNMENT_ENTITY", country_code="NG", resident=False
        ),
        "GHS",
    )
    assert row.counterparty_country == "NG"
    assert row.counterparty_resident is False
    with pytest.raises(RiskWeightUnavailable):
        _credit_rwa(row)


@pytest.mark.parametrize(
    "attributes", [{"borrower_class": "unknown"}, {"issuer_class": "public_institution"}]
)
def test_public_loan_requires_the_closed_borrower_class(attributes: dict[str, str]) -> None:
    """BoG CRD (June 2018) ¶117–118: unknown or wrong-field classes grant no preference."""
    row = replace(
        _row(
            "PUBLIC/UNKNOWN",
            "LOAN",
            balance="1000",
            counterparty_type="GOVERNMENT_ENTITY",
            regulatory_category="CORPORATE_UNRATED",
            attributes=attributes,
        ),
        counterparty_country="GH",
        counterparty_resident=True,
    )
    with pytest.raises(RiskWeightUnavailable):
        _credit_rwa(row)


def test_domicile_uses_the_supplied_jurisdiction_instead_of_a_country_literal() -> None:
    """BoG CRD (June 2018) ¶117–122: jurisdiction is governed input, not a literal."""
    row = replace(
        _row(
            "PUBLIC/JURISDICTION",
            "LOAN",
            balance="1000",
            counterparty_type="GOVERNMENT_ENTITY",
            regulatory_category="CORPORATE_UNRATED",
            attributes={"borrower_class": "public_institution"},
        ),
        counterparty_country="KE",
        counterparty_resident=None,
    )
    book = _build_credit_exposures(
        [_stress_row(row)], bog_capital_params(), domestic_country="KE", capital_facts=()
    )
    assert book[0].risk_weight_pct == Decimal("50")
    with pytest.raises(EnterpriseStressError):
        _build_credit_exposures(
            [_stress_row(row)], bog_capital_params(), domestic_country="GH", capital_facts=()
        )


@pytest.mark.parametrize("foreign", [False, True])
def test_typed_sovereign_loan_with_central_government_borrower_retains_weight(
    foreign: bool,
) -> None:
    """BoG CRD (June 2018) ¶106–107: a central-government borrower is not a PSE."""
    row = replace(
        _row(
            "SOVEREIGN/LOAN",
            "LOAN",
            balance="1000",
            balance_ghs="1000",
            currency="USD" if foreign else "GHS",
            counterparty_type="SOVEREIGN",
            regulatory_category="CORPORATE_UNRATED",
            attributes={"borrower_class": "central_government", "instrument": "gog_bond"},
        ),
        counterparty_country="GH",
        counterparty_resident=True,
    )
    assert _credit_rwa(row) == Decimal("200" if foreign else "0")
    book = _build_credit_exposures(
        [_stress_row(row)], bog_capital_params(), domestic_country="GH", capital_facts=()
    )
    assert book[0].risk_weight_pct == Decimal("20" if foreign else "0")
