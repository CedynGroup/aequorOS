"""End-to-end SDI engine slice against a realistic ingested canonical book.

Onboards a savings-&-loans bank, seeds the canonical fixture (positions +
counterparties + capital_structure — the shape a real ingestion produces), and
drives the SDI engine layer: module readiness, LMTD Table-1 floors resolving to
the SDI values, the simplified-capital checks, and the class-aware loan
classification. Proves the pieces compose against real data, not just synthetic
inputs — the layer the /banks/{id}/sdi/* endpoints delegate to.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank
from app.services import loan_classification, sdi_capital, sdi_capital_checks, sdi_readiness
from app.services.regulatory_reporting import calendar
from app.services.regulatory_reporting.le_generation import (
    _table1_thresholds,  # pyright: ignore[reportPrivateUsage]
)
from tests.api.helpers import ORG_1, USER_1
from tests.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture

_CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)


def _onboard_sdi(db: Session) -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name="AequorOS SDI (E2E)",
        short_name="AEQSDI",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="savings_and_loans",
        institution_type="savings_and_loans",
    )
    db.add(bank)
    db.flush()
    seed_canonical_fixture(db, organization_id=ORG_1, bank_id=bank.id, as_of=FIXTURE_AS_OF)
    return bank


def test_sdi_end_to_end_engine_slice(db_session: Session) -> None:
    sdi = _onboard_sdi(db_session)

    # 1. The canonical book lights up the position-driven modules.
    statuses = {
        m.module: m.status
        for m in sdi_readiness.assess_sdi_readiness(db_session, _CTX, sdi, FIXTURE_AS_OF)
    }
    assert statuses["liquidity_table1"] != "blocked"
    assert statuses["capital"] == "ready"  # capital_structure ingested
    assert statuses["exposures"] == "ready"
    assert statuses["provisioning"] != "blocked"

    # 2. LMTD Table-1 binds against the SDI floors (not the bank floors), and the
    #    control-plane value is normalised (90, not 90.000000 — audit M1).
    thresholds = _table1_thresholds(db_session, _CTX, sdi, FIXTURE_AS_OF)
    assert str(thresholds["narrow_to_volatile"][0]) == "90"
    assert str(thresholds["broad_to_total_deposits"][0]) == "70"

    # 3. Simplified-capital checks run against the ingested capital_structure.
    paid_up = sdi_capital_checks.check_paid_up_capital(db_session, _CTX, sdi, FIXTURE_AS_OF)
    assert paid_up.computable  # dataset present → computable (not None)
    assert paid_up.required_ghs is not None

    # 4. Class-aware loan classification produces the NBFI 4-grade book.
    report = loan_classification.classify_loan_book(db_session, _CTX, sdi, FIXTURE_AS_OF)
    assert report.institution_class == "sdi"
    assert report.result.total_exposure_ghs > 0  # the fixture has a loan book
    # The SDI grid has no OLEM watch grade (4-grade, not 5).
    grades = {b.grade for b in report.result.buckets}
    assert "olem" not in grades

    # 4b. The live s.29 CAR composes: NOF ÷ RWA against the s.29 floor, with the
    #     loan book reconciling exactly to the classification total (same book,
    #     two modules) and every simplified risk weight flagged pending BoG.
    summary = sdi_capital.compute_sdi_capital_summary(db_session, _CTX, sdi, FIXTURE_AS_OF)
    assert summary.computable
    assert summary.car_pct is not None
    assert summary.car_min_pct == Decimal("10")  # Act 930 s.29 SDI floor
    loan_band = next(b for b in summary.bands if b.bucket == "other_loans")
    assert loan_band.exposure_ghs == report.result.total_exposure_ghs
    assert summary.pending_parameters  # risk weights are still pending BoG

    # 5. The SDI's return calendar is scoped (bank BSD forms do not apply).
    obligations = calendar.list_obligations(
        db_session, _CTX, sdi.id, as_of=FIXTURE_AS_OF
    ).obligations
    assert obligations == []
