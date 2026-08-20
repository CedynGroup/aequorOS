"""Per-bank market-data source-preference API: endpoints + RBAC + isolation.

Drives the four endpoints under ``/banks/{bank_id}/market-data`` end to end:
source-preferences GET/PUT (defaults, upsert, analyst gate), the ``/planes``
side-by-side comparison, and the published forward grid. Seeds canonical data
directly (like tests/api/test_market_data_views.py), then asserts the frozen
§4 JSON contract the frontend depends on.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.session import get_sessionmaker
from app.models import (
    Bank,
    CanonicalYieldCurve,
    CanonicalYieldCurvePoint,
    DeskDetermination,
    IngestionBatch,
    LineageRecord,
)
from tests.api.helpers import ORG_1, ORG_2, headers

AS_OF = date(2026, 7, 15)
INGESTED_AT = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


def _seed_bank(session: Session) -> str:
    bank = Bank(
        organization_id=ORG_1,
        name="Source Pref API Bank",
        short_name="SPAB",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="universal_bank",
    )
    session.add(bank)
    session.flush()
    return bank.id


def _seed_curve(session: Session, bank_id: str, *, curve_name: str, source_system: str) -> None:
    batch = IngestionBatch(
        organization_id=ORG_1,
        bank_id=bank_id,
        source_system=source_system,
        adapter_version="1.0",
        extraction_mode="full",
        status="accepted",
        as_of_date=AS_OF,
    )
    session.add(batch)
    session.flush()
    lineage = LineageRecord(
        organization_id=ORG_1,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref="source-pref-api-fixture",
        input_lineage_ids=[],
    )
    session.add(lineage)
    session.flush()
    meta: dict[str, Any] = {
        "organization_id": ORG_1,
        "bank_id": bank_id,
        "as_of_date": AS_OF,
        "ingested_at": INGESTED_AT,
        "source_system": source_system,
        "ingestion_batch_id": batch.id,
        "lineage_id": lineage.id,
        "validation_status": "accepted",
    }
    curve = CanonicalYieldCurve(
        **meta,
        source_reference=f"{source_system}/{curve_name}",
        currency="GHS",
        curve_name=curve_name,
        curve_type="zero" if curve_name.startswith("AEQ.") else "sovereign",
    )
    session.add(curve)
    session.flush()
    for tenor_months, rate in ((3, "0.24"), (12, "0.25")):
        session.add(
            CanonicalYieldCurvePoint(
                **meta,
                source_reference=f"{source_system}/{curve_name}/{tenor_months}m",
                yield_curve_id=curve.id,
                tenor_months=tenor_months,
                rate=Decimal(rate),
            )
        )
    session.flush()


def _base(bank_id: str) -> str:
    return f"/api/v1/banks/{bank_id}/market-data"


def test_get_source_preferences_returns_defaults(db_client: TestClient) -> None:
    _ = db_client
    session = get_sessionmaker()()
    try:
        bank_id = _seed_bank(session)
        session.commit()
    finally:
        session.close()

    response = db_client.get(f"{_base(bank_id)}/source-preferences", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["bank_id"] == bank_id
    for category in ("curves", "fx", "rates"):
        assert body[category] == {"source": "aequor", "overlay": True}
    assert body["updated_at"] is None
    assert body["updated_by"] is None


def test_put_source_preferences_upserts_and_persists(db_client: TestClient) -> None:
    _ = db_client
    session = get_sessionmaker()()
    try:
        bank_id = _seed_bank(session)
        session.commit()
    finally:
        session.close()

    put = db_client.put(
        f"{_base(bank_id)}/source-preferences",
        headers=headers(),
        json={
            "curves": {"source": "vendor", "overlay": False},
            "fx": {"source": "bank"},
            "reason": "licensed feed for curves, own marks for FX",
        },
    )
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["curves"] == {"source": "vendor", "overlay": False}
    assert body["fx"] == {"source": "bank", "overlay": True}
    assert body["rates"] == {"source": "aequor", "overlay": True}
    assert body["updated_at"] is not None
    assert body["updated_by"] is not None

    # Re-read persists the selection.
    again = db_client.get(f"{_base(bank_id)}/source-preferences", headers=headers())
    assert again.json()["curves"] == {"source": "vendor", "overlay": False}


def test_put_source_preferences_requires_analyst_role(db_client: TestClient) -> None:
    _ = db_client
    session = get_sessionmaker()()
    try:
        bank_id = _seed_bank(session)
        session.commit()
    finally:
        session.close()

    response = db_client.put(
        f"{_base(bank_id)}/source-preferences",
        headers=headers(roles=("viewer",)),
        json={"curves": {"source": "vendor"}},
    )
    assert response.status_code == 403, response.text


def test_get_planes_side_by_side(db_client: TestClient) -> None:
    _ = db_client
    session = get_sessionmaker()()
    try:
        bank_id = _seed_bank(session)
        _seed_curve(session, bank_id, curve_name="AEQ.GHS.SOV.ZERO", source_system="AEQUOR_DESK")
        _seed_curve(session, bank_id, curve_name="GHS_SOVEREIGN", source_system="BLOOMBERG")
        session.commit()
    finally:
        session.close()

    response = db_client.get(
        f"{_base(bank_id)}/planes?category=curves&as_of={AS_OF.isoformat()}",
        headers=headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["category"] == "curves"
    assert body["selected_source"] == "aequor"
    assert body["overlay_enabled"] is True
    by_source = {plane["source"]: plane for plane in body["planes"]}
    assert by_source["aequor"]["available"] is True
    assert by_source["aequor"]["is_selected"] is True
    assert by_source["aequor"]["items"][0]["curve_name"] == "AEQ.GHS.SOV.ZERO"
    assert by_source["aequor"]["items"][0]["kind"] == "curve"
    assert by_source["vendor"]["available"] is True
    assert by_source["bank"]["available"] is False
    assert body["overlay"] == {"available": False, "delta_preview": []}


def test_get_planes_rejects_unknown_category(db_client: TestClient) -> None:
    _ = db_client
    session = get_sessionmaker()()
    try:
        bank_id = _seed_bank(session)
        session.commit()
    finally:
        session.close()

    response = db_client.get(
        f"{_base(bank_id)}/planes?category=bogus&as_of={AS_OF.isoformat()}",
        headers=headers(),
    )
    assert response.status_code == 422, response.text


def test_get_forward_grid(db_client: TestClient) -> None:
    _ = db_client
    session = get_sessionmaker()()
    try:
        bank_id = _seed_bank(session)
        session.add(
            DeskDetermination(
                cob_date=AS_OF,
                methodology_code="GHS_CURVE_V1",
                methodology_version=3,
                input_snapshot=[{"instrument": "deposit", "tenor": "3M", "quote": "0.24"}],
                input_digest="feedface",
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
        )
        session.commit()
    finally:
        session.close()

    response = db_client.get(
        f"{_base(bank_id)}/curves/AEQ.GHS.SOV.FWD/forward-grid?as_of={AS_OF.isoformat()}",
        headers=headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["curve_name"] == "AEQ.GHS.SOV.FWD"
    assert body["currency"] == "GHS"
    assert body["methodology_ref"] == "GHS_CURVE_V1 v3"
    assert body["grid_is_authoritative"] is False
    assert body["frequency"] == "3M"
    assert body["available_frequencies"] == ["3M"]
    assert body["assumptions"] is None
    assert len(body["rows"]) == 2
    assert body["rows"][0]["start"] == AS_OF.isoformat()
    assert body["rows"][0]["forward_yield"] == "0.24"
    assert body["pillars"] == [{"tenor": "3M", "instrument": "deposit", "quote": "0.24"}]


def test_forward_grid_missing_curve_is_404(db_client: TestClient) -> None:
    _ = db_client
    session = get_sessionmaker()()
    try:
        bank_id = _seed_bank(session)
        session.commit()
    finally:
        session.close()

    response = db_client.get(
        f"{_base(bank_id)}/curves/AEQ.GHS.SOV.FWD/forward-grid?as_of={AS_OF.isoformat()}",
        headers=headers(),
    )
    assert response.status_code == 404, response.text


def test_source_preferences_are_tenant_isolated(db_client: TestClient) -> None:
    _ = db_client
    session = get_sessionmaker()()
    try:
        bank_id = _seed_bank(session)
        session.commit()
    finally:
        session.close()

    # A different tenant cannot read another org's bank preference.
    response = db_client.get(f"{_base(bank_id)}/source-preferences", headers=headers(org_id=ORG_2))
    assert response.status_code == 404, response.text
