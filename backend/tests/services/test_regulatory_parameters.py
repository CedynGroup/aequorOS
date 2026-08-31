"""Regulatory-parameter control plane — resolver + seed (SDI Phase C).

Pins the contracts every SDI engine depends on: precedence
(institution_type > institution_class > fail-loud), effective-dating, provenance,
and confirmation-status surfacing. The seed catalogue is the single source shared
with the migration and the hermetic conftest seed.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Bank, RegulatoryParameter
from app.services import regulatory_parameters as rp
from tests.api.helpers import ORG_1


def _bank(db: Session, *, institution_type: str) -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name=f"{institution_type} tenant",
        short_name="RP",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="x",
        institution_type=institution_type,
    )
    db.add(bank)
    db.flush()
    return bank


def test_seed_loads_and_covers_the_core_grid(db_session: Session) -> None:
    rows = list(db_session.scalars(select(RegulatoryParameter)))
    assert rows, "control plane seeded"
    codes = {r.param_code for r in rows}
    for expected in (
        "car_min",
        "paid_up_min",
        "single_obligor_limit_pct",
        "large_exposure_limit_pct",
        "primary_liquidity_reserve_pct",
        "prov_loss",
        "narrow_to_volatile",
    ):
        assert expected in codes, expected
    # Every seeded row is approved (visible to the resolver) with full provenance.
    for r in rows:
        assert r.status == "approved"
        assert r.source_citation
        assert r.confirmation_status in ("confirmed", "pending")
        assert r.value_numeric is not None or r.value_json is not None


def test_class_param_resolves_by_class(db_session: Session) -> None:
    bank = _bank(db_session, institution_type="universal_bank")
    sdi = _bank(db_session, institution_type="savings_and_loans")
    assert rp.resolve_decimal(db_session, bank, "car_min") == Decimal("13")
    assert rp.resolve_decimal(db_session, sdi, "car_min") == Decimal("10")
    assert rp.resolve_decimal(db_session, bank, "large_exposure_limit_pct") == Decimal("20")
    assert rp.resolve_decimal(db_session, sdi, "large_exposure_limit_pct") == Decimal("15")
    # Single-obligor is 25% for BOTH classes (Act 930 s.62(1)) — the reconciliation.
    assert rp.resolve_decimal(db_session, bank, "single_obligor_limit_pct") == Decimal("25")
    assert rp.resolve_decimal(db_session, sdi, "single_obligor_limit_pct") == Decimal("25")


def test_institution_type_row_wins_over_class(db_session: Session) -> None:
    # paid_up_min is licence-specific (institution_type scope): S&L 15m, RCB 1m,
    # universal bank 400m — the type row must win, not a coarse class default.
    snl = _bank(db_session, institution_type="savings_and_loans")
    rcb = _bank(db_session, institution_type="rural_community_bank")
    bank = _bank(db_session, institution_type="universal_bank")
    assert rp.resolve_decimal(db_session, snl, "paid_up_min") == Decimal("15")
    assert rp.resolve_decimal(db_session, rcb, "paid_up_min") == Decimal("1")
    assert rp.resolve_decimal(db_session, bank, "paid_up_min") == Decimal("400")


def test_resolution_carries_full_provenance(db_session: Session) -> None:
    sdi = _bank(db_session, institution_type="savings_and_loans")
    resolved = rp.resolve(db_session, sdi, "car_min")
    assert resolved.value == Decimal("10")
    assert resolved.unit == "percent"
    assert resolved.source_citation == "Act 930 s.29"
    assert resolved.confirmation_status == "confirmed"
    assert resolved.is_pending is False
    assert resolved.scope_type == "institution_class"
    assert resolved.scope_key == "sdi"
    assert resolved.effective_from == rp.SEED_EFFECTIVE_FROM
    assert resolved.parameter_id


def test_pending_value_is_surfaced_not_hidden(db_session: Session) -> None:
    sdi = _bank(db_session, institution_type="savings_and_loans")
    resolved = rp.resolve(db_session, sdi, "risk_weight_mortgage")
    assert resolved.value == Decimal("50")
    assert resolved.confirmation_status == "pending"
    assert resolved.is_pending is True


def test_mandatory_resolve_fails_loud_when_unseeded(db_session: Session) -> None:
    bank = _bank(db_session, institution_type="universal_bank")
    with pytest.raises(rp.RegulatoryParameterError, match="not seeded"):
        rp.resolve(db_session, bank, "a_code_that_does_not_exist")


def test_try_resolve_returns_none_for_dormant_param(db_session: Session) -> None:
    bank = _bank(db_session, institution_type="universal_bank")
    assert rp.try_resolve(db_session, bank, "a_code_that_does_not_exist") is None


def test_a_later_generation_supersedes_the_seed(db_session: Session) -> None:
    sdi = _bank(db_session, institution_type="savings_and_loans")
    # Close the seeded row and add a superseding generation effective 2027-01-01.
    db_session.execute(
        delete(RegulatoryParameter).where(
            RegulatoryParameter.param_code == "car_min",
            RegulatoryParameter.scope_key == "sdi",
        )
    )
    db_session.add_all(
        [
            RegulatoryParameter(
                scope_type="institution_class",
                scope_key="sdi",
                param_code="car_min",
                jurisdiction_code="GH",
                value_numeric=Decimal("10"),
                unit="percent",
                source_citation="Act 930 s.29",
                confirmation_status="confirmed",
                effective_from=date(2020, 1, 1),
                effective_to=date(2027, 1, 1),
                status="approved",
                proposed_by="seed",
                approved_by="seed",
            ),
            RegulatoryParameter(
                scope_type="institution_class",
                scope_key="sdi",
                param_code="car_min",
                jurisdiction_code="GH",
                value_numeric=Decimal("12"),
                unit="percent",
                source_citation="Hypothetical 2027 uplift",
                confirmation_status="confirmed",
                effective_from=date(2027, 1, 1),
                effective_to=None,
                status="approved",
                proposed_by="seed",
                approved_by="checker",
            ),
        ]
    )
    db_session.flush()
    assert rp.resolve_decimal(db_session, sdi, "car_min", as_of=date(2026, 6, 30)) == Decimal("10")
    assert rp.resolve_decimal(db_session, sdi, "car_min", as_of=date(2027, 6, 30)) == Decimal("12")

    prefetched = rp.PrefetchedParameterResolver.load(
        db_session,
        sdi,
        as_of_dates=[date(2026, 6, 30), date(2027, 6, 30)],
    )
    assert prefetched.resolve("car_min", as_of=date(2026, 6, 30)).decimal == Decimal("10")
    assert prefetched.resolve("car_min", as_of=date(2027, 6, 30)).decimal == Decimal("12")


def test_draft_rows_are_invisible_to_the_resolver(db_session: Session) -> None:
    sdi = _bank(db_session, institution_type="finance_house")
    db_session.add(
        RegulatoryParameter(
            scope_type="institution_class",
            scope_key="sdi",
            param_code="a_draft_only_code",
            jurisdiction_code="GH",
            value_numeric=Decimal("99"),
            unit="percent",
            source_citation="draft",
            confirmation_status="pending",
            effective_from=date(2020, 1, 1),
            status="draft",
            proposed_by="maker",
        )
    )
    db_session.flush()
    assert rp.try_resolve(db_session, sdi, "a_draft_only_code") is None


def test_normalized_value_strips_numeric_scale_padding() -> None:
    """A Numeric(18,6) round-trips 80 as Decimal('80.000000') on Postgres;
    normalized_value must render byte-identically to the in-code floor (audit M1)."""

    def _rp(value: str) -> rp.ResolvedParameter:
        return rp.ResolvedParameter(
            param_code="narrow_to_volatile",
            value=Decimal(value),
            value_json=None,
            unit="percent",
            source_citation="LMTD 2026 ¶9",
            confirmation_status="confirmed",
            scope_type="institution_class",
            scope_key="bank",
            jurisdiction_code="GH",
            effective_from=date(2020, 1, 1),
            parameter_id="id",
        )

    assert str(_rp("80.000000").normalized_value) == "80"
    assert str(_rp("100.000000").normalized_value) == "100"
    assert str(_rp("12.500000").normalized_value) == "12.5"
    assert str(_rp("0.000000").normalized_value) == "0"
