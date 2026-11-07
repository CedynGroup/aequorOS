"""BoG CRD (June 2018) ¶98, ¶106–107, ¶117–124: shared review regressions.

This module's other-suite cases live in ``test_crd_review_repairs_integration.py``.
"""

__all__ = ["_derive_specs"]
from dataclasses import replace
from decimal import Decimal

import pytest

from app.capital.domain.engine import (
    CapitalFact,
    RiskWeightUnavailable,
    _credit_line_items,  # pyright: ignore[reportPrivateUsage]
)
from app.live.service.fact_derivation import (
    _derive_specs,  # pyright: ignore[reportPrivateUsage]
)
from app.stress.domain.appendix_ii import _crd_class  # pyright: ignore[reportPrivateUsage]
from app.stress.domain.credit_bottom_up import compute_bottom_up_credit
from app.stress.service.enterprise_stress import (
    _build_credit_exposures,  # pyright: ignore[reportPrivateUsage]
)
from tests.credit.service.test_crd_credit_exposures import (
    _stress_row,  # pyright: ignore[reportPrivateUsage]
)
from tests.services.test_derivation_fail_closed_defaults import (
    _canonical,  # pyright: ignore[reportPrivateUsage]
    _row,  # pyright: ignore[reportPrivateUsage]
)
from tests.support.capital_params import bog_capital_params

pytestmark = pytest.mark.requirement("BoG CRD (June 2018) ¶98, ¶106–107, ¶117–124")


@pytest.mark.parametrize("position_type", ["LOAN", "SECURITY_HOLDING", "INTERBANK_PLACEMENT"])
@pytest.mark.parametrize("counterparty", [("CENTRAL_BANK", "bog"), ("SOVEREIGN", "gog")])
@pytest.mark.parametrize("foreign", [False, True])
@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("domicile", ["domestic", "foreign_country", "nonresident"])
def test_typed_domestic_identity_reaches_capital_and_stress(
    position_type: str, counterparty: tuple[str, str], foreign: bool, live: bool, domicile: str
) -> None:
    """BoG CRD (June 2018) ¶106–107: typed domestic claims carry 0%/20%, conflicts refuse."""
    counterparty_type, role = counterparty
    row = replace(
        _row(
            "TYPED/1",
            position_type,
            balance="1000",
            currency="USD" if foreign else "GHS",
            counterparty_type=counterparty_type,
        ),
        counterparty_country="NG" if domicile == "foreign_country" else "GH",
        counterparty_resident=domicile != "nonresident",
    )
    specs, _, _ = _derive_specs(_canonical(row), live=live)
    facts = [
        CapitalFact(spec.fact_group, spec.category, spec.amount, spec.risk_weight_code)
        for spec in specs
    ]
    if domicile != "domestic":
        with pytest.raises(RiskWeightUnavailable):
            _credit_line_items(facts, bog_capital_params())
        return
    lines = _credit_line_items(facts, bog_capital_params())
    line = next(line for line in lines if line.exposure_amount == Decimal("1000"))
    assert line.weighted_amount == Decimal("200" if foreign else "0")
    assert _crd_class(line) == role
    book = _build_credit_exposures(
        [_stress_row(row)], bog_capital_params(), domestic_country="GH", capital_facts=facts
    )
    assert book[0].crd_class == role
    assert book[0].risk_weight_pct == Decimal("20" if foreign else "0")


@pytest.mark.parametrize("position_type", ["LOAN", "SECURITY_HOLDING", "INTERBANK_PLACEMENT"])
@pytest.mark.parametrize("issuer,role", [("Bank of Ghana", "bog"), ("Government of Ghana", "gog")])
@pytest.mark.parametrize("live", [False, True])
def test_registry_issuer_name_preserves_the_role_in_capital_and_loss_outputs(
    position_type: str, issuer: str, role: str, live: bool
) -> None:
    """BoG CRD (June 2018) ¶107: the same 20% weight retains distinct GoG/BoG reporting roles."""
    row = _row(
        "ISSUER/1",
        position_type,
        balance="1000",
        currency="USD",
        attributes={"issuer": issuer, "pd_pct": "2", "lgd_pct": "45"},
    )
    canonical = _canonical(row)
    canonical = replace(
        canonical, central_bank_names=replace(canonical.central_bank_names, full=("bank of ghana",))
    )
    specs, _, _ = _derive_specs(canonical, live=live)
    facts = [
        CapitalFact(spec.fact_group, spec.category, spec.amount, spec.risk_weight_code)
        for spec in specs
    ]
    line = next(
        line
        for line in _credit_line_items(facts, bog_capital_params())
        if line.exposure_amount == Decimal("1000")
    )
    assert line.weighted_amount == Decimal("200")
    assert _crd_class(line) == role
    book = _build_credit_exposures(
        [_stress_row(row)],
        bog_capital_params(),
        sovereign_names=canonical.sovereign_issuer_names,
        central_bank_names=canonical.central_bank_names.full,
        domestic_country="GH",
        capital_facts=facts,
    )
    result = compute_bottom_up_credit(
        book, pd_multiplier=Decimal("1"), lgd_multiplier=Decimal("1"), fx_fraction=Decimal("0")
    )
    assert book[0].crd_class == role
    assert result.base_expected_loss == Decimal("9")
    assert {impact.exposure_class for impact in result.by_class} == {role}


@pytest.mark.parametrize("role", ["bog", "gog"])
@pytest.mark.parametrize("named", [False, True])
def test_domestic_public_loan_uses_one_net_and_collateral_basis(role: str, named: bool) -> None:
    """BoG CRD (June 2018) ¶98, ¶107: netting and CRM preserve the domestic issuer role."""
    attributes = {
        "specific_provision_ghs": "100",
        "crm_collateral_ghs": "200",
        "crm_collateral_class": "corporate_debt",
    }
    if named:
        attributes["issuer"] = "Bank of Ghana" if role == "bog" else "Government of Ghana"
    row = replace(
        _row(
            "PUBLIC/CRM",
            "LOAN",
            balance="1000",
            currency="USD",
            counterparty_type=None if named else "CENTRAL_BANK" if role == "bog" else "SOVEREIGN",
            attributes=attributes,
        ),
        counterparty_country="GH",
        counterparty_resident=True,
    )
    canonical = _canonical(row)
    canonical = replace(
        canonical, central_bank_names=replace(canonical.central_bank_names, full=("bank of ghana",))
    )
    specs, _, _ = _derive_specs(canonical, live=False)
    facts = [
        CapitalFact(spec.fact_group, spec.category, spec.amount, spec.risk_weight_code)
        for spec in specs
    ]
    params = replace(bog_capital_params(), crm_haircuts={"CORPORATE_DEBT": Decimal("0")})
    line = next(
        line for line in _credit_line_items(facts, params) if line.rate_pct == Decimal("20")
    )
    assert line.exposure_amount == Decimal("700")
    assert line.weighted_amount == Decimal("140")
    assert _crd_class(line) == role
    book = _build_credit_exposures(
        [_stress_row(row)],
        params,
        sovereign_names=canonical.sovereign_issuer_names,
        central_bank_names=canonical.central_bank_names.full,
        domestic_country="GH",
        capital_facts=facts,
    )
    assert book[0].ead == Decimal("1000")
    assert book[0].credit_amount == Decimal("900")
    result = compute_bottom_up_credit(
        book, pd_multiplier=Decimal("1"), lgd_multiplier=Decimal("1"), fx_fraction=Decimal("0")
    )
    assert result.base_credit_rwa == Decimal("140")
