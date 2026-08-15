"""Vendor-blind market data consumption: §15 arbitration, staleness, history."""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import (
    Bank,
    CanonicalCounterpartyRating,
    CanonicalFxRate,
    CanonicalMarketIndex,
    CanonicalYieldCurve,
    CanonicalYieldCurvePoint,
    DeskCurveDefinition,
    IngestionBatch,
    LineageRecord,
)
from app.services import market_data
from app.services.market_desk import entitlements
from tests.api.helpers import ORG_1

AS_OF = date(2026, 7, 15)
EARLIER = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)
LATER = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
# Within the same business day as LATER, so LATER-pulled data is still fresh.
NOW = datetime(2026, 7, 15, 13, 0, tzinfo=UTC)
FAR_FUTURE = NOW + timedelta(days=30)


def _bank(db_session: Session) -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name="Market Data Test Bank",
        short_name="MDTB",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
    )
    db_session.add(bank)
    db_session.flush()
    return bank


def _meta(
    db_session: Session,
    bank: Bank,
    *,
    source_system: str,
    as_of: date,
    ingested_at: datetime,
) -> dict[str, Any]:
    """One batch + lineage node per seeded generation, like a real pull."""
    batch = IngestionBatch(
        organization_id=ORG_1,
        bank_id=bank.id,
        source_system=source_system,
        adapter_version="1.0",
        extraction_mode="full",
        status="accepted",
        as_of_date=as_of,
    )
    db_session.add(batch)
    db_session.flush()
    lineage = LineageRecord(
        organization_id=ORG_1,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref="market-data-test-fixture",
        input_lineage_ids=[],
    )
    db_session.add(lineage)
    db_session.flush()
    return {
        "organization_id": ORG_1,
        "bank_id": bank.id,
        "as_of_date": as_of,
        "ingested_at": ingested_at,
        "source_system": source_system,
        "ingestion_batch_id": batch.id,
        "lineage_id": lineage.id,
        "validation_status": "accepted",
    }


def _seed_curve(  # noqa: PLR0913 - fixture knob for every arbitration axis
    db_session: Session,
    bank: Bank,
    *,
    curve_name: str,
    source_system: str,
    ingested_at: datetime,
    rates: dict[int, str],
    as_of: date = AS_OF,
    currency: str = "GHS",
    curve_type: str = "sovereign",
) -> CanonicalYieldCurve:
    meta = _meta(
        db_session, bank, source_system=source_system, as_of=as_of, ingested_at=ingested_at
    )
    curve = CanonicalYieldCurve(
        **meta,
        source_reference=f"{source_system}/{curve_name}",
        currency=currency,
        curve_name=curve_name,
        curve_type=curve_type,
    )
    db_session.add(curve)
    db_session.flush()
    for tenor_months, rate in rates.items():
        db_session.add(
            CanonicalYieldCurvePoint(
                **meta,
                source_reference=f"{source_system}/{curve_name}/{tenor_months}m",
                yield_curve_id=curve.id,
                tenor_months=tenor_months,
                rate=Decimal(rate),
            )
        )
    db_session.flush()
    return curve


def _seed_spot(  # noqa: PLR0913 - fixture knob for every arbitration axis
    db_session: Session,
    bank: Bank,
    *,
    rate: str,
    as_of: date,
    source_system: str = "BLOOMBERG",
    ingested_at: datetime = LATER,
    base_currency: str = "USD",
    quote_currency: str = "GHS",
) -> CanonicalFxRate:
    meta = _meta(
        db_session, bank, source_system=source_system, as_of=as_of, ingested_at=ingested_at
    )
    row = CanonicalFxRate(
        **meta,
        source_reference=f"{source_system}/{base_currency}{quote_currency}/{as_of.isoformat()}",
        base_currency=base_currency,
        quote_currency=quote_currency,
        rate_type="spot",
        tenor_months=None,
        rate=Decimal(rate),
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_most_recently_refreshed_source_wins_for_same_as_of(db_session: Session) -> None:
    bank = _bank(db_session)
    _seed_curve(
        db_session,
        bank,
        curve_name="GHS_SOVEREIGN",
        source_system="MANUAL_UPLOAD",
        ingested_at=EARLIER,
        rates={12: "0.20"},
    )
    _seed_curve(
        db_session,
        bank,
        curve_name="GHS_SOVEREIGN_BVAL",
        source_system="BLOOMBERG",
        ingested_at=LATER,
        rates={12: "0.185", 1: "0.14", 60: "0.22"},
    )

    view = market_data.get_yield_curve(db_session, ORG_1, bank.id, "GHS", AS_OF, now=NOW)

    assert view is not None
    assert view.curve_name == "GHS_SOVEREIGN_BVAL"
    assert view.attribution.source_system == "BLOOMBERG"
    # Points come back tenor-sorted as decimal fractions.
    assert [tenor for tenor, _ in view.points] == [1, 12, 60]
    assert view.points[1][1] == Decimal("0.185")
    assert view.attribution.stale is False


def test_latest_as_of_date_wins_over_ingestion_recency(db_session: Session) -> None:
    bank = _bank(db_session)
    _seed_curve(
        db_session,
        bank,
        curve_name="GHS_SOVEREIGN_OLD",
        source_system="BLOOMBERG",
        as_of=AS_OF - timedelta(days=1),
        ingested_at=LATER,
        rates={12: "0.19"},
    )
    _seed_curve(
        db_session,
        bank,
        curve_name="GHS_SOVEREIGN",
        source_system="MANUAL_UPLOAD",
        as_of=AS_OF,
        ingested_at=EARLIER,
        rates={12: "0.185"},
    )

    view = market_data.get_yield_curve(db_session, ORG_1, bank.id, "GHS", AS_OF, now=NOW)

    assert view is not None
    assert view.curve_name == "GHS_SOVEREIGN"
    assert view.as_of_date == AS_OF

    # An as-of before the newer generation serves the older business date.
    older = market_data.get_yield_curve(
        db_session, ORG_1, bank.id, "GHS", AS_OF - timedelta(days=1), now=NOW
    )
    assert older is not None
    assert older.curve_name == "GHS_SOVEREIGN_OLD"


def test_superseded_generations_are_invisible(db_session: Session) -> None:
    bank = _bank(db_session)
    _seed_curve(
        db_session,
        bank,
        curve_name="GHS_SOVEREIGN",
        source_system="MANUAL_UPLOAD",
        ingested_at=EARLIER,
        rates={12: "0.20"},
    )
    replaced = _seed_curve(
        db_session,
        bank,
        curve_name="GHS_SOVEREIGN_BVAL",
        source_system="BLOOMBERG",
        ingested_at=LATER,
        rates={12: "0.185"},
    )
    replaced.superseded_by = uuid4()
    db_session.flush()

    view = market_data.get_yield_curve(db_session, ORG_1, bank.id, "GHS", AS_OF, now=NOW)

    assert view is not None
    assert view.curve_name == "GHS_SOVEREIGN"
    assert view.attribution.source_system == "MANUAL_UPLOAD"


def test_staleness_tag_flips_with_age(db_session: Session) -> None:
    bank = _bank(db_session)
    _seed_curve(
        db_session,
        bank,
        curve_name="GHS_SOVEREIGN",
        source_system="REFINITIV",
        ingested_at=LATER,
        rates={12: "0.185"},
    )

    fresh = market_data.get_yield_curve(db_session, ORG_1, bank.id, "GHS", AS_OF, now=NOW)
    assert fresh is not None
    assert fresh.attribution.stale is False
    assert fresh.attribution.age_seconds == (NOW - LATER).total_seconds()

    stale = market_data.get_yield_curve(db_session, ORG_1, bank.id, "GHS", AS_OF, now=FAR_FUTURE)
    assert stale is not None
    assert stale.attribution.stale is True
    assert stale.attribution.age_seconds > fresh.attribution.age_seconds


def test_get_yield_curve_curve_name_filter_is_opt_in(db_session: Session) -> None:
    """Default arbitration is untouched; curve_name narrows to one series."""
    bank = _bank(db_session)
    _seed_curve(
        db_session,
        bank,
        curve_name="AEQ.GHS.SOV.ZERO",
        source_system="AEQUOR_DESK",
        ingested_at=EARLIER,
        rates={12: "0.20"},
        curve_type="zero",
    )
    _seed_curve(
        db_session,
        bank,
        curve_name="AEQ.GHS.OIS",
        source_system="AEQUOR_DESK",
        ingested_at=LATER,
        rates={12: "0.185"},
        curve_type="discount",
    )

    # Default (no curve_name): historical single-winner arbitration by currency.
    default = market_data.get_yield_curve(db_session, ORG_1, bank.id, "GHS", AS_OF, now=NOW)
    assert default is not None
    assert default.curve_name == "AEQ.GHS.OIS"  # most recently ingested wins

    named = market_data.get_yield_curve(
        db_session, ORG_1, bank.id, "GHS", AS_OF, curve_name="AEQ.GHS.SOV.ZERO", now=NOW
    )
    assert named is not None
    assert named.curve_name == "AEQ.GHS.SOV.ZERO"
    assert named.curve_type == "zero"
    assert named.points == ((12, Decimal("0.20")),)

    missing = market_data.get_yield_curve(
        db_session, ORG_1, bank.id, "GHS", AS_OF, curve_name="AEQ.GHS.NOPE", now=NOW
    )
    assert missing is None


def test_list_yield_curves_serves_every_current_curve(db_session: Session) -> None:
    """Multi-curve read: one view per (currency, curve_name), each arbitrated."""
    bank = _bank(db_session)
    _seed_curve(
        db_session,
        bank,
        curve_name="AEQ.GHS.SOV.ZERO",
        source_system="AEQUOR_DESK",
        ingested_at=LATER,
        rates={3: "0.24", 12: "0.22"},
        curve_type="zero",
    )
    _seed_curve(
        db_session,
        bank,
        curve_name="AEQ.GHS.SOV.FWD",
        source_system="AEQUOR_DESK",
        ingested_at=LATER,
        rates={12: "0.21"},
        curve_type="forward",
    )
    _seed_curve(
        db_session,
        bank,
        curve_name="AEQ.GHS.OIS",
        source_system="AEQUOR_DESK",
        ingested_at=LATER,
        rates={1: "0.26"},
        curve_type="discount",
    )
    # Two generations of the same name: the newer business date must win
    # WITHIN the name, not collapse the whole currency to one curve.
    _seed_curve(
        db_session,
        bank,
        curve_name="USD_TREASURY",
        source_system="BLOOMBERG",
        ingested_at=EARLIER,
        as_of=AS_OF - timedelta(days=2),
        rates={12: "0.043"},
        currency="USD",
    )
    _seed_curve(
        db_session,
        bank,
        curve_name="USD_TREASURY",
        source_system="BLOOMBERG",
        ingested_at=LATER,
        as_of=AS_OF,
        rates={12: "0.045"},
        currency="USD",
    )

    views = market_data.list_yield_curves(db_session, ORG_1, bank.id, as_of=AS_OF, now=NOW)

    assert [(v.currency, v.curve_name, v.curve_type) for v in views] == [
        ("GHS", "AEQ.GHS.OIS", "discount"),
        ("GHS", "AEQ.GHS.SOV.FWD", "forward"),
        ("GHS", "AEQ.GHS.SOV.ZERO", "zero"),
        ("USD", "USD_TREASURY", "sovereign"),
    ]
    usd = views[-1]
    assert usd.as_of_date == AS_OF
    assert usd.points == ((12, Decimal("0.045")),)

    # Currency filter narrows without changing per-name arbitration.
    ghs_only = market_data.list_yield_curves(
        db_session, ORG_1, bank.id, as_of=AS_OF, currency="ghs", now=NOW
    )
    assert [v.curve_name for v in ghs_only] == [
        "AEQ.GHS.OIS",
        "AEQ.GHS.SOV.FWD",
        "AEQ.GHS.SOV.ZERO",
    ]


def test_fx_spot_serves_latest_generation_with_attribution(db_session: Session) -> None:
    bank = _bank(db_session)
    _seed_spot(db_session, bank, rate="12.70", as_of=AS_OF - timedelta(days=1))
    _seed_spot(
        db_session, bank, rate="12.85", as_of=AS_OF, source_system="REFINITIV", ingested_at=LATER
    )

    view = market_data.get_fx_spot(db_session, ORG_1, bank.id, "USD", "GHS", AS_OF, now=NOW)

    assert view is not None
    assert view.rate == Decimal("12.85")
    assert view.as_of_date == AS_OF
    assert view.attribution.source_system == "REFINITIV"
    assert view.attribution.stale is False

    # Superseding the newest generation falls back to the prior business date.
    newest = db_session.query(CanonicalFxRate).filter_by(as_of_date=AS_OF).one()
    newest.superseded_by = uuid4()
    db_session.flush()
    fallback = market_data.get_fx_spot(db_session, ORG_1, bank.id, "USD", "GHS", AS_OF, now=NOW)
    assert fallback is not None
    assert fallback.rate == Decimal("12.70")


def test_fx_spot_history_is_ascending_and_capped(db_session: Session) -> None:
    bank = _bank(db_session)
    days = 10
    for offset in range(days):
        day = AS_OF - timedelta(days=days - 1 - offset)
        _seed_spot(db_session, bank, rate=f"{Decimal('12.00') + Decimal(offset) / 100}", as_of=day)

    history = market_data.get_fx_spot_history(
        db_session, ORG_1, bank.id, "USD", "GHS", AS_OF, limit=5
    )

    assert len(history) == 5
    dates = [day for day, _ in history]
    assert dates == sorted(dates)
    # The cap keeps the most recent observations, ending at the as-of date.
    assert dates[-1] == AS_OF
    assert dates[0] == AS_OF - timedelta(days=4)
    assert history[-1][1] == Decimal("12.09")

    # Observations after the requested as-of never leak in.
    truncated = market_data.get_fx_spot_history(
        db_session, ORG_1, bank.id, "USD", "GHS", AS_OF - timedelta(days=3)
    )
    assert [day for day, _ in truncated] == sorted(day for day, _ in truncated)
    assert all(day <= AS_OF - timedelta(days=3) for day, _ in truncated)


def test_list_fx_base_currencies_discovers_pairs(db_session: Session) -> None:
    bank = _bank(db_session)
    _seed_spot(db_session, bank, rate="12.85", as_of=AS_OF)
    _seed_spot(db_session, bank, rate="16.20", as_of=AS_OF, base_currency="EUR")

    assert market_data.list_fx_base_currencies(db_session, ORG_1, bank.id, "GHS", AS_OF) == [
        "EUR",
        "USD",
    ]
    assert market_data.list_fx_base_currencies(db_session, ORG_1, bank.id, "NGN", AS_OF) == []


def test_rating_and_index_follow_the_same_arbitration(db_session: Session) -> None:
    bank = _bank(db_session)
    for agency, ingested_at, rating in (("fitch", EARLIER, "B-"), ("sp", LATER, "B")):
        meta = _meta(
            db_session, bank, source_system="BLOOMBERG", as_of=AS_OF, ingested_at=ingested_at
        )
        db_session.add(
            CanonicalCounterpartyRating(
                **meta,
                source_reference=f"rating/{agency}",
                issuer="GHANA_SOVEREIGN",
                agency=agency,
                rating=rating,
                watch_status="stable",
                rating_date=AS_OF - timedelta(days=3),
            )
        )
    for scenario, value, ingested_at in (("base", "0.05", EARLIER), ("adverse", "0.02", LATER)):
        meta = _meta(
            db_session, bank, source_system="REFINITIV", as_of=AS_OF, ingested_at=ingested_at
        )
        db_session.add(
            CanonicalMarketIndex(
                **meta,
                source_reference=f"index/{scenario}",
                index_code="GHANA_GDP_FORECAST",
                value=Decimal(value),
                scenario=scenario,
                horizon_months=12,
            )
        )
    db_session.flush()

    rating = market_data.get_rating(db_session, ORG_1, bank.id, "GHANA_SOVEREIGN", AS_OF, now=NOW)
    assert rating is not None
    assert rating.agency == "sp"  # most recently refreshed wins
    assert rating.rating == "B"
    assert rating.attribution.stale is False

    index = market_data.get_index(db_session, ORG_1, bank.id, "GHANA_GDP_FORECAST", AS_OF, now=NOW)
    assert index is not None
    assert index.scenario == "base"  # scenario is part of the request, not arbitration
    assert index.value == Decimal("0.05")
    adverse = market_data.get_index(
        db_session, ORG_1, bank.id, "GHANA_GDP_FORECAST", AS_OF, scenario="adverse", now=NOW
    )
    assert adverse is not None
    assert adverse.value == Decimal("0.02")


def test_views_expose_no_vendor_concepts_outside_attribution(db_session: Session) -> None:
    bank = _bank(db_session)
    _seed_curve(
        db_session,
        bank,
        curve_name="GHS_SOVEREIGN",
        source_system="BLOOMBERG",
        ingested_at=LATER,
        rates={12: "0.185"},
    )
    _seed_spot(db_session, bank, rate="12.85", as_of=AS_OF)

    curve = market_data.get_yield_curve(db_session, ORG_1, bank.id, "GHS", AS_OF, now=NOW)
    spot = market_data.get_fx_spot(db_session, ORG_1, bank.id, "USD", "GHS", AS_OF, now=NOW)
    assert curve is not None
    assert spot is not None

    assert {f.name for f in dataclasses.fields(curve)} == {
        "currency",
        "curve_name",
        "curve_type",
        "as_of_date",
        "points",
        "attribution",
    }
    assert {f.name for f in dataclasses.fields(spot)} == {
        "base_currency",
        "quote_currency",
        "rate",
        "as_of_date",
        "attribution",
    }
    # The vendor name appears only in the attribution's source_system.
    for view in (curve, spot):
        business_fields = {
            f.name: getattr(view, f.name)
            for f in dataclasses.fields(view)
            if f.name != "attribution"
        }
        assert "BLOOMBERG" not in repr(business_fields)
        assert view.attribution.source_system in ("BLOOMBERG", "REFINITIV", "MANUAL_UPLOAD")


def test_missing_data_returns_none(db_session: Session) -> None:
    bank = _bank(db_session)
    assert market_data.get_yield_curve(db_session, ORG_1, bank.id, "GHS", AS_OF) is None
    assert market_data.get_fx_spot(db_session, ORG_1, bank.id, "USD", "GHS", AS_OF) is None
    assert market_data.get_fx_spot_history(db_session, ORG_1, bank.id, "USD", "GHS", AS_OF) == []
    assert market_data.get_rating(db_session, ORG_1, bank.id, "GHANA_SOVEREIGN", AS_OF) is None
    assert market_data.get_index(db_session, ORG_1, bank.id, "GHS_POLICY_RATE", AS_OF) is None


# ---------------------------------------------------------------------------
# FC-6d: definition entitlement tier gates AEQ.* curve visibility at read
# ---------------------------------------------------------------------------


def _approved_curve_definition(
    db_session: Session, *, curve_code: str, tier: str
) -> DeskCurveDefinition:
    row = DeskCurveDefinition(
        curve_code=curve_code,
        version=1,
        status="approved",
        currency="GHS",
        calendar_name="GHANA",
        curve_kind="forward",
        instrument_set_ref="GHS.SOV",
        interpolation_method="monotone_convex",
        output_daycount="ACT_360",
        payment_interval_months=3,
        curve_frequency="3M",
        entitlement_tier=tier,
        change_rationale="test",
        proposed_by="analyst@aequoros.com",
        approved_by="lead@aequoros.com",
        effective_from=date(2026, 1, 1),
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_premium_curve_tier_hidden_from_standard_org(db_session: Session) -> None:
    """A premium-tier definition hides its published curve from a standard org, even
    though the sovereign name-family gate alone would admit it; a standard-tier curve
    stays visible."""
    bank = _bank(db_session)
    _seed_curve(
        db_session,
        bank,
        curve_name="AEQ.GHS.SOV.FWD",
        source_system="AEQUOR_DESK",
        ingested_at=LATER,
        rates={12: "0.25"},
        curve_type="forward",
    )
    _seed_curve(
        db_session,
        bank,
        curve_name="AEQ.GHS.SOV.ZERO",
        source_system="AEQUOR_DESK",
        ingested_at=LATER,
        rates={12: "0.24"},
        curve_type="zero",
    )
    _approved_curve_definition(db_session, curve_code="AEQ.GHS.SOV.FWD", tier="premium")
    _approved_curve_definition(db_session, curve_code="AEQ.GHS.SOV.ZERO", tier="standard")
    db_session.flush()

    # Default entitlement is the standard tier (no rows) — the premium FWD curve is
    # hidden; the standard ZERO curve is served.
    names = {
        v.curve_name
        for v in market_data.list_yield_curves(db_session, ORG_1, bank.id, as_of=AS_OF, now=NOW)
    }
    assert "AEQ.GHS.SOV.ZERO" in names
    assert "AEQ.GHS.SOV.FWD" not in names

    # Granting premium unlocks the FWD curve.
    entitlements.grant_tier(
        db_session,
        organization_id=ORG_1,
        tier="premium",
        effective_from=date(2026, 1, 1),
        granted_by="ops@aequoros.com",
    )
    db_session.flush()
    names_premium = {
        v.curve_name
        for v in market_data.list_yield_curves(db_session, ORG_1, bank.id, as_of=AS_OF, now=NOW)
    }
    assert {"AEQ.GHS.SOV.FWD", "AEQ.GHS.SOV.ZERO"} <= names_premium
