"""BoG CRD (June 2018) Part 2 capital exposure regressions."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from app.domain.capital.engine import (
    CapitalFact,
    CapitalParams,
    RiskWeightUnavailable,
    _credit_line_items,  # pyright: ignore[reportPrivateUsage]
)
from app.domain.stress.appendix_ii import _crd_class  # pyright: ignore[reportPrivateUsage]
from app.domain.stress.credit_bottom_up import compute_bottom_up_credit
from app.domain.stress.translation import MacroPathPoint
from app.models import CanonicalGlAccount
from app.services import enterprise_stress
from app.services.credit_exposure_book import ExposureRow
from app.services.fact_derivation import (
    DerivationError,
    _derive_specs,  # pyright: ignore[reportPrivateUsage]
    _PositionRow,  # pyright: ignore[reportPrivateUsage]
    _split_securities,  # pyright: ignore[reportPrivateUsage]
)
from tests.domain.stress_fixtures import base_paths
from tests.domain.test_capital_engine import bog_capital_params
from tests.services.test_derivation_fail_closed_defaults import (
    _canonical,  # pyright: ignore[reportPrivateUsage]
    _row,  # pyright: ignore[reportPrivateUsage]
)

pytestmark = pytest.mark.requirement("BoG CRD (June 2018) Part 2")


def _credit_rwa(
    *rows: _PositionRow,
    gl_accounts: list[CanonicalGlAccount] | None = None,
    params: CapitalParams | None = None,
) -> Decimal:
    canonical = replace(_canonical(*rows), gl_accounts=gl_accounts or [])
    specs, _, _ = _derive_specs(canonical, live=True)
    facts = [
        CapitalFact(
            fact_group=spec.fact_group,
            category=spec.category,
            amount=spec.amount,
            risk_weight_code=spec.risk_weight_code,
            ccf_pct=spec.ccf_pct,
        )
        for spec in specs
    ]
    return sum(
        (
            item.weighted_amount
            for item in _credit_line_items(facts, params or bog_capital_params())
        ),
        Decimal("0"),
    )


def test_gog_foreign_currency_claim_weighted_20pct() -> None:
    """BoG CRD (June 2018) ¶107: GoG foreign-currency claims carry 20%."""
    row = _row(
        "GOG/1",
        "SECURITY_HOLDING",
        currency="USD",
        balance="100",
        balance_ghs="1000",
        attributes={"instrument": "gog_bond"},
    )
    assert _credit_rwa(row) == Decimal("200")


@pytest.mark.parametrize(("instrument", "issuer_role"), [("gog_bond", "gog"), ("bog_bill", "bog")])
def test_domestic_sovereign_credit_retains_its_crd_stress_class(
    instrument: str, issuer_role: str
) -> None:
    """BoG CRD (June 2018) ¶106–107: retain the issuer class on weighted claims."""
    row = _row(
        "DOMESTIC/FX",
        "SECURITY_HOLDING",
        currency="USD",
        balance="1000",
        balance_ghs="1000",
        attributes={"instrument": instrument},
    )
    specs, _, _ = _derive_specs(_canonical(row), live=True)
    facts = [
        CapitalFact(spec.fact_group, spec.category, spec.amount, spec.risk_weight_code)
        for spec in specs
        if spec.fact_group == "credit_exposure"
    ]
    line = next(
        line for line in _credit_line_items(facts, bog_capital_params()) if line.weighted_amount > 0
    )
    assert line.weighted_amount == Decimal("200")
    assert _crd_class(line) == issuer_role


@pytest.mark.parametrize(
    ("instrument", "issuer_class", "expected"),
    [("tor_bond", "public_enterprise", "1000"), ("cocoa_bill", "public_institution", "500")],
)
def test_pse_paper_has_its_own_weight(instrument: str, issuer_class: str, expected: str) -> None:
    """BoG CRD (June 2018) ¶117–118, Annex 2D: TOR 100%; Cocobod 50%."""
    assert _credit_rwa(
        _row(
            "PSE/1",
            "SECURITY_HOLDING",
            balance="1000",
            attributes={"instrument": instrument, "issuer_class": issuer_class},
        )
    ) == Decimal(expected)


@pytest.mark.parametrize("issuer_class", ["private", "anything"])
def test_unknown_issuer_class_is_not_sovereign(issuer_class: str) -> None:
    """BoG CRD (June 2018) ¶106–118: arbitrary issuer labels confer no exemption."""
    assert _credit_rwa(
        _row(
            "PRIVATE/1",
            "SECURITY_HOLDING",
            balance="1000",
            attributes={"issuer_class": issuer_class},
        )
    ) == Decimal("1000")


@pytest.mark.parametrize("category", ["SME_UNRATED", "SME_RETAIL"])
def test_sme_claim_weighted_100pct(category: str) -> None:
    """BoG CRD (June 2018) ¶139: SME claims are 100%, including business retail."""
    assert _credit_rwa(
        _row("SME/1", "LOAN", balance="1000", regulatory_category=category)
    ) == Decimal("1000")


def test_unrated_interbank_claim_weighted_50pct() -> None:
    """BoG CRD (June 2018) ¶123: an unrated bank claim is 50%."""
    assert _credit_rwa(_row("BANK/1", "INTERBANK_PLACEMENT", balance="1000")) == Decimal("500")


def test_collateral_recognition_survives_the_sme_currency_weight_split() -> None:
    """BoG CRD (June 2018) ¶139: preserve existing CRM across the new weight split."""
    params = replace(bog_capital_params(), crm_haircuts={"CORPORATE_DEBT": Decimal("0")})
    domestic = _row(
        "SME/LOCAL",
        "LOAN",
        balance="1000",
        regulatory_category="SME_UNRATED",
        attributes={"crm_collateral_ghs": "1500", "crm_collateral_class": "corporate_debt"},
    )
    foreign = _row(
        "SME/FX",
        "LOAN",
        currency="USD",
        balance="1000",
        balance_ghs="1000",
        regulatory_category="SME_UNRATED",
    )
    # The existing family-level pool is used once: 1,000 local, then 500 FX.
    assert _credit_rwa(domestic, foreign, params=params) == Decimal("600")


def test_bank_loan_collateral_follows_the_counterparty_credit_category() -> None:
    """BoG CRD (June 2018) ¶123: preserve existing CRM on a reclassified bank loan."""
    params = replace(bog_capital_params(), crm_haircuts={"CORPORATE_DEBT": Decimal("0")})
    loan = replace(
        _row(
            "BANK/LOAN",
            "LOAN",
            balance="1000",
            regulatory_category="CORPORATE_UNRATED",
            attributes={"crm_collateral_ghs": "200", "crm_collateral_class": "corporate_debt"},
        ),
        counterparty_type="BANK_NON_OECD",
    )
    assert _credit_rwa(loan, params=params) == Decimal("400")


def test_specific_provisions_and_suspense_reduce_credit_exposure_only() -> None:
    """BoG CRD (June 2018) ¶98: deduct specific provisions and suspended interest."""
    row = replace(
        _row(
            "NPL/1",
            "LOAN",
            balance="1000",
            regulatory_category="CORPORATE_UNRATED",
            attributes={"ecl_provision_ghs": "200", "interest_in_suspense_ghs": "50"},
        ),
        ifrs9_stage=3,
    )
    assert _credit_rwa(row) == Decimal("1125")
    specs, _, _ = _derive_specs(_canonical(row), live=True)
    assert next(spec.amount for spec in specs if spec.fact_group == "loan_exposure") == Decimal(
        "1000"
    )
    assert next(spec.amount for spec in specs if spec.fact_group == "ecl_exposure") == Decimal(
        "1000"
    )


@pytest.mark.parametrize(
    ("currency", "origination", "maturity", "expected"),
    [
        ("GHS", "2026-04-30", "2026-07-30", "200"),
        ("GHS", "2025-01-01", "2026-07-30", "500"),
        ("USD", "2026-04-30", "2026-07-30", "500"),
    ],
)
def test_interbank_short_weight_requires_domestic_original_maturity(
    currency: str,
    origination: str,
    maturity: str,
    expected: str,
) -> None:
    """BoG CRD (June 2018) ¶124: three months ORIGINAL, in domestic currency."""
    row = replace(
        _row(
            "BANK/1",
            "INTERBANK_PLACEMENT",
            currency=currency,
            balance="1000",
            balance_ghs="1000",
            maturity=date.fromisoformat(maturity),
        ),
        origination_date=date.fromisoformat(origination),
    )
    assert _credit_rwa(row) == Decimal(expected)


@pytest.mark.parametrize(("grade", "expected"), [("1", "200"), ("2", "500"), ("6", "1500")])
def test_interbank_rating_grade_is_not_ignored(grade: str, expected: str) -> None:
    """BoG CRD (June 2018) ¶123: use the external rating grade table."""
    assert _credit_rwa(
        _row(
            "BANK/1",
            "INTERBANK_PLACEMENT",
            balance="1000",
            attributes={"external_rating_grade": grade},
        )
    ) == Decimal(expected)


def test_foreign_pse_has_twenty_point_addon() -> None:
    """BoG CRD (June 2018) ¶119: foreign-currency PSE institution is 50% + 20%."""
    assert _credit_rwa(
        _row(
            "PSE/1",
            "SECURITY_HOLDING",
            currency="USD",
            balance="1000",
            balance_ghs="1000",
            attributes={"issuer_class": "public_institution"},
        )
    ) == Decimal("700")


def test_general_provisions_do_not_reduce_performing_exposure() -> None:
    """BoG CRD (June 2018) ¶98: only SPECIFIC provisions net the exposure."""
    row = replace(
        _row(
            "LOAN/1",
            "LOAN",
            balance="1000",
            regulatory_category="CORPORATE_UNRATED",
            attributes={"ecl_provision_ghs": "200"},
        ),
        ifrs9_stage=2,
    )
    assert _credit_rwa(row) == Decimal("1000")


def test_credit_basis_counts_each_asset_once() -> None:
    """BoG CRD (June 2018) ¶98: do not also weight gross summary balances."""
    assert _credit_rwa(
        _row(
            "GOG/1",
            "SECURITY_HOLDING",
            currency="USD",
            balance="1000",
            balance_ghs="1000",
            attributes={"instrument": "gog_bond"},
        ),
        _row("PSE/1", "SECURITY_HOLDING", balance="1000", attributes={"instrument": "tor_bond"}),
        _row("BANK/1", "INTERBANK_PLACEMENT", balance="1000"),
        _row("SME/1", "LOAN", balance="1000", regulatory_category="SME_UNRATED"),
    ) == Decimal("2700")


@pytest.mark.parametrize("amount", ["-1", "nan", "Infinity", "invalid"])
def test_invalid_provision_never_grants_a_deduction(amount: str) -> None:
    """BoG CRD (June 2018) ¶98: an invalid provision cannot establish net exposure."""
    with pytest.raises(DerivationError, match="provisions"):
        _credit_rwa(
            _row(
                "LOAN/1",
                "LOAN",
                balance="1000",
                regulatory_category="SME_UNRATED",
                attributes={"specific_provision_ghs": amount},
            )
        )


def test_unknown_interbank_rating_refuses_capital() -> None:
    """BoG CRD (June 2018) ¶101–105, ¶123: an unrecognised assessment is not unrated."""
    with pytest.raises(RiskWeightUnavailable):
        _credit_rwa(
            _row(
                "BANK/1",
                "INTERBANK_PLACEMENT",
                balance="1000",
                attributes={"external_rating_grade": "AAA"},
            )
        )


def test_unconverted_credit_exposure_refuses_capital() -> None:
    """BoG CRD (June 2018) ¶98: omitted conversion must not remove an exposure."""
    with pytest.raises(RiskWeightUnavailable):
        _credit_rwa(
            _row(
                "LOAN/1",
                "LOAN",
                currency="USD",
                balance="1000",
                converted=False,
                regulatory_category="SME_UNRATED",
            )
        )


def test_specific_provision_is_not_deducted_again_through_gl_allowance() -> None:
    """BoG CRD (June 2018) ¶98: provisions deducted once from the credit exposure."""
    row = replace(
        _row(
            "NPL/1",
            "LOAN",
            balance="1000",
            regulatory_category="CORPORATE_UNRATED",
            attributes={"ecl_provision_ghs": "200"},
        ),
        ifrs9_stage=3,
    )
    allowance = CanonicalGlAccount(
        account_code="GL-1390",
        name="Loan loss allowance",
        account_class="ASSET",
        balance=Decimal("-200"),
    )
    assert _credit_rwa(row, gl_accounts=[allowance]) == Decimal("1200")


def test_pse_hqla_determination_does_not_confer_sovereign_capital_weight() -> None:
    """BoG CRD (June 2018) ¶117: HQLA classification cannot make PSE credit RW0."""
    row = _row(
        "PSE/1",
        "SECURITY_HOLDING",
        balance="1000",
        attributes={"issuer_class": "public_institution", "hqla_level": "L2A"},
    )
    split, other = _split_securities(_canonical(row), [])
    assert split.level2a == Decimal("1000")
    assert other == Decimal("0")
    assert _credit_rwa(row) == Decimal("500")


def test_foreign_currency_sme_has_twenty_point_addon() -> None:
    """BoG CRD (June 2018) ¶139: foreign-currency SME claims carry 100% + 20%."""
    assert _credit_rwa(
        _row(
            "SME/FX",
            "LOAN",
            currency="USD",
            balance="1000",
            balance_ghs="1000",
            regulatory_category="SME_UNRATED",
        )
    ) == Decimal("1200")


@pytest.mark.parametrize("position_type", ["LOAN", "INTERBANK_PLACEMENT"])
def test_foreign_central_bank_claim_uses_public_counterparty_treatment(position_type: str) -> None:
    """BoG CRD (June 2018) ¶107: central-bank claims are 20% in foreign currency."""
    assert _credit_rwa(
        _row(
            "BOG/FX",
            position_type,
            currency="USD",
            balance="1000",
            balance_ghs="1000",
            regulatory_category="CORPORATE_UNRATED",
            counterparty_type="CENTRAL_BANK",
            attributes={"issuer": "Bank of Ghana"},
        )
    ) == Decimal("200")


def test_bank_loan_uses_bank_weight_instead_of_corporate_product_weight() -> None:
    """BoG CRD (June 2018) ¶123: a loan to an unrated bank is a bank claim at 50%."""
    assert _credit_rwa(
        _row(
            "BANK/LOAN",
            "LOAN",
            balance="1000",
            regulatory_category="CORPORATE_UNRATED",
            counterparty_type="BANK_NON_OECD",
        )
    ) == Decimal("500")


@pytest.mark.parametrize("counterparty", ["NBFI", "CORPORATE"])
def test_nonbank_placement_never_receives_bank_preference(counterparty: str) -> None:
    """BoG CRD (June 2018) ¶123–126: the bank weight does not cover nonbanks."""
    with pytest.raises(RiskWeightUnavailable):
        _credit_rwa(
            _row("NONBANK/1", "INTERBANK_PLACEMENT", balance="1000", counterparty_type=counterparty)
        )


@pytest.mark.parametrize("code", ["GL-1390", "GL-1700"])
@pytest.mark.parametrize("name", ["Suspended interest", "Interest in suspense"])
def test_suspended_interest_gl_is_deducted_once(code: str, name: str) -> None:
    """BoG CRD (June 2018) ¶98: suspended interest has one capital deduction."""
    row = _row(
        "LOAN/SUSPENSE",
        "LOAN",
        balance="1000",
        regulatory_category="CORPORATE_UNRATED",
        attributes={"interest_in_suspense_ghs": "50"},
    )
    contra = CanonicalGlAccount(
        account_code=code, name=name, account_class="ASSET", balance=Decimal("-50")
    )
    assert _credit_rwa(row, gl_accounts=[contra]) == Decimal("950")


@pytest.mark.parametrize(
    "contra_name",
    [
        "Equipment impairment",
        "Equipment provision",
        "Equipment allowance",
        "Equipment contra",
        "Equipment write-off",
    ],
)
def test_unrelated_asset_impairment_remains_in_residual_rwa(contra_name: str) -> None:
    """BoG CRD (June 2018) ¶98: a loan deduction cannot undo equipment impairment."""
    loan = _row("LOAN/1", "LOAN", balance="1000", regulatory_category="CORPORATE_UNRATED")
    accounts = [
        CanonicalGlAccount(
            account_code="GL-1700", name="Equipment", account_class="ASSET", balance=Decimal("1000")
        ),
        CanonicalGlAccount(
            account_code="GL-1790",
            name=contra_name,
            account_class="ASSET",
            balance=Decimal("-200"),
        ),
    ]
    assert _credit_rwa(loan, gl_accounts=accounts) == Decimal("1800")


def test_uncovered_loan_allowance_remains_with_its_gl_loan() -> None:
    """BoG CRD (June 2018) ¶98: an uncovered GL loan retains its contra balance."""
    security = _row(
        "PAPER/1", "SECURITY_HOLDING", balance="1000", attributes={"instrument": "gog_bond"}
    )
    accounts = [
        CanonicalGlAccount(
            account_code="GL-1300", name="Loans", account_class="ASSET", balance=Decimal("1000")
        ),
        CanonicalGlAccount(
            account_code="GL-1390",
            name="Loan loss allowance",
            account_class="ASSET",
            balance=Decimal("-200"),
        ),
    ]
    assert _credit_rwa(security, gl_accounts=accounts) == Decimal("800")


def _stress_row(row: _PositionRow) -> ExposureRow:
    return ExposureRow(
        source_reference=row.source_reference,
        position_type=row.position_type,
        currency=row.currency,
        balance_rep=row.balance_ghs,
        is_foreign_currency=row.currency != "GHS",
        notional_rep=None,
        ifrs9_stage=row.ifrs9_stage,
        attributes=row.attributes,
        counterparty_type=row.counterparty_type,
        counterparty_resident=True,
        counterparty_country=None,
        group_key=row.source_reference,
        regulatory_category=row.regulatory_category,
        product_risk_weight_code="RW75",
        product_code=row.product_code,
        contractual_maturity=row.contractual_maturity,
    )


def test_sme_fx_stress_uses_corrected_credit_weights() -> None:
    """BoG CRD (June 2018) ¶139: FX shock adds 120 to 2,200, preserving gross EAD."""
    rows = [
        _row("SME/DOM", "LOAN", balance="1000", regulatory_category="SME_UNRATED"),
        _row(
            "SME/FX", "LOAN", currency="USD", balance_ghs="1000", regulatory_category="SME_RETAIL"
        ),
    ]
    book = enterprise_stress._build_credit_exposures(  # pyright: ignore[reportPrivateUsage]
        [_stress_row(row) for row in rows], bog_capital_params(), capital_facts=()
    )
    result = compute_bottom_up_credit(
        book, pd_multiplier=Decimal("1"), lgd_multiplier=Decimal("1"), fx_fraction=Decimal("0.1")
    )
    assert result.base_credit_rwa == _credit_rwa(*rows) == Decimal("2200")
    assert result.stressed_credit_rwa == Decimal("2320")
    assert result.credit_rwa_uplift_factor == Decimal("1.054545")
    assert sum((exposure.ead for exposure in book), Decimal("0")) == Decimal("2000")


@pytest.mark.parametrize(
    ("position_type", "category", "counterparty", "attributes", "expected"),
    [
        ("LOAN", "CORPORATE_UNRATED", "CENTRAL_BANK", {"instrument": "bog_bill"}, "200"),
        ("LOAN", "CORPORATE_UNRATED", "BANK_OECD", {}, "500"),
        ("INTERBANK_PLACEMENT", None, "BANK_NON_OECD", {"external_rating_grade": "6"}, "1500"),
        (
            "LOAN",
            "CORPORATE_UNRATED",
            "GOVERNMENT_ENTITY",
            {"issuer_class": "public_institution"},
            "700",
        ),
        (
            "LOAN",
            "SME_UNRATED",
            "SME",
            {"specific_provision_ghs": "200", "interest_in_suspense_ghs": "50"},
            "900",
        ),
    ],
)
def test_bottom_up_rwa_matches_net_capital_without_netting_expected_loss(
    position_type: str,
    category: str | None,
    counterparty: str,
    attributes: dict[str, str],
    expected: str,
) -> None:
    """BoG CRD (June 2018) ¶98, ¶107, ¶117–124, ¶139: capital and stress share a basis."""
    row = _row(
        "CLAIM/FX",
        position_type,
        currency="USD",
        balance_ghs="1000",
        regulatory_category=category,
        counterparty_type=counterparty,
        attributes=attributes,
    )
    book = enterprise_stress._build_credit_exposures(
        [_stress_row(row)], bog_capital_params(), capital_facts=()
    )  # pyright: ignore[reportPrivateUsage]
    result = compute_bottom_up_credit(
        book, pd_multiplier=Decimal("1"), lgd_multiplier=Decimal("1"), fx_fraction=Decimal("0.1")
    )
    assert result.base_credit_rwa == _credit_rwa(row) == Decimal(expected)
    assert result.stressed_credit_rwa == Decimal(expected) * Decimal("1.1")
    assert book[0].ead == Decimal("1000")
    if counterparty == "SME":
        assert result.base_expected_loss == Decimal("9")
        assert result.stressed_expected_loss == Decimal("9.9")


def test_projection_overlay_does_not_revalue_constant_residual_assets() -> None:
    """BoG CRD (June 2018) ¶98, ¶139: only migrating claims contribute the RWA delta."""
    rows = [
        _row("SME/DOM", "LOAN", balance="1000", regulatory_category="SME_UNRATED"),
        _row(
            "SME/FX", "LOAN", currency="USD", balance_ghs="1000", regulatory_category="SME_RETAIL"
        ),
    ]
    book = enterprise_stress._build_credit_exposures(
        [_stress_row(row) for row in rows], bog_capital_params(), capital_facts=()
    )  # pyright: ignore[reportPrivateUsage]
    paths = [
        MacroPathPoint(
            point.variable,
            point.year_index,
            point.base_value,
            point.base_value * Decimal("1.1")
            if point.variable == "fx_usd_ghs" and point.year_index > 0
            else point.base_value,
        )
        for point in base_paths()
    ]
    uplift, _ = enterprise_stress._credit_overlays(book, paths, 3, base_credit_rwa=Decimal("3200"))  # pyright: ignore[reportPrivateUsage]
    assert {Decimal("3200") * factor for factor in uplift.values()} == {Decimal("3320")}
