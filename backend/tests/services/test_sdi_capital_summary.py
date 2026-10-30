"""SDI live s.29 capital adequacy: CAR = Net Own Funds ÷ RWA (docs/sdi.md §4.2).

Computed directly from canonical capital-structure + position data — the Basel
live engine cannot serve an SDI. Risk weights come from the simplified control-
plane buckets (all currently ``pending`` BoG confirmation), never hardcoded.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    CanonicalGlAccount,
    CanonicalReferenceRow,
)
from app.services import sdi_capital, sdi_capital_assurance
from tests.fixtures.sdi_capital import (
    SDI_AS_OF,
    create_sdi_bank,
    create_sdi_batch_lineage,
    seed_sdi_capital,
    seed_sdi_positions,
)
from tests.support.helpers import ORG_1, USER_1

_CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)


def test_car_is_nof_over_rwa_with_control_plane_weights(db_session: Session) -> None:
    sdi = create_sdi_bank(db_session)
    # NOF = 20 + 25 + 10 - 1 = 54m
    seed_sdi_capital(
        db_session,
        sdi,
        [
            ("paid_up_capital", "20000000", "CET1"),
            ("statutory_reserves", "25000000", "CET1"),
            ("retained_earnings", "10000000", "CET1"),
            ("intangible_assets", "1000000", "CET1_DEDUCTION"),
        ],
    )
    # RWA: cash 0% + sovereign 0% + interbank 20% (→4m) + loans 100% (→400m)
    #      + other_assets 100% (→10m) = 414m. Liabilities (deposits, borrowings)
    #      are NEVER risk-weighted — they must not inflate RWA.
    seed_sdi_positions(
        db_session,
        sdi,
        [
            ("CASH/1", "CASH", "15000000"),
            ("SEC/1", "SECURITY_HOLDING", "50000000"),
            ("IB/1", "INTERBANK_PLACEMENT", "20000000"),
            ("LN/1", "LOAN", "400000000"),
            ("OTH/1", "OTHER_ASSET", "10000000"),
            ("DEP/1", "DEPOSIT", "300000000"),  # liability — excluded
            ("BORR/1", "INTERBANK_BORROWING", "50000000"),  # liability — excluded
        ],
    )

    summary = sdi_capital.compute_sdi_capital_summary(db_session, _CTX, sdi, SDI_AS_OF)

    assert summary.net_own_funds_ghs == Decimal("54000000")
    assert summary.total_rwa_ghs == Decimal("414000000")
    assert summary.car_pct == Decimal("13.04")  # 54/414*100
    assert summary.car_min_pct == Decimal("10")  # s.29 SDI floor
    assert summary.status == "green"  # 13.04 >= 10
    assert summary.computable is True
    # every simplified risk weight is still pending BoG confirmation
    assert set(summary.pending_parameters) == {
        "risk_weight_cash",
        "risk_weight_sovereign",
        "risk_weight_interbank",
        "risk_weight_other_loans",
        "risk_weight_other_assets",
    }
    # every band carries its bucket + confirmation status for the UI
    band_by_bucket = {b.bucket: b for b in summary.bands}
    assert band_by_bucket["interbank"].weight_pct == Decimal("20")
    # the 50m interbank BORROWING is a liability — interbank exposure is the 20m
    # placement only, never 70m
    assert band_by_bucket["interbank"].exposure_ghs == Decimal("20000000")
    assert band_by_bucket["other_loans"].weight_pct == Decimal("100")
    assert all(b.confirmation_status == "pending" for b in summary.bands)


def test_deduction_tier_subtracts_from_net_own_funds(db_session: Session) -> None:
    sdi = create_sdi_bank(db_session)
    seed_sdi_capital(
        db_session,
        sdi,
        [
            ("paid_up_capital", "20000000", "CET1"),
            ("goodwill", "5000000", "CET1_DEDUCTION"),
        ],
    )
    seed_sdi_positions(db_session, sdi, [("LN/1", "LOAN", "100000000")])

    summary = sdi_capital.compute_sdi_capital_summary(db_session, _CTX, sdi, SDI_AS_OF)
    assert summary.net_own_funds_ghs == Decimal("15000000")  # 20 - 5


def test_prefetched_net_own_funds_matches_latest_generation_arithmetic(
    db_session: Session,
) -> None:
    sdi = create_sdi_bank(db_session)
    may_end = date(2026, 5, 31)
    seed_sdi_capital(
        db_session,
        sdi,
        [("paid_up_capital", "10000000", "cet1"), ("goodwill", "2000000", "cet1_deduction")],
        as_of=may_end,
    )
    seed_sdi_capital(
        db_session,
        sdi,
        [("paid_up_capital", "20000000", "cet1")],
    )
    seed_sdi_capital(
        db_session,
        sdi,
        [("paid_up_capital", "30000000", "cet1"), ("goodwill", "5000000", "cet1_deduction")],
    )
    dates = [may_end, date(2026, 6, 15), SDI_AS_OF, date(2026, 7, 31)]

    prefetched = sdi_capital.prefetch_net_own_funds(db_session, _CTX, sdi, dates)

    assert prefetched == {
        as_of: sdi_capital.net_own_funds(db_session, _CTX, sdi, as_of) for as_of in dates
    }
    assert prefetched == {
        may_end: Decimal("8000000"),
        date(2026, 6, 15): Decimal("8000000"),
        SDI_AS_OF: Decimal("25000000"),
        date(2026, 7, 31): Decimal("25000000"),
    }


def test_below_floor_flags_red(db_session: Session) -> None:
    sdi = create_sdi_bank(db_session)
    seed_sdi_capital(db_session, sdi, [("paid_up_capital", "5000000", "CET1")])
    seed_sdi_positions(db_session, sdi, [("LN/1", "LOAN", "100000000")])  # 100m RWA

    summary = sdi_capital.compute_sdi_capital_summary(db_session, _CTX, sdi, SDI_AS_OF)
    assert summary.car_pct == Decimal("5.00")  # 5/100
    assert summary.status == "red"  # 5 < 10


def test_accepted_with_warning_positions_are_included_in_rwa(db_session: Session) -> None:
    # Regression: "warning" (accepted-with-warnings) is usable book data, exactly
    # like the loan/fact loaders — dropping it silently understates RWA.
    sdi = create_sdi_bank(db_session)
    seed_sdi_capital(db_session, sdi, [("paid_up_capital", "20000000", "CET1")])
    seed_sdi_positions(db_session, sdi, [("LN/1", "LOAN", "300000000")], status="warning")

    summary = sdi_capital.compute_sdi_capital_summary(db_session, _CTX, sdi, SDI_AS_OF)
    assert summary.total_rwa_ghs == Decimal("300000000")  # the warning loan counts


def test_no_positions_is_not_computable_not_a_false_green(db_session: Session) -> None:
    sdi = create_sdi_bank(db_session)
    seed_sdi_capital(db_session, sdi, [("paid_up_capital", "20000000", "CET1")])
    # no positions → RWA 0 → CAR undefined, never a false green

    summary = sdi_capital.compute_sdi_capital_summary(db_session, _CTX, sdi, SDI_AS_OF)
    assert summary.total_rwa_ghs == Decimal("0")
    assert summary.car_pct is None
    assert summary.status == "na"
    assert summary.computable is False


def test_capital_assurance_marks_unconfirmed_inputs_as_provisional(db_session: Session) -> None:
    sdi = create_sdi_bank(db_session)
    seed_sdi_capital(db_session, sdi, [("paid_up_capital", "20000000", "CET1")])
    seed_sdi_positions(db_session, sdi, [("LN/1", "LOAN", "100000000")])

    assurance = sdi_capital_assurance.get_sdi_capital_assurance(db_session, _CTX, sdi, SDI_AS_OF)

    assert assurance.current.car_pct == Decimal("20.00")
    assert assurance.current.assessment_status == "provisional"
    assert assurance.current.actual_provision_ghs is None
    assert assurance.mapped_gl_capital_ghs is None
    assert assurance.gl_reconciliation_status == "not_mapped"
    assert assurance.history == [assurance.current]
    assert any("risk weights" in blocker for blocker in assurance.filing_blockers)


def test_capital_assurance_reconciles_explicitly_mapped_gl_components(
    db_session: Session,
) -> None:
    sdi = create_sdi_bank(db_session)
    seed_sdi_capital(db_session, sdi, [("paid_up_capital", "20000000", "CET1")])
    capital_row = db_session.scalar(
        select(CanonicalReferenceRow).where(
            CanonicalReferenceRow.organization_id == ORG_1,
            CanonicalReferenceRow.bank_id == sdi.id,
            CanonicalReferenceRow.dataset_kind == "capital_structure",
        )
    )
    assert capital_row is not None
    capital_row.payload = {**capital_row.payload, "gl_account_code": "GL-3100"}
    batch, lineage = create_sdi_batch_lineage(db_session, sdi, "s29-gl")
    db_session.add(
        CanonicalGlAccount(
            organization_id=ORG_1,
            bank_id=sdi.id,
            as_of_date=SDI_AS_OF,
            source_system="EXCEL_CSV",
            source_reference="GL/3100",
            ingestion_batch_id=batch.id,
            lineage_id=lineage.id,
            validation_status="accepted",
            account_code="GL-3100",
            name="Paid-up capital",
            account_class="EQUITY",
            currency="GHS",
            balance=Decimal("20000000"),
        )
    )
    seed_sdi_positions(db_session, sdi, [("LN/1", "LOAN", "100000000")])
    db_session.flush()

    assurance = sdi_capital_assurance.get_sdi_capital_assurance(db_session, _CTX, sdi, SDI_AS_OF)

    assert assurance.mapped_gl_capital_ghs == Decimal("20000000")
    assert assurance.capital_to_gl_difference_ghs == Decimal("0")
    assert assurance.gl_reconciliation_status == "mapped"
