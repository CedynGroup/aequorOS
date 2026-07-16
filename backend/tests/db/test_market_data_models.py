from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.ids import new_uuid7
from app.models import (
    Bank,
    CanonicalCounterpartyRating,
    CanonicalFxRate,
    CanonicalMarketIndex,
    CanonicalYieldCurve,
    CanonicalYieldCurvePoint,
    IngestionBatch,
    LineageRecord,
    MarketDataConnection,
    MarketDataQuotaUsage,
)
from tests.api.helpers import ORG_1

AS_OF = date(2026, 6, 30)


def make_bank(session: Session) -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name="Sample Bank Limited",
        short_name="SBL",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
    )
    session.add(bank)
    session.flush()
    return bank


def make_batch(
    session: Session, bank: Bank, *, source_system: str = "BLOOMBERG", as_of: date = AS_OF
) -> IngestionBatch:
    batch = IngestionBatch(
        organization_id=ORG_1,
        bank_id=bank.id,
        source_system=source_system,
        adapter_version="1.0",
        extraction_mode="full",
        status="created",
        as_of_date=as_of,
    )
    session.add(batch)
    session.flush()
    return batch


def make_lineage(session: Session, batch: IngestionBatch) -> LineageRecord:
    record = LineageRecord(
        organization_id=ORG_1,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref="bloomberg_v1.0/market_data",
        input_lineage_ids=[],
    )
    session.add(record)
    session.flush()
    return record


def metadata_for(
    batch: IngestionBatch, lineage: LineageRecord, *, source_reference: str
) -> dict[str, Any]:
    return {
        "organization_id": ORG_1,
        "bank_id": batch.bank_id,
        "as_of_date": batch.as_of_date,
        "source_system": batch.source_system,
        "source_reference": source_reference,
        "ingestion_batch_id": batch.id,
        "lineage_id": lineage.id,
        "validation_status": "accepted",
    }


def build_chain(session: Session) -> tuple[Bank, IngestionBatch, LineageRecord]:
    bank = make_bank(session)
    batch = make_batch(session, bank)
    lineage = make_lineage(session, batch)
    return bank, batch, lineage


def make_curve(
    session: Session,
    batch: IngestionBatch,
    lineage: LineageRecord,
    *,
    currency: str = "GHS",
    curve_name: str = "GHS_SOVEREIGN",
    **overrides: Any,
) -> CanonicalYieldCurve:
    curve = CanonicalYieldCurve(
        **metadata_for(batch, lineage, source_reference=f"CURVE/{currency}/{curve_name}"),
        currency=currency,
        curve_name=curve_name,
        **overrides,
    )
    session.add(curve)
    session.flush()
    return curve


def make_point(  # noqa: PLR0913 - keyword-only fixture builder
    session: Session,
    curve: CanonicalYieldCurve,
    batch: IngestionBatch,
    lineage: LineageRecord,
    *,
    tenor_months: int = 3,
    rate: Decimal = Decimal("0.15800000"),
    **overrides: Any,
) -> CanonicalYieldCurvePoint:
    point = CanonicalYieldCurvePoint(
        **metadata_for(
            batch, lineage, source_reference=f"{curve.source_reference}/{tenor_months}M"
        ),
        yield_curve_id=curve.id,
        tenor_months=tenor_months,
        rate=rate,
        **overrides,
    )
    session.add(point)
    session.flush()
    return point


def make_fx_rate(  # noqa: PLR0913 - keyword-only fixture builder
    session: Session,
    batch: IngestionBatch,
    lineage: LineageRecord,
    *,
    rate_type: str = "spot",
    tenor_months: int | None = None,
    rate: Decimal = Decimal("12.85000000"),
    **overrides: Any,
) -> CanonicalFxRate:
    fx_rate = CanonicalFxRate(
        **metadata_for(
            batch, lineage, source_reference=f"FX/USDGHS/{rate_type}/{tenor_months or 0}"
        ),
        base_currency="USD",
        quote_currency="GHS",
        rate_type=rate_type,
        tenor_months=tenor_months,
        rate=rate,
        **overrides,
    )
    session.add(fx_rate)
    session.flush()
    return fx_rate


def make_index(
    session: Session,
    batch: IngestionBatch,
    lineage: LineageRecord,
    *,
    index_code: str = "GHANA_GDP_FORECAST",
    horizon_months: int | None = 12,
    **overrides: Any,
) -> CanonicalMarketIndex:
    index = CanonicalMarketIndex(
        **metadata_for(
            batch, lineage, source_reference=f"INDEX/{index_code}/{horizon_months or 0}"
        ),
        index_code=index_code,
        value=Decimal("0.048000"),
        horizon_months=horizon_months,
        **overrides,
    )
    session.add(index)
    session.flush()
    return index


def make_rating(
    session: Session,
    batch: IngestionBatch,
    lineage: LineageRecord,
    *,
    issuer: str = "GHANA_SOVEREIGN",
    agency: str = "moodys",
    **overrides: Any,
) -> CanonicalCounterpartyRating:
    rating = CanonicalCounterpartyRating(
        **metadata_for(batch, lineage, source_reference=f"RATING/{issuer}/{agency}"),
        issuer=issuer,
        agency=agency,
        rating="Caa1",
        rating_date=date(2026, 5, 15),
        **overrides,
    )
    session.add(rating)
    session.flush()
    return rating


def make_connection(
    session: Session,
    bank: Bank,
    *,
    vendor: str = "bloomberg",
    display_name: str = "Treasury BLPAPI",
    **overrides: Any,
) -> MarketDataConnection:
    fields: dict[str, Any] = {
        "organization_id": ORG_1,
        "bank_id": bank.id,
        "vendor": vendor,
        "display_name": display_name,
        "credential_ciphertext": "opaque-encrypted-blob",
        "credential_fingerprint": "f" * 64,
        "vault_path": f"vault://institutions/{bank.id}/vendor_credentials/{vendor}/default",
        "scopes": ["YIELD_CURVE_GHS", "FX_SPOT_USD_GHS"],
        "schedule": {"YIELD_CURVE": "END_OF_DAY", "FX_SPOT": "HOURLY"},
    }
    fields.update(overrides)
    connection = MarketDataConnection(**fields)
    session.add(connection)
    session.flush()
    return connection


class TestSourceSystemWidening:
    @pytest.mark.parametrize("source_system", ["BLOOMBERG", "REFINITIV", "MANUAL_UPLOAD"])
    def test_market_data_vendors_are_accepted(
        self, db_session: Session, source_system: str
    ) -> None:
        bank = make_bank(db_session)
        batch = make_batch(db_session, bank, source_system=source_system)
        assert batch.source_system == source_system

    def test_unknown_source_system_is_still_rejected(self, db_session: Session) -> None:
        bank = make_bank(db_session)
        with pytest.raises(IntegrityError):
            make_batch(db_session, bank, source_system="TERMINAL_FEED")


class TestYieldCurves:
    def test_curve_and_points_round_trip(self, db_session: Session) -> None:
        bank, batch, lineage = build_chain(db_session)
        curve = make_curve(db_session, batch, lineage)
        point = make_point(db_session, curve, batch, lineage)
        db_session.refresh(curve)
        db_session.refresh(point)
        assert curve.curve_type == "sovereign"
        assert point.yield_curve_id == curve.id
        assert point.rate == Decimal("0.15800000")

    def test_curve_type_is_constrained(self, db_session: Session) -> None:
        bank, batch, lineage = build_chain(db_session)
        with pytest.raises(IntegrityError):
            make_curve(db_session, batch, lineage, curve_type="astrology")

    def test_duplicate_current_curve_is_rejected(self, db_session: Session) -> None:
        bank, batch, lineage = build_chain(db_session)
        make_curve(db_session, batch, lineage)
        with pytest.raises(IntegrityError):
            make_curve(db_session, batch, lineage)

    def test_superseded_curve_frees_the_natural_key(self, db_session: Session) -> None:
        bank, batch, lineage = build_chain(db_session)
        original = make_curve(db_session, batch, lineage)

        replacement_batch = make_batch(db_session, bank)
        replacement_lineage = make_lineage(db_session, replacement_batch)
        # Column defaults fire at flush, so mint the replacement id up front:
        # the old row must point at its successor before the successor lands.
        replacement_id = new_uuid7()
        original.superseded_by = replacement_id
        db_session.flush()
        replacement = make_curve(
            db_session, replacement_batch, replacement_lineage, id=replacement_id
        )

        assert original.superseded_by == replacement.id
        assert replacement.superseded_by is None

    def test_point_tenor_must_be_positive(self, db_session: Session) -> None:
        bank, batch, lineage = build_chain(db_session)
        curve = make_curve(db_session, batch, lineage)
        with pytest.raises(IntegrityError):
            make_point(db_session, curve, batch, lineage, tenor_months=0)

    def test_point_rate_rejects_percentage_style_values(self, db_session: Session) -> None:
        bank, batch, lineage = build_chain(db_session)
        curve = make_curve(db_session, batch, lineage)
        with pytest.raises(IntegrityError):
            make_point(db_session, curve, batch, lineage, rate=Decimal("15.80000000"))

    def test_duplicate_current_point_tenor_is_rejected(self, db_session: Session) -> None:
        bank, batch, lineage = build_chain(db_session)
        curve = make_curve(db_session, batch, lineage)
        make_point(db_session, curve, batch, lineage, tenor_months=3)
        make_point(db_session, curve, batch, lineage, tenor_months=6)
        with pytest.raises(IntegrityError):
            make_point(db_session, curve, batch, lineage, tenor_months=3)


class TestFxRates:
    def test_spot_round_trips_without_tenor(self, db_session: Session) -> None:
        bank, batch, lineage = build_chain(db_session)
        spot = make_fx_rate(db_session, batch, lineage)
        db_session.refresh(spot)
        assert spot.rate_type == "spot"
        assert spot.tenor_months is None

    def test_spot_with_tenor_is_rejected(self, db_session: Session) -> None:
        bank, batch, lineage = build_chain(db_session)
        with pytest.raises(IntegrityError):
            make_fx_rate(db_session, batch, lineage, rate_type="spot", tenor_months=3)

    def test_forward_without_tenor_is_rejected(self, db_session: Session) -> None:
        bank, batch, lineage = build_chain(db_session)
        with pytest.raises(IntegrityError):
            make_fx_rate(db_session, batch, lineage, rate_type="forward", tenor_months=None)

    def test_rate_must_be_positive(self, db_session: Session) -> None:
        bank, batch, lineage = build_chain(db_session)
        with pytest.raises(IntegrityError):
            make_fx_rate(db_session, batch, lineage, rate=Decimal("0"))

    def test_duplicate_current_spot_is_rejected(self, db_session: Session) -> None:
        bank, batch, lineage = build_chain(db_session)
        make_fx_rate(db_session, batch, lineage)
        with pytest.raises(IntegrityError):
            make_fx_rate(db_session, batch, lineage)

    def test_forwards_at_different_tenors_coexist(self, db_session: Session) -> None:
        bank, batch, lineage = build_chain(db_session)
        make_fx_rate(db_session, batch, lineage, rate_type="forward", tenor_months=3)
        make_fx_rate(db_session, batch, lineage, rate_type="forward", tenor_months=6)
        with pytest.raises(IntegrityError):
            make_fx_rate(db_session, batch, lineage, rate_type="forward", tenor_months=3)

    def test_superseded_spot_frees_the_natural_key(self, db_session: Session) -> None:
        bank, batch, lineage = build_chain(db_session)
        original = make_fx_rate(db_session, batch, lineage)

        replacement_batch = make_batch(db_session, bank)
        replacement_lineage = make_lineage(db_session, replacement_batch)
        replacement_id = new_uuid7()
        original.superseded_by = replacement_id
        db_session.flush()
        replacement = make_fx_rate(
            db_session,
            replacement_batch,
            replacement_lineage,
            rate=Decimal("12.90000000"),
            id=replacement_id,
        )

        assert original.superseded_by == replacement.id
        assert replacement.rate == Decimal("12.90000000")


class TestMarketIndices:
    def test_index_round_trips_with_base_scenario_default(self, db_session: Session) -> None:
        bank, batch, lineage = build_chain(db_session)
        index = make_index(db_session, batch, lineage)
        db_session.refresh(index)
        assert index.scenario == "base"
        assert index.value == Decimal("0.048000")

    def test_scenario_is_constrained(self, db_session: Session) -> None:
        bank, batch, lineage = build_chain(db_session)
        with pytest.raises(IntegrityError):
            make_index(db_session, batch, lineage, scenario="optimistic")

    def test_duplicate_current_observation_is_rejected(self, db_session: Session) -> None:
        bank, batch, lineage = build_chain(db_session)
        make_index(db_session, batch, lineage, horizon_months=None)
        with pytest.raises(IntegrityError):
            make_index(db_session, batch, lineage, horizon_months=None)

    def test_scenarios_and_horizons_partition_the_key(self, db_session: Session) -> None:
        bank, batch, lineage = build_chain(db_session)
        make_index(db_session, batch, lineage, horizon_months=12)
        make_index(db_session, batch, lineage, horizon_months=24)
        make_index(db_session, batch, lineage, horizon_months=12, scenario="adverse")
        with pytest.raises(IntegrityError):
            make_index(db_session, batch, lineage, horizon_months=12)


class TestCounterpartyRatings:
    def test_rating_round_trips(self, db_session: Session) -> None:
        bank, batch, lineage = build_chain(db_session)
        rating = make_rating(db_session, batch, lineage, watch_status="negative")
        db_session.refresh(rating)
        assert rating.rating == "Caa1"
        assert rating.watch_status == "negative"
        assert rating.rating_date == date(2026, 5, 15)

    def test_agency_is_constrained(self, db_session: Session) -> None:
        bank, batch, lineage = build_chain(db_session)
        with pytest.raises(IntegrityError):
            make_rating(db_session, batch, lineage, agency="uncle_kwame")

    def test_watch_status_is_constrained_but_nullable(self, db_session: Session) -> None:
        bank, batch, lineage = build_chain(db_session)
        unwatched = make_rating(db_session, batch, lineage, watch_status=None)
        assert unwatched.watch_status is None
        with pytest.raises(IntegrityError):
            make_rating(
                db_session, batch, lineage, issuer="NIGERIA_SOVEREIGN", watch_status="vibes"
            )

    def test_duplicate_current_issuer_agency_is_rejected(self, db_session: Session) -> None:
        bank, batch, lineage = build_chain(db_session)
        make_rating(db_session, batch, lineage)
        make_rating(db_session, batch, lineage, agency="fitch")
        with pytest.raises(IntegrityError):
            make_rating(db_session, batch, lineage)


class TestMarketDataConnections:
    def test_connection_round_trips_with_testing_default(self, db_session: Session) -> None:
        bank = make_bank(db_session)
        connection = make_connection(db_session, bank)
        db_session.refresh(connection)
        assert connection.status == "TESTING"
        assert connection.scopes == ["YIELD_CURVE_GHS", "FX_SPOT_USD_GHS"]
        assert connection.schedule["YIELD_CURVE"] == "END_OF_DAY"

    def test_manual_upload_carries_no_credentials(self, db_session: Session) -> None:
        bank = make_bank(db_session)
        connection = make_connection(
            db_session,
            bank,
            vendor="manual_upload",
            display_name="Treasury templates",
            credential_ciphertext=None,
            credential_fingerprint=None,
        )
        assert connection.credential_ciphertext is None

    def test_vendor_is_constrained(self, db_session: Session) -> None:
        bank = make_bank(db_session)
        with pytest.raises(IntegrityError):
            make_connection(db_session, bank, vendor="fax_machine")

    def test_status_is_constrained(self, db_session: Session) -> None:
        bank = make_bank(db_session)
        connection = make_connection(db_session, bank)
        connection.status = "SLEEPING"
        with pytest.raises(IntegrityError):
            db_session.flush()

    def test_display_name_is_unique_per_bank_and_vendor(self, db_session: Session) -> None:
        bank = make_bank(db_session)
        make_connection(db_session, bank)
        make_connection(db_session, bank, vendor="refinitiv")
        with pytest.raises(IntegrityError):
            make_connection(db_session, bank)


class TestMarketDataQuotaUsage:
    def make_usage(
        self, session: Session, bank: Bank, *, month: str = "2026-06", **overrides: Any
    ) -> MarketDataQuotaUsage:
        fields: dict[str, Any] = {
            "organization_id": ORG_1,
            "bank_id": bank.id,
            "vendor": "bloomberg",
            "month": month,
            "monthly_cap": 5000,
        }
        fields.update(overrides)
        usage = MarketDataQuotaUsage(**fields)
        session.add(usage)
        session.flush()
        return usage

    def test_counters_default_to_zero(self, db_session: Session) -> None:
        bank = make_bank(db_session)
        usage = self.make_usage(db_session, bank)
        db_session.refresh(usage)
        assert usage.units_consumed == 0
        assert usage.pull_count == 0
        assert usage.monthly_cap == 5000

    def test_one_ledger_row_per_bank_vendor_month(self, db_session: Session) -> None:
        bank = make_bank(db_session)
        self.make_usage(db_session, bank, month="2026-06")
        self.make_usage(db_session, bank, month="2026-07")
        self.make_usage(db_session, bank, month="2026-06", vendor="refinitiv")
        with pytest.raises(IntegrityError):
            self.make_usage(db_session, bank, month="2026-06")
