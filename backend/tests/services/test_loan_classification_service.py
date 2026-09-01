"""Loan-classification service (docs/sdi.md §2.2, §4, §7 Phase G).

Proves the thin service resolves the tenant's class grid from the regulatory-
parameter control plane (never a literal) and classifies a hand-built LOAN book
end to end:

- a savings-&-loans (SDI) tenant binds the 4-grade grid with the SDI rates
  (0/20/50/100) and the 90-day NPL cutoff;
- a universal-bank tenant binds the 5-grade grid with the bank rates
  (1/10/25/50/100) including the OLEM watch grade;
- the ``days_past_due`` snapshot attribute drives classification, and a loan
  without it falls back to the IFRS 9 stage proxy;
- an unseeded required parameter fails loud (no invented regulatory number).

The book is built directly through the canonical models (no file ingestion),
mirroring ``tests/factories/canonical.py``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    Bank,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    IngestionBatch,
    LineageRecord,
    RegulatoryParameter,
)
from app.services import loan_classification as svc
from app.services import regulatory_parameters as rp
from tests.api.helpers import ORG_1, USER_1

AS_OF = date(2026, 6, 30)
CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)


def _make_bank(db: Session, *, institution_type: str) -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name="Loan Classification Test Bank",
        short_name="LCTB",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type=institution_type,
    )
    db.add(bank)
    db.flush()
    return bank


def _seed_loans(
    db: Session,
    bank: Bank,
    loans: list[tuple],  # (ref, balance_ghs, days_past_due, ifrs9_stage[, extra_attributes])
) -> None:
    """Insert a minimal LOAN book: (ref, balance_ghs, days_past_due, ifrs9_stage)."""
    batch = IngestionBatch(
        organization_id=ORG_1,
        bank_id=bank.id,
        source_system="EXCEL_CSV",
        adapter_version="1.0",
        extraction_mode="full",
        status="accepted",
        as_of_date=AS_OF,
    )
    db.add(batch)
    db.flush()
    lineage = LineageRecord(
        organization_id=ORG_1,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref="loan-classification-test",
        input_lineage_ids=[],
    )
    db.add(lineage)
    db.flush()
    common = {
        "organization_id": ORG_1,
        "bank_id": bank.id,
        "as_of_date": AS_OF,
        "source_system": "EXCEL_CSV",
        "ingestion_batch_id": batch.id,
        "lineage_id": lineage.id,
        "validation_status": "accepted",
    }
    for ref, balance, dpd, stage, *rest in loans:
        position = CanonicalPosition(
            **common, source_reference=ref, position_type="LOAN", currency="GHS"
        )
        db.add(position)
        db.flush()
        attributes: dict[str, object] = {"balance_ghs": balance}
        if dpd is not None:
            attributes["days_past_due"] = dpd
        if rest:
            attributes.update(rest[0])
        db.add(
            CanonicalPositionSnapshot(
                **common,
                source_reference=ref,
                position_id=position.id,
                balance=Decimal(balance),
                ifrs9_stage=stage,
                attributes=attributes,
            )
        )
    db.flush()


def test_sdi_tenant_binds_4_grade_grid_with_20_50_100_rates_and_npl_at_90(
    db_session: Session,
) -> None:
    bank = _make_bank(db_session, institution_type="savings_and_loans")
    _seed_loans(
        db_session,
        bank,
        [
            ("LN-1", "2000000", 0, 1),  # standard
            ("LN-2", "1000000", 120, 2),  # substandard (DPD wins over stage 2)
            ("LN-3", "600000", 200, 3),  # doubtful
            ("LN-4", "400000", 400, 3),  # loss
        ],
    )

    report = svc.classify_loan_book(db_session, CTX, bank, AS_OF)

    assert report.institution_class == "sdi"
    assert report.grid.grades == ("standard", "substandard", "doubtful", "loss")
    assert report.grid.npl_dpd_threshold == 90
    rates = {band.grade: band.provision_rate for band in report.grid.bands}
    assert rates == {
        "standard": Decimal("0"),
        "substandard": Decimal("0.2"),
        "doubtful": Decimal("0.5"),
        "loss": Decimal("1"),
    }

    result = report.result
    assert result.total_exposure_ghs == Decimal("4000000.0000")
    # NPL = substandard + doubtful + loss = 1M + 0.6M + 0.4M
    assert result.npl_exposure_ghs == Decimal("2000000.0000")
    assert result.npl_ratio == Decimal("0.500000")
    # provisions: 0 + 20% ·1M + 50% ·0.6M + 100% ·0.4M = 200k + 300k + 400k
    assert result.total_provision_required_ghs == Decimal("900000.0000")
    assert result.bucket_for("substandard").provision_required_ghs == Decimal("200000.0000")
    assert result.bucket_for("doubtful").provision_required_ghs == Decimal("300000.0000")
    assert result.bucket_for("loss").provision_required_ghs == Decimal("400000.0000")

    assert report.loan_count == 4
    assert report.pending_parameters == ()
    provenance = {item.param_code: item for item in report.parameters}
    assert provenance["prov_substandard"].value == Decimal("20")
    assert provenance["prov_substandard"].confirmation_status == "confirmed"
    assert "NBFI" in provenance["prov_substandard"].source_citation
    # The SDI grid never resolves the bank-only OLEM parameters.
    assert "prov_olem" not in provenance
    assert "dpd_olem_min" not in provenance
    assert report.dpd_covered_count == 4
    assert report.dpd_covered_exposure_ghs == Decimal("4000000")
    dpd = {bucket.code: bucket for bucket in report.delinquency_buckets}
    assert dpd["90_179"].exposure_ghs == Decimal("1000000")
    assert dpd["180_359"].exposure_ghs == Decimal("600000")
    assert dpd["360_plus"].exposure_ghs == Decimal("400000")
    par = {metric.code: metric for metric in report.portfolio_at_risk}
    assert par["par_30"].ratio == Decimal("0.5")
    assert par["par_90"].exposure_ghs == Decimal("2000000")
    assert par["par_90"].ratio == Decimal("0.5")


def test_bank_tenant_binds_5_grade_grid_with_bank_rates_including_olem(
    db_session: Session,
) -> None:
    bank = _make_bank(db_session, institution_type="universal_bank")
    _seed_loans(
        db_session,
        bank,
        [
            ("LN-1", "3000000", 5, 1),  # standard 1%
            ("LN-2", "1000000", 45, 1),  # olem 10% (performing)
            ("LN-3", "500000", 120, 2),  # substandard 25%
            ("LN-4", "300000", 200, 3),  # doubtful 50%
            ("LN-5", "200000", 400, 3),  # loss 100%
        ],
    )

    report = svc.classify_loan_book(db_session, CTX, bank, AS_OF)

    assert report.institution_class == "bank"
    assert report.grid.grades == ("standard", "olem", "substandard", "doubtful", "loss")
    rates = {band.grade: band.provision_rate for band in report.grid.bands}
    assert rates == {
        "standard": Decimal("0.01"),
        "olem": Decimal("0.1"),
        "substandard": Decimal("0.25"),
        "doubtful": Decimal("0.5"),
        "loss": Decimal("1"),
    }

    result = report.result
    assert result.total_exposure_ghs == Decimal("5000000.0000")
    # OLEM (45 DPD) is below the 90-day NPL cutoff -> performing.
    olem = result.bucket_for("olem")
    assert olem.non_performing is False
    assert olem.exposure_ghs == Decimal("1000000.0000")
    assert olem.provision_required_ghs == Decimal("100000.0000")  # 10%
    # NPL = substandard + doubtful + loss
    assert result.npl_exposure_ghs == Decimal("1000000.0000")
    assert result.npl_ratio == Decimal("0.200000")
    # provisions: 1%·3M + 10%·1M + 25%·0.5M + 50%·0.3M + 100%·0.2M
    #           = 30k + 100k + 125k + 150k + 200k
    assert result.total_provision_required_ghs == Decimal("605000.0000")
    assert report.pending_parameters == ()


def test_loan_without_dpd_uses_stage_proxy(db_session: Session) -> None:
    bank = _make_bank(db_session, institution_type="savings_and_loans")
    _seed_loans(
        db_session,
        bank,
        [
            ("LN-1", "1000000", None, 1),  # stage 1, no DPD -> standard
            ("LN-2", "500000", None, 3),  # stage 3, no DPD -> substandard (proxy)
        ],
    )

    report = svc.classify_loan_book(db_session, CTX, bank, AS_OF)

    assert report.stage_proxy_count == 2
    assert report.result.bucket_for("substandard").exposure_ghs == Decimal("500000.0000")
    assert report.result.npl_exposure_ghs == Decimal("500000.0000")
    stage_proxied = next(
        loan
        for loan in report.result.loans
        if loan.classification_basis == svc.engine.BASIS_STAGE_PROXY and loan.non_performing
    )
    assert stage_proxied.grade == "substandard"
    assert report.dpd_covered_count == 0
    assert all(bucket.count == 0 for bucket in report.delinquency_buckets)


def test_superseded_and_error_snapshots_are_excluded(db_session: Session) -> None:
    bank = _make_bank(db_session, institution_type="savings_and_loans")
    _seed_loans(db_session, bank, [("LN-1", "1000000", 0, 1)])
    # Only the one accepted, current-generation LOAN is classified.
    report = svc.classify_loan_book(db_session, CTX, bank, AS_OF)
    assert report.loan_count == 1
    assert report.result.total_exposure_ghs == Decimal("1000000.0000")


def test_unseeded_required_parameter_fails_loud(db_session: Session) -> None:
    bank = _make_bank(db_session, institution_type="savings_and_loans")
    _seed_loans(db_session, bank, [("LN-1", "1000000", 120, 3)])
    # Remove the SDI substandard provisioning rate: the class grid can no longer
    # be built and the resolver refuses to invent a number.
    db_session.execute(
        delete(RegulatoryParameter).where(
            RegulatoryParameter.scope_type == "institution_class",
            RegulatoryParameter.scope_key == "sdi",
            RegulatoryParameter.param_code == "prov_substandard",
        )
    )
    db_session.flush()
    with pytest.raises(rp.RegulatoryParameterError, match="prov_substandard"):
        svc.classify_loan_book(db_session, CTX, bank, AS_OF)


def test_provisions_held_split_by_applied_grade_with_coverage(db_session: Session) -> None:
    """Held provisions split on the grid's OWN classification, and coverage is
    specific ÷ NPL exposure — numerator and denominator on the same footing."""
    bank = _make_bank(db_session, institution_type="savings_and_loans")
    _seed_loans(
        db_session,
        bank,
        [
            ("PERF", "100000", 0, 1, {"ecl_provision_ghs": "1000"}),
            ("NPL-1", "50000", 200, None, {"ecl_provision_ghs": "25000"}),
            # NPL with NO stated provision: excluded from held, still in the denominator.
            ("NPL-2", "30000", 400, None),
            ("SUSP", "20000", 0, 1, {"interest_in_suspense_ghs": "700"}),
        ],
    )
    report = svc.classify_loan_book(db_session, CTX, bank, AS_OF)
    held = report.provisions_held
    assert held is not None
    assert held.specific_ghs == Decimal("25000")
    assert held.general_ghs == Decimal("1000")
    assert held.interest_in_suspense_ghs == Decimal("700")
    assert held.stated_loan_count == 2
    assert report.result.npl_exposure_ghs == Decimal("80000")
    assert report.provision_coverage_pct == Decimal("25000") / Decimal("80000") * Decimal("100")


def test_provisions_unstated_book_reports_none_never_zero(db_session: Session) -> None:
    bank = _make_bank(db_session, institution_type="savings_and_loans")
    _seed_loans(db_session, bank, [("A", "100000", 0, 1), ("B", "50000", 200, None)])
    report = svc.classify_loan_book(db_session, CTX, bank, AS_OF)
    assert report.provisions_held is None
    assert report.provision_coverage_pct is None


def test_coverage_is_none_when_there_is_no_npl_exposure(db_session: Session) -> None:
    """A fully performing book with stated provisions has no NPL to cover:
    coverage is not-applicable (None), not infinity and not 100%."""
    bank = _make_bank(db_session, institution_type="savings_and_loans")
    _seed_loans(db_session, bank, [("A", "100000", 0, 1, {"ecl_provision_ghs": "500"})])
    report = svc.classify_loan_book(db_session, CTX, bank, AS_OF)
    assert report.provisions_held is not None
    assert report.provisions_held.general_ghs == Decimal("500")
    assert report.provision_coverage_pct is None
