"""End-to-end SDI engine slice against a realistic ingested canonical book.

Onboards a savings-&-loans bank, seeds the canonical fixture (positions +
counterparties + capital_structure — the shape a real ingestion produces), and
drives the SDI engine layer: module readiness, LMTD Table-1 floors resolving to
the SDI values, the simplified-capital checks, and the class-aware loan
classification. Proves the pieces compose against real data, not just synthetic
inputs — the layer the /banks/{id}/sdi/* endpoints delegate to.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, BankReportingPeriod, RegulatoryPackage, RegulatoryParameter
from app.schemas.regulatory_reporting import RegulatoryPackageCreate
from app.services import loan_classification, sdi_capital, sdi_capital_checks, sdi_readiness
from app.services.regulatory_reporting import calendar, generation
from app.services.regulatory_reporting.le_generation import (
    _table1_thresholds,  # pyright: ignore[reportPrivateUsage]
    generate_sdi_large_exposures,
    generate_sdi_lmt,
)
from app.services.regulatory_reporting.registry import REGISTRY
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


def _period(db: Session, bank: Bank) -> BankReportingPeriod:
    period = BankReportingPeriod(
        organization_id=bank.organization_id,
        bank_id=bank.id,
        period_start=date(FIXTURE_AS_OF.year, FIXTURE_AS_OF.month, 1),
        period_end=FIXTURE_AS_OF,
        label=FIXTURE_AS_OF.strftime("%Y-%m"),
        status="closed",
    )
    db.add(period)
    db.flush()
    return period


def _govern_sdi_rwa_scope(db: Session) -> None:
    """Approve the two s.29 decisions required before a return can be filed."""
    common = {
        "scope_type": "institution_class",
        "scope_key": "sdi",
        "jurisdiction_code": "GH",
        "unit": "count",
        "source_citation": "test governed SDI filing scope",
        "confirmation_status": "confirmed",
        "effective_from": date(2025, 1, 1),
        "status": "approved",
        "proposed_by": "test-maker",
        "approved_by": "test-checker",
    }
    db.add_all(
        [
            RegulatoryParameter(
                **common,
                param_code=sdi_capital.COMPOSITION_PARAM,
                value_numeric=None,
                value_json={
                    "credit": sdi_capital.MEASURE_BUCKET_WEIGHTED_EXPOSURE,
                },
            ),
            RegulatoryParameter(
                **common,
                param_code=sdi_capital.BUCKET_MAP_PARAM,
                value_numeric=None,
                value_json={
                    "CASH": "cash",
                    "SECURITY_HOLDING": "sovereign",
                    "INTERBANK_PLACEMENT": "interbank",
                    "LOAN": "other_loans",
                    "OTHER_ASSET": "other_assets",
                },
            ),
        ]
    )
    db.flush()


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

    # 5. The SDI sees only the evidence-backed SDI packet, never BSD returns.
    obligations = calendar.list_obligations(
        db_session, _CTX, sdi.id, as_of=FIXTURE_AS_OF
    ).obligations
    assert {obligation.return_code for obligation in obligations} == {
        "SDI-LMT-MONTHLY",
        "SDI-LE-MONTHLY",
        "SDI-STRESS-ANNUAL",
        "SDI-IRRBB-QUARTERLY",
    }


def test_sdi_lmt_generator_renders_only_the_published_applicable_tables(
    db_session: Session,
) -> None:
    sdi = _onboard_sdi(db_session)
    period = _period(db_session, sdi)

    generated = generate_sdi_lmt(db_session, _CTX, sdi, period, REGISTRY["SDI-LMT-MONTHLY"])

    sections = {section["code"] for section in generated.snapshot["sections"]}
    assert {
        "prudential_ratio_inputs",
        "prudential_ratio_percentages",
        "maturity_ladder",
    } <= sections
    assert "lcr_by_currency" not in sections
    assert generated.snapshot["metadata"]["report_scope"].endswith(
        "Table 11 excluded as banks-only."
    )
    assert generated.source_runs == []


def test_sdi_lmt_mints_through_the_sealed_package_lifecycle(
    db_session: Session,
) -> None:
    sdi = _onboard_sdi(db_session)
    _period(db_session, sdi)

    package = generation.generate_package(
        db_session,
        _CTX,
        sdi.id,
        RegulatoryPackageCreate(
            return_code="SDI-LMT-MONTHLY",
            reporting_date=FIXTURE_AS_OF,
        ),
    )

    assert package.return_family == "sdi"
    assert package.return_code == "SDI-LMT-MONTHLY"
    assert package.status == "generated"
    persisted = db_session.get(RegulatoryPackage, package.id)
    assert persisted is not None
    assert persisted.content_digest


def test_sdi_large_exposures_refuses_an_ungoverned_s29_filing_basis(
    db_session: Session,
) -> None:
    sdi = _onboard_sdi(db_session)
    period = _period(db_session, sdi)

    with pytest.raises(sdi_capital.SdiCapitalPolicyUnresolved):
        generate_sdi_large_exposures(db_session, _CTX, sdi, period, REGISTRY["SDI-LE-MONTHLY"])


def test_sdi_large_exposures_uses_governed_s29_net_own_funds(
    db_session: Session,
) -> None:
    sdi = _onboard_sdi(db_session)
    period = _period(db_session, sdi)
    _govern_sdi_rwa_scope(db_session)

    generated = generate_sdi_large_exposures(
        db_session, _CTX, sdi, period, REGISTRY["SDI-LE-MONTHLY"]
    )

    summary = sdi_capital.compute_sdi_capital_summary(db_session, _CTX, sdi, FIXTURE_AS_OF)
    totals = {row["code"]: row for row in generated.snapshot["totals"]}
    assert Decimal(str(totals["nof_ghs"]["value"])) == summary.net_own_funds_ghs
    assert "tier1_ghs" not in totals
    metadata = generated.snapshot["metadata"]
    assert (
        metadata["nof_basis"]
        == "Net Own Funds from the governed Act 930 s.29 SDI capital calculation."
    )
    assert metadata["sdi_rwa_taxonomy_source"] == sdi_capital.BUCKET_MAP_CONTROL_PLANE
    assert metadata["sdi_rwa_composition_source"] == sdi_capital.COMPOSITION_CONTROL_PLANE
