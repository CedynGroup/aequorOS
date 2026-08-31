"""Per-bank market-data source preference: service-level behaviour.

Covers ``resolve_source_systems`` mapping, preference CRUD + defaults, the
preference-aware arbitration filter and its graceful-fallback flag, the
side-by-side ``/planes`` resolution, the forward grid, org isolation, and the
regression guard that a default (``source_systems=None``) getter is
byte-identical to today.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    AuditEvent,
    Bank,
    CanonicalFxRate,
    CanonicalMarketIndex,
    CanonicalYieldCurve,
    CanonicalYieldCurvePoint,
    DeskDetermination,
    IngestionBatch,
    LineageRecord,
    MarketDataSourcePreference,
)
from app.schemas.market_data_overlays import MarketDataOverlayCreate
from app.schemas.market_data_sources import (
    CategoryPreferenceUpdate,
    PlaneCurveItemRead,
    PlaneIndexItemRead,
    SourcePreferencesUpdate,
)
from app.services import market_data, market_data_overlays, market_data_sources
from tests.api.helpers import ORG_1, ORG_2, USER_1, USER_2

AS_OF = date(2026, 7, 15)
NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)
CTX_2 = TenantContext(organization_id=ORG_2, actor_user_id=USER_2)


# ---------------------------------------------------------------------------
# Seeding helpers (mirrors tests/api/test_market_data_views.py)
# ---------------------------------------------------------------------------


def _seed_bank(db: Session, organization_id: str = ORG_1) -> str:
    bank = Bank(
        organization_id=organization_id,
        name="Source Preference Test Bank",
        short_name="SPTB",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="universal_bank",
    )
    db.add(bank)
    db.flush()
    return bank.id


def _meta(
    db: Session,
    bank_id: str,
    *,
    source_system: str,
    organization_id: str = ORG_1,
    as_of: date = AS_OF,
) -> dict[str, Any]:
    batch = IngestionBatch(
        organization_id=organization_id,
        bank_id=bank_id,
        source_system=source_system,
        adapter_version="1.0",
        extraction_mode="full",
        status="accepted",
        as_of_date=as_of,
    )
    db.add(batch)
    db.flush()
    lineage = LineageRecord(
        organization_id=organization_id,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref="source-preference-test-fixture",
        input_lineage_ids=[],
    )
    db.add(lineage)
    db.flush()
    return {
        "organization_id": organization_id,
        "bank_id": bank_id,
        "as_of_date": as_of,
        "ingested_at": NOW,
        "source_system": source_system,
        "ingestion_batch_id": batch.id,
        "lineage_id": lineage.id,
        "validation_status": "accepted",
    }


def _seed_curve(  # noqa: PLR0913 - a fixture knob per curve identity axis
    db: Session,
    bank_id: str,
    *,
    curve_name: str,
    source_system: str,
    rates: dict[int, str],
    curve_type: str = "sovereign",
    organization_id: str = ORG_1,
    as_of: date = AS_OF,
    currency: str = "GHS",
) -> None:
    meta = _meta(
        db,
        bank_id,
        source_system=source_system,
        organization_id=organization_id,
        as_of=as_of,
    )
    curve = CanonicalYieldCurve(
        **meta,
        source_reference=f"{source_system}/{curve_name}",
        currency=currency,
        curve_name=curve_name,
        curve_type=curve_type,
    )
    db.add(curve)
    db.flush()
    for tenor_months, rate in rates.items():
        db.add(
            CanonicalYieldCurvePoint(
                **meta,
                source_reference=f"{source_system}/{curve_name}/{tenor_months}m",
                yield_curve_id=curve.id,
                tenor_months=tenor_months,
                rate=Decimal(rate),
            )
        )
    db.flush()


def _seed_fx(db: Session, bank_id: str, *, source_system: str, rate: str) -> None:
    meta = _meta(db, bank_id, source_system=source_system)
    db.add(
        CanonicalFxRate(
            **meta,
            source_reference=f"{source_system}/USDGHS",
            base_currency="USD",
            quote_currency="GHS",
            rate_type="spot",
            tenor_months=None,
            rate=Decimal(rate),
        )
    )
    db.flush()


def _seed_index(db: Session, bank_id: str, *, source_system: str, value: str) -> None:
    meta = _meta(db, bank_id, source_system=source_system)
    db.add(
        CanonicalMarketIndex(
            **meta,
            source_reference=f"{source_system}/GHS.MPR/base",
            index_code="GHS.MPR",
            value=Decimal(value),
            scenario="base",
            horizon_months=None,
        )
    )
    db.flush()


def _seed_three_source_curves(db: Session, bank_id: str) -> None:
    _seed_curve(
        db,
        bank_id,
        curve_name="AEQ.GHS.SOV.ZERO",
        source_system="AEQUOR_DESK",
        curve_type="zero",
        rates={3: "0.24", 12: "0.25"},
    )
    _seed_curve(
        db,
        bank_id,
        curve_name="GHS_SOVEREIGN",
        source_system="BLOOMBERG",
        rates={3: "0.20", 12: "0.21"},
    )
    _seed_curve(
        db,
        bank_id,
        curve_name="GHS_BANK",
        source_system="MANUAL_UPLOAD",
        rates={3: "0.30", 12: "0.31"},
    )


# ---------------------------------------------------------------------------
# resolve_source_systems mapping (spec §2 table)
# ---------------------------------------------------------------------------


def test_resolve_source_systems_mapping() -> None:
    present = {"AEQUOR_DESK", "BLOOMBERG", "REFINITIV", "MANUAL_UPLOAD", "API_PUSH"}
    assert market_data_sources.resolve_source_systems("aequor", present) == ("AEQUOR_DESK",)
    assert market_data_sources.resolve_source_systems("vendor", present) == (
        "BLOOMBERG",
        "REFINITIV",
    )
    # bank = present minus aequor/vendor, sorted.
    assert market_data_sources.resolve_source_systems("bank", present) == (
        "API_PUSH",
        "MANUAL_UPLOAD",
    )
    # aequor/vendor are fixed even when absent from the bank's data.
    assert market_data_sources.resolve_source_systems("vendor", set()) == (
        "BLOOMBERG",
        "REFINITIV",
    )
    assert market_data_sources.resolve_source_systems("bank", set()) == ()


# ---------------------------------------------------------------------------
# Preference CRUD + defaults
# ---------------------------------------------------------------------------


def test_get_preference_synthesises_default(db_session: Session) -> None:
    bank_id = _seed_bank(db_session)
    pref = market_data_sources.get_preference(db_session, CTX, bank_id)
    for category in ("curves", "fx", "rates"):
        choice = getattr(pref, category)
        assert choice.source == "aequor"
        assert choice.overlay is True
    assert pref.updated_at is None
    assert pref.updated_by is None


def test_set_preference_upsert_and_audit(db_session: Session) -> None:
    bank_id = _seed_bank(db_session)
    patch = SourcePreferencesUpdate(
        curves=CategoryPreferenceUpdate(source="vendor", overlay=False),
        reason="switch curves to the licensed vendor feed",
    )
    result = market_data_sources.set_preference(db_session, CTX, bank_id, patch)
    assert result.curves.source == "vendor"
    assert result.curves.overlay is False
    # Untouched categories fall to the defaults on first write.
    assert result.fx.source == "aequor"
    assert result.fx.overlay is True
    assert result.updated_by == USER_1

    row = db_session.scalar(
        select(MarketDataSourcePreference).where(
            MarketDataSourcePreference.organization_id == ORG_1,
            MarketDataSourcePreference.bank_id == bank_id,
        )
    )
    assert row is not None
    audit = db_session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "market_data_source_preference.updated")
    ).all()
    assert len(audit) == 1
    assert audit[0].details["curves_source"] == "vendor"
    assert audit[0].details["reason"] == "switch curves to the licensed vendor feed"


def test_set_preference_partial_patch_preserves_other_categories(db_session: Session) -> None:
    bank_id = _seed_bank(db_session)
    market_data_sources.set_preference(
        db_session,
        CTX,
        bank_id,
        SourcePreferencesUpdate(fx=CategoryPreferenceUpdate(source="bank")),
    )
    result = market_data_sources.set_preference(
        db_session,
        CTX,
        bank_id,
        SourcePreferencesUpdate(rates=CategoryPreferenceUpdate(overlay=False)),
    )
    assert result.fx.source == "bank"  # kept from the first write
    assert result.rates.overlay is False
    assert result.curves.source == "aequor"


# ---------------------------------------------------------------------------
# Preference-aware arbitration (source filter + graceful fallback)
# ---------------------------------------------------------------------------


def test_preferred_projection_curve_honours_source_selection(db_session: Session) -> None:
    bank_id = _seed_bank(db_session)
    _seed_three_source_curves(db_session, bank_id)

    # Default (aequor): the desk sovereign zero drives FTP.
    default_view = market_data_sources.preferred_projection_curve(
        db_session, ORG_1, bank_id, "GHS", AS_OF, now=NOW
    )
    assert default_view is not None
    assert default_view.curve_name == "AEQ.GHS.SOV.ZERO"
    assert default_view.attribution.source_system == "AEQUOR_DESK"
    assert default_view.attribution.fell_back is False

    # Vendor selection serves the licensed feed, no fallback.
    market_data_sources.set_preference(
        db_session,
        CTX,
        bank_id,
        SourcePreferencesUpdate(curves=CategoryPreferenceUpdate(source="vendor")),
    )
    vendor_view = market_data_sources.preferred_projection_curve(
        db_session, ORG_1, bank_id, "GHS", AS_OF, now=NOW
    )
    assert vendor_view is not None
    assert vendor_view.attribution.source_system == "BLOOMBERG"
    assert vendor_view.attribution.fell_back is False

    # Bank selection serves the bank's own uploaded curve.
    market_data_sources.set_preference(
        db_session,
        CTX,
        bank_id,
        SourcePreferencesUpdate(curves=CategoryPreferenceUpdate(source="bank")),
    )
    bank_view = market_data_sources.preferred_projection_curve(
        db_session, ORG_1, bank_id, "GHS", AS_OF, now=NOW
    )
    assert bank_view is not None
    assert bank_view.attribution.source_system == "MANUAL_UPLOAD"


def test_preferred_projection_curve_graceful_fallback_flag(db_session: Session) -> None:
    bank_id = _seed_bank(db_session)
    # Only a vendor curve exists; the bank selects its own (absent) plane.
    _seed_curve(
        db_session,
        bank_id,
        curve_name="GHS_SOVEREIGN",
        source_system="BLOOMBERG",
        rates={3: "0.20", 12: "0.21"},
    )
    market_data_sources.set_preference(
        db_session,
        CTX,
        bank_id,
        SourcePreferencesUpdate(curves=CategoryPreferenceUpdate(source="bank")),
    )
    view = market_data_sources.preferred_projection_curve(
        db_session, ORG_1, bank_id, "GHS", AS_OF, now=NOW
    )
    assert view is not None  # the calculation never breaks on a preferred-plane gap
    assert view.attribution.source_system == "BLOOMBERG"
    assert view.attribution.fell_back is True
    assert view.attribution.requested_source == "bank"
    assert view.attribution.served_source == "BLOOMBERG"


def test_prefetched_curves_match_scalar_resolvers_for_every_requested_date(
    db_session: Session,
) -> None:
    """The bounded IRR request loader preserves both curve-selection contracts."""
    bank_id = _seed_bank(db_session)
    _seed_curve(
        db_session,
        bank_id,
        curve_name="AEQ.GHS.SOV.ZERO",
        curve_type="zero",
        source_system="AEQUOR_DESK",
        rates={3: "0.24", 12: "0.25"},
    )
    _seed_curve(
        db_session,
        bank_id,
        curve_name="AEQ.GHS.OIS",
        curve_type="discount",
        source_system="AEQUOR_DESK",
        rates={3: "0.22", 12: "0.23"},
    )
    db_session.commit()
    for curve_name, bps in (("AEQ.GHS.SOV.ZERO", "50"), ("AEQ.GHS.OIS", "25")):
        market_data_overlays.create_overlay(
            db_session,
            CTX,
            bank_id,
            MarketDataOverlayCreate(
                base_ref_kind="curve",
                base_curve_name=curve_name,
                adjustment_type="additive_bps",
                value=Decimal(bps),
                component_tag="other",
                effective_from=AS_OF,
            ),
        )
    dates = [AS_OF, AS_OF + timedelta(days=1)]

    prefetched = market_data_sources.prefetch_preferred_curves(
        db_session, ORG_1, bank_id, "GHS", dates, now=NOW
    )

    for as_of in dates:
        assert prefetched.projection[as_of] == market_data_sources.preferred_projection_curve(
            db_session, ORG_1, bank_id, "GHS", as_of, now=NOW
        )
        assert prefetched.discount[as_of] == market_data_sources.preferred_discount_curve(
            db_session, ORG_1, bank_id, "GHS", as_of, now=NOW
        )


def test_prefetched_curve_payload_stays_bounded_with_long_history(
    db_session: Session,
) -> None:
    bank_id = _seed_bank(db_session)
    for days_ago in range(180, 0, -1):
        _seed_curve(
            db_session,
            bank_id,
            curve_name=f"GHS.HISTORY.{days_ago}",
            source_system="MANUAL_UPLOAD",
            rates={3: "0.20", 12: "0.21"},
            as_of=AS_OF - timedelta(days=days_ago),
        )
        _seed_curve(
            db_session,
            bank_id,
            curve_name=f"USD.HISTORY.{days_ago}",
            source_system="MANUAL_UPLOAD",
            rates={3: "0.04", 12: "0.05"},
            as_of=AS_OF - timedelta(days=days_ago),
            currency="USD",
        )
    _seed_curve(
        db_session,
        bank_id,
        curve_name="GHS.CURRENT",
        source_system="MANUAL_UPLOAD",
        rates={3: "0.24", 12: "0.25"},
    )
    point_parameter_counts: list[int] = []

    def capture_points(
        _connection: object,
        _cursor: object,
        statement: str,
        parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if "from canonical_yield_curve_points" in statement.lower():
            point_parameter_counts.append(len(parameters))  # type: ignore[arg-type]

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", capture_points)
    try:
        prefetched = market_data_sources.prefetch_preferred_curves(
            db_session, ORG_1, bank_id, "GHS", [AS_OF], now=NOW
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture_points)

    scalar = market_data_sources.preferred_projection_curve(
        db_session, ORG_1, bank_id, "GHS", AS_OF, now=NOW
    )
    assert prefetched.projection[AS_OF] == scalar
    assert point_parameter_counts == [4]


def test_preferred_fx_spot_source_filter_and_fallback(db_session: Session) -> None:
    bank_id = _seed_bank(db_session)
    # The canonical FX unique key excludes source_system, so a pair carries one
    # current-generation row per date — here the licensed vendor feed.
    _seed_fx(db_session, bank_id, source_system="REFINITIV", rate="13.0")

    # Default (aequor) has no desk FX → graceful fallback to any-source, flagged.
    default_spot = market_data_sources.preferred_fx_spot(
        db_session, ORG_1, bank_id, "USD", "GHS", AS_OF, now=NOW
    )
    assert default_spot is not None
    assert default_spot.attribution.source_system == "REFINITIV"
    assert default_spot.attribution.fell_back is True
    assert default_spot.attribution.requested_source == "aequor"
    assert default_spot.attribution.served_source == "REFINITIV"

    # Selecting the vendor plane serves the same row with no fallback.
    market_data_sources.set_preference(
        db_session,
        CTX,
        bank_id,
        SourcePreferencesUpdate(fx=CategoryPreferenceUpdate(source="vendor")),
    )
    vendor_spot = market_data_sources.preferred_fx_spot(
        db_session, ORG_1, bank_id, "USD", "GHS", AS_OF, now=NOW
    )
    assert vendor_spot is not None
    assert vendor_spot.attribution.source_system == "REFINITIV"
    assert vendor_spot.attribution.fell_back is False


# ---------------------------------------------------------------------------
# /planes side-by-side
# ---------------------------------------------------------------------------


def test_resolve_planes_side_by_side_availability_and_selection(db_session: Session) -> None:
    bank_id = _seed_bank(db_session)
    _seed_three_source_curves(db_session, bank_id)

    planes = market_data_sources.resolve_planes(db_session, CTX, bank_id, "curves", AS_OF)
    assert planes.category == "curves"
    assert planes.selected_source == "aequor"
    by_source = {plane.source: plane for plane in planes.planes}
    assert set(by_source) == {"aequor", "bank", "vendor"}
    assert by_source["aequor"].available is True
    assert by_source["aequor"].is_selected is True
    assert by_source["vendor"].available is True
    assert by_source["bank"].available is True
    # Each plane serves only its own source's curve.
    (aequor_item,) = by_source["aequor"].items
    assert isinstance(aequor_item, PlaneCurveItemRead)
    assert aequor_item.curve_name == "AEQ.GHS.SOV.ZERO"
    assert by_source["vendor"].items[0].attribution.source_system == "BLOOMBERG"


def test_resolve_planes_marks_absent_plane_unavailable(db_session: Session) -> None:
    bank_id = _seed_bank(db_session)
    # Only an aequor curve exists — bank and vendor planes are empty.
    _seed_curve(
        db_session,
        bank_id,
        curve_name="AEQ.GHS.SOV.ZERO",
        source_system="AEQUOR_DESK",
        curve_type="zero",
        rates={3: "0.24"},
    )
    planes = market_data_sources.resolve_planes(db_session, CTX, bank_id, "curves", AS_OF)
    by_source = {plane.source: plane for plane in planes.planes}
    assert by_source["aequor"].available is True
    assert by_source["bank"].available is False
    assert by_source["bank"].items == []
    assert by_source["vendor"].available is False


def test_resolve_planes_overlay_preview(db_session: Session) -> None:
    bank_id = _seed_bank(db_session)
    _seed_curve(
        db_session,
        bank_id,
        curve_name="AEQ.GHS.SOV.ZERO",
        source_system="AEQUOR_DESK",
        curve_type="zero",
        rates={3: "0.24", 12: "0.25"},
    )
    db_session.commit()
    market_data_overlays.create_overlay(
        db_session,
        CTX,
        bank_id,
        MarketDataOverlayCreate(
            base_ref_kind="curve",
            base_curve_name="AEQ.GHS.SOV.ZERO",
            adjustment_type="additive_bps",
            value=Decimal("50"),
            component_tag="liquidity_premium",
            effective_from=AS_OF,
        ),
    )
    planes = market_data_sources.resolve_planes(db_session, CTX, bank_id, "curves", AS_OF)
    assert planes.overlay.available is True
    assert planes.overlay.delta_preview
    delta = planes.overlay.delta_preview[0]
    assert delta.curve_name == "AEQ.GHS.SOV.ZERO"
    assert delta.delta == Decimal("0.0050")  # +50 bps


def test_resolve_planes_rates_category_has_no_overlay(db_session: Session) -> None:
    bank_id = _seed_bank(db_session)
    _seed_index(db_session, bank_id, source_system="AEQUOR_DESK", value="15.0")
    planes = market_data_sources.resolve_planes(db_session, CTX, bank_id, "rates", AS_OF)
    by_source = {plane.source: plane for plane in planes.planes}
    assert by_source["aequor"].available is True
    index_item = by_source["aequor"].items[0]
    assert isinstance(index_item, PlaneIndexItemRead)
    assert index_item.value == Decimal("15.0")
    assert planes.overlay.available is False


# ---------------------------------------------------------------------------
# Forward grid (FC-5 / G1)
# ---------------------------------------------------------------------------


def test_get_forward_grid_from_approved_determination(db_session: Session) -> None:
    bank_id = _seed_bank(db_session)
    determination = DeskDetermination(
        cob_date=AS_OF,
        methodology_code="GHS_CURVE_V1",
        methodology_version=2,
        input_snapshot=[
            {"instrument": "deposit", "tenor": "3M", "quote": "0.24"},
            {"instrument": "swap", "tenor": "1Y", "quote": "0.25"},
        ],
        input_digest="deadbeef",
        derived_values={
            "curves": {
                "AEQ.GHS.SOV.FWD": {
                    "curve_type": "forward",
                    "points": [
                        {"tenor_months": 3, "rate_pct": 24.0},
                        {"tenor_months": 6, "rate_pct": 25.0},
                    ],
                }
            },
            "curves_qa_passed": True,
        },
        qa_results={},
        status="approved",
        prepared_by="analyst@aequoros.example",
        reviewed_by="supervisor@aequoros.example",
    )
    db_session.add(determination)
    db_session.flush()

    grid = market_data_sources.get_forward_grid(db_session, CTX, bank_id, "AEQ.GHS.SOV.FWD", AS_OF)
    assert grid.curve_name == "AEQ.GHS.SOV.FWD"
    assert grid.currency == "GHS"
    assert grid.methodology_ref == "GHS_CURVE_V1 v2"
    assert grid.grid_is_authoritative is False
    assert grid.frequency == "3M"
    assert grid.available_frequencies == ["3M"]
    assert grid.assumptions is None
    assert len(grid.rows) == 2
    assert grid.rows[0].start == AS_OF
    assert grid.rows[0].end == date(2026, 10, 15)  # +3 months
    assert grid.rows[0].forward_yield == "0.24"
    assert grid.rows[1].forward_yield == "0.25"
    # The endpoint returns the forward period's DF ratio, never a cumulative DF.
    assert Decimal(grid.rows[0].discount_factor) < Decimal("1")
    assert Decimal(grid.rows[1].discount_factor) == Decimal("0.941176470588")
    assert [p.instrument for p in grid.pillars] == ["deposit", "swap"]


def test_get_forward_grid_returns_persisted_calendar_grid_and_assumptions(
    db_session: Session,
) -> None:
    bank_id = _seed_bank(db_session)
    primary_rows = [
        {
            "start": "2026-07-15",
            "end": "2026-07-17",
            "discount_factor": "1.000000000000",
            "forward_yield": "0.000000000000",
        },
        {
            "start": "2026-07-17",
            "end": "2026-10-19",
            "discount_factor": "0.942100000000",
            "forward_yield": "0.245000000000",
        },
    ]
    daily_rows = [
        {
            "start": "2026-07-15",
            "end": "2026-07-15",
            "discount_factor": "1.000000000000",
            "forward_yield": "0.000000000000",
        },
        {
            "start": "2026-07-15",
            "end": "2026-07-16",
            "discount_factor": "0.999300000000",
            "forward_yield": "0.245000000000",
        },
    ]
    determination = DeskDetermination(
        cob_date=AS_OF,
        methodology_code="GHS_CURVE_V2",
        methodology_version=4,
        input_snapshot=[],
        input_digest="authoritative-grid",
        derived_values={
            "curves": {"AEQ.GHS.SOV.FWD": {"curve_type": "forward", "points": []}},
            "forward_grids": {
                "AEQ.GHS.SOV.FWD": {
                    "rows": primary_rows,
                    "grids": {
                        "1D": {"rows": daily_rows},
                        "1M": {"rows": primary_rows},
                        "3M": {"rows": primary_rows},
                        "6M": {"rows": primary_rows},
                        "1Y": {"rows": primary_rows},
                    },
                    "definition": {
                        "version": 7,
                        "calendar_name": "GHANA",
                        "instrument_set_ref": "GHS_BILLS_AGS",
                        "projection_index": "GHS_SOVEREIGN",
                        "discount_curve_code": "AEQ.GHS.OIS",
                        "interpolation_method": "monotone_convex",
                        "output_daycount": "ACT/360",
                        "payment_frequency": "Quarterly",
                        "payment_interval_months": 3,
                        "curve_frequency": "3M",
                        "spot_lag_days": 2,
                        "roll_convention": "modified_following",
                        "extrapolation_rule": "flat_forward",
                    },
                }
            },
            "curves_qa_passed": True,
        },
        qa_results={},
        status="approved",
        prepared_by="analyst@aequoros.example",
        reviewed_by="supervisor@aequoros.example",
    )
    db_session.add(determination)
    db_session.flush()

    grid = market_data_sources.get_forward_grid(db_session, CTX, bank_id, "AEQ.GHS.SOV.FWD", AS_OF)
    assert grid.grid_is_authoritative is True
    assert [(row.start, row.end) for row in grid.rows] == [
        (date(2026, 7, 15), date(2026, 7, 17)),
        (date(2026, 7, 17), date(2026, 10, 19)),
    ]
    assert grid.assumptions is not None
    assert grid.assumptions.calendar_name == "GHANA"
    assert grid.assumptions.curve_frequency == "3M"
    assert grid.frequency == "3M"
    assert grid.available_frequencies == ["1D", "1M", "1Y", "3M", "6M"]

    daily = market_data_sources.get_forward_grid(
        db_session, CTX, bank_id, "AEQ.GHS.SOV.FWD", AS_OF, frequency="1D"
    )
    assert daily.frequency == "1D"
    assert [(row.start, row.end) for row in daily.rows] == [
        (date(2026, 7, 15), date(2026, 7, 15)),
        (date(2026, 7, 15), date(2026, 7, 16)),
    ]


def test_get_forward_grid_404_when_no_determination(db_session: Session) -> None:
    bank_id = _seed_bank(db_session)
    with pytest.raises(HTTPException) as exc:
        market_data_sources.get_forward_grid(db_session, CTX, bank_id, "AEQ.GHS.SOV.FWD", AS_OF)
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Org isolation + regression guard
# ---------------------------------------------------------------------------


def test_preference_is_org_isolated(db_session: Session) -> None:
    bank_id = _seed_bank(db_session, organization_id=ORG_1)
    market_data_sources.set_preference(
        db_session,
        CTX,
        bank_id,
        SourcePreferencesUpdate(curves=CategoryPreferenceUpdate(source="vendor")),
    )
    # A different tenant cannot see the bank at all (404), let alone its preference.
    with pytest.raises(HTTPException) as exc:
        market_data_sources.get_preference(db_session, CTX_2, bank_id)
    assert exc.value.status_code == 404
    # The explicit org filter keeps the row invisible to a cross-tenant load.
    assert market_data_sources.load_preference(db_session, ORG_2, bank_id).curves.source == "aequor"


def test_default_none_getters_are_byte_identical(db_session: Session) -> None:
    """The regression guard: an unfiltered getter is identical with/without the arg."""
    bank_id = _seed_bank(db_session)
    _seed_three_source_curves(db_session, bank_id)
    _seed_fx(db_session, bank_id, source_system="AEQUOR_DESK", rate="12.5")

    baseline_curve = market_data.get_yield_curve(db_session, ORG_1, bank_id, "GHS", AS_OF, now=NOW)
    explicit_none_curve = market_data.get_yield_curve(
        db_session, ORG_1, bank_id, "GHS", AS_OF, source_systems=None, overlay=False, now=NOW
    )
    assert baseline_curve == explicit_none_curve

    baseline_spot = market_data.get_fx_spot(
        db_session, ORG_1, bank_id, "USD", "GHS", AS_OF, now=NOW
    )
    explicit_none_spot = market_data.get_fx_spot(
        db_session, ORG_1, bank_id, "USD", "GHS", AS_OF, source_systems=None, overlay=False, now=NOW
    )
    assert baseline_spot == explicit_none_spot

    baseline_list = market_data.list_yield_curves(db_session, ORG_1, bank_id, as_of=AS_OF, now=NOW)
    explicit_none_list = market_data.list_yield_curves(
        db_session, ORG_1, bank_id, as_of=AS_OF, source_systems=None, overlay=False, now=NOW
    )
    assert baseline_list == explicit_none_list
