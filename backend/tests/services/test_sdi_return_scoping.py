"""SDI Phase B — the reporting calendar is class-filtered (docs/sdi.md §6.2).

A savings-&-loans tenant resolves to the ``sdi`` regime and sees NONE of the
bank/BoG returns in its reporting calendar; a universal bank sees the full set.
Every registered return is a bank return today (the SDI/ORASS return pack is
Phase F, blocked on BoG), so the SDI calendar is honestly empty — the filter is
the scoping mechanism, not a claim that an SDI has no obligations of its own.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank
from app.schemas.regulatory_reporting import RegulatoryPackageCreate
from app.services.regulatory_reporting import calendar, generation
from app.services.regulatory_reporting.registry import REGISTRY
from app.services.regulatory_reporting.templates import get_template
from tests.api.helpers import ORG_1, USER_1

_AS_OF = date(2026, 6, 30)


def _make_bank(db: Session, *, institution_type: str) -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name=f"{institution_type} tenant",
        short_name="Scope",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="x",
        institution_type=institution_type,
    )
    db.add(bank)
    db.flush()
    return bank


def test_sdi_returns_are_explicitly_scoped_and_bank_returns_remain_isolated() -> None:
    """The public directive packets are SDI-only; no BSD return leaks across."""
    sdi_codes = {
        code for code, definition in REGISTRY.items() if "sdi" in definition.institution_classes
    }
    assert sdi_codes == {
        "SDI-LMT-MONTHLY",
        "SDI-LE-MONTHLY",
        "SDI-STRESS-ANNUAL",
        "SDI-IRRBB-QUARTERLY",
        # Credit PR-6: the Notice 2025/23 monthly NPL report binds banks AND
        # SDIs - the deliberate first return family the two classes share.
        "NPL-MONTHLY",
    }
    assert all(
        definition.institution_classes == ("bank",)
        for code, definition in REGISTRY.items()
        if code not in sdi_codes
    )


def test_universal_bank_sees_the_full_reporting_calendar(db_session: Session) -> None:
    bank = _make_bank(db_session, institution_type="universal_bank")
    ctx = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)
    result = calendar.list_obligations(db_session, ctx, bank.id, as_of=_AS_OF)
    # Bank/BoG returns expand into calendar obligations for a 'bank' tenant.
    assert result.obligations


def test_savings_and_loans_sees_only_its_sdi_return_calendar(db_session: Session) -> None:
    bank = _make_bank(db_session, institution_type="savings_and_loans")
    ctx = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)
    result = calendar.list_obligations(db_session, ctx, bank.id, as_of=_AS_OF)
    assert {obligation.return_code for obligation in result.obligations} == {
        "SDI-LMT-MONTHLY",
        "SDI-LE-MONTHLY",
        "SDI-STRESS-ANNUAL",
        "SDI-IRRBB-QUARTERLY",
        # Credit PR-6: the Notice 2025/23 monthly NPL report binds banks AND
        # SDIs - the deliberate first return family the two classes share.
        "NPL-MONTHLY",
    }
    assert {obligation.return_family for obligation in result.obligations} == {"sdi", "credit"}
    assert result.coverage_note is None


def test_finance_house_sees_the_same_sdi_return_calendar(db_session: Session) -> None:
    # Both licence types share the SDI class and its published directive pack.
    bank = _make_bank(db_session, institution_type="finance_house")
    ctx = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)
    result = calendar.list_obligations(db_session, ctx, bank.id, as_of=_AS_OF)
    assert {obligation.return_code for obligation in result.obligations} == {
        "SDI-LMT-MONTHLY",
        "SDI-LE-MONTHLY",
        "SDI-STRESS-ANNUAL",
        "SDI-IRRBB-QUARTERLY",
        # Credit PR-6: the Notice 2025/23 monthly NPL report binds banks AND
        # SDIs - the deliberate first return family the two classes share.
        "NPL-MONTHLY",
    }


def test_sdi_cannot_generate_a_bank_only_return(db_session: Session) -> None:
    """Server-side return-set scoping (docs/sdi.md §4.4/§14): the single package-
    mint site rejects a bank-only return code for an SDI tenant — the calendar
    filter alone is not security, since an SDI could POST the code directly."""
    bank_only_code = next(
        code for code, d in REGISTRY.items() if "sdi" not in d.institution_classes
    )
    sdi = _make_bank(db_session, institution_type="savings_and_loans")
    ctx = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)
    payload = RegulatoryPackageCreate(return_code=bank_only_code, reporting_date=_AS_OF)
    with pytest.raises(HTTPException) as exc:
        generation.generate_package(db_session, ctx, sdi.id, payload)
    assert exc.value.status_code == 403
    assert "class" in str(exc.value.detail).lower()


def test_sdi_lmt_template_contains_only_the_applicable_public_tables() -> None:
    template = get_template(REGISTRY["SDI-LMT-MONTHLY"].template_id)
    assert template is not None
    sections = {section.section_code for section in template.sections}
    assert "prudential_ratio_inputs" in sections
    assert "maturity_ladder" in sections
    assert "unencumbered_assets" in sections
    assert "lcr_by_currency" not in sections
    assert not any("LCR" in section.sheet_title for section in template.sections)


def test_sdi_large_exposure_template_is_the_five_published_directive_forms() -> None:
    template = get_template(REGISTRY["SDI-LE-MONTHLY"].template_id)
    assert template is not None
    assert [section.section_code for section in template.sections] == [
        "template_1",
        "template_1a",
        "template_2",
        "template_3",
        "template_4",
    ]


def test_sdi_stress_template_excludes_the_basel_capital_build() -> None:
    template = get_template(REGISTRY["SDI-STRESS-ANNUAL"].template_id)
    assert template is not None
    sections = {section.section_code for section in template.sections}
    assert {"t1_summary_positions", "t3_profit_and_loss", "t5_rwa", "governance"} <= sections
    assert "t2_capital_projection" not in sections
    table1 = next(
        section for section in template.sections if section.section_code == "t1_summary_positions"
    )
    assert [column.header for column in table1.columns] == [
        "Label",
        "Period",
        "Net Own Funds",
        "Risk-Weighted Assets",
        "CAR %",
        "Paid-up Capital",
    ]


def test_sdi_irrbb_template_omits_the_bank_tier1_outlier_columns() -> None:
    template = get_template(REGISTRY["SDI-IRRBB-QUARTERLY"].template_id)
    assert template is not None
    eve = next(section for section in template.sections if section.section_code == "eve_scenarios")
    assert [column.key for column in eve.columns] == ["code", "eve_ghs", "value"]
