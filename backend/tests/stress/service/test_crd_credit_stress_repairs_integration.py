"""BoG CRD (June 2018) ¶98, ¶139: stress and capital share net measurement.

Primary authority:
https://www.bog.gov.gh/wp-content/uploads/2022/05/Basel-II-BOG-CRD-Final-27-June-2018-Basel-Committee-BSD.pdf

This module's other-suite cases live in ``test_crd_credit_stress_repairs.py``.
"""

from __future__ import annotations

from typing import cast

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.authorization import ModuleScope, SensitivityScope
from app.live.service.fact_derivation import derive_facts
from app.models import BankFinancialFact, RegulatoryRun
from app.stress.schemas.reverse_stress import ReverseStressRunCreate
from app.stress.service import reverse_stress
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID
from tests.fixtures.capital_structure import MAKER, REPORTING_DATE, seed_book
from tests.support.authority import grant_organization_analyst

pytestmark = pytest.mark.requirement("BoG CRD (June 2018) ¶98, ¶139")


def test_reverse_stress_stale_basis_is_actionable_and_creates_no_frontier(
    db_session: Session,
) -> None:
    """BoG CRD (June 2018) ¶98: reverse stress refuses stale facts before storing a frontier."""
    seed_book(db_session)
    derived = derive_facts(db_session, MAKER, SAMPLE_BANK_ID, REPORTING_DATE)
    grant_organization_analyst(
        db_session,
        ModuleScope.FORECASTING,
        SensitivityScope.CONFIDENTIAL,
        grantor="credit-basis-test",
        reason="Exercise reverse stress stale-basis refusal.",
    )
    db_session.execute(
        delete(BankFinancialFact).where(
            BankFinancialFact.bank_id == derived.bank_id,
            BankFinancialFact.fact_group == "credit_exposure",
        )
    )
    db_session.flush()
    with pytest.raises(HTTPException) as refusal:
        reverse_stress.run_reverse_stress(
            db_session,
            MAKER,
            derived.bank_id,
            ReverseStressRunCreate(reporting_period_id=derived.reporting_period_id),
        )
    assert refusal.value.status_code == 409
    detail = cast(dict[str, str], refusal.value.detail)
    assert detail["error_code"] == "credit_exposure_basis_missing"
    assert "net CRD credit exposure basis" in detail["message"]
    assert (
        db_session.scalar(select(RegulatoryRun).where(RegulatoryRun.module == "reverse_stress"))
        is None
    )
