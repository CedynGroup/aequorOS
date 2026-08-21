"""Overlay lifecycle API: CRUD, RBAC, versioned edits, audit, isolation."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_sessionmaker
from app.models import AuditEvent, Bank, MarketDataOverlay
from tests.api.helpers import ORG_1, ORG_2, headers

AS_OF = date(2026, 7, 15)
CURVE = "AEQ.GHS.SOV.ZERO"


def _seed_bank(session: Session, org_id: str = ORG_1) -> str:
    bank = Bank(
        organization_id=org_id,
        name="Overlay API Test Bank",
        short_name="OATB",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="universal_bank",
    )
    session.add(bank)
    session.flush()
    return bank.id


def _bank_id(db_client: TestClient, org_id: str = ORG_1) -> str:
    _ = db_client
    session = get_sessionmaker()()
    try:
        bank_id = _seed_bank(session, org_id)
        session.commit()
    finally:
        session.close()
    return bank_id


def _url(bank_id: str) -> str:
    return f"/api/v1/banks/{bank_id}/market-data/overlays"


def _payload(**kwargs: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "base_ref_kind": "curve",
        "base_curve_name": CURVE,
        "adjustment_type": "additive_bps",
        "value": "25",
        "component_tag": "liquidity_premium",
        "effective_from": "2026-01-01",
    }
    payload.update(kwargs)
    return payload


def test_overlay_create_list_end_lifecycle(db_client: TestClient) -> None:
    bank_id = _bank_id(db_client)

    created = db_client.post(_url(bank_id), headers=headers(), json=_payload(note="Desk TLP"))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["base_curve_name"] == CURVE
    assert body["adjustment_type"] == "additive_bps"
    assert body["value"] == "25"
    assert body["component_tag"] == "liquidity_premium"
    assert body["tenor_months"] is None
    assert body["effective_to"] is None
    assert body["superseded_by"] is None
    assert body["active"] is True
    # "set by [bank], effective [date], by [user]" attribution material.
    assert body["created_by_email"] == "demo.user.one@example.test"
    assert body["effective_from"] == "2026-01-01"
    overlay_id = body["id"]

    listed = db_client.get(_url(bank_id), headers=headers())
    assert listed.status_code == 200, listed.text
    listing = listed.json()
    assert listing["total"] == 1
    assert listing["overlays"][0]["id"] == overlay_id

    ended = db_client.post(
        f"{_url(bank_id)}/{overlay_id}/end",
        headers=headers(),
        json={"effective_to": "2026-07-01"},
    )
    assert ended.status_code == 200, ended.text
    assert ended.json()["effective_to"] == "2026-07-01"

    # Ended before today: gone from the active list, present in history.
    active = db_client.get(_url(bank_id), headers=headers()).json()
    assert active["total"] == 0
    history = db_client.get(f"{_url(bank_id)}?include_history=true", headers=headers()).json()
    assert history["total"] == 1
    assert history["overlays"][0]["active"] is False


def test_overlay_versioned_edit_supersedes_prior_row(db_client: TestClient) -> None:
    bank_id = _bank_id(db_client)
    first = db_client.post(_url(bank_id), headers=headers(), json=_payload()).json()

    edited = db_client.post(
        _url(bank_id),
        headers=headers(),
        json=_payload(value="40", supersedes=first["id"]),
    )
    assert edited.status_code == 201, edited.text
    new_id = edited.json()["id"]

    active = db_client.get(_url(bank_id), headers=headers()).json()
    assert [row["id"] for row in active["overlays"]] == [new_id]
    history = db_client.get(f"{_url(bank_id)}?include_history=true", headers=headers()).json()
    by_id = {row["id"]: row for row in history["overlays"]}
    assert by_id[first["id"]]["superseded_by"] == new_id
    assert by_id[first["id"]]["active"] is False

    # A second edit against the superseded version conflicts.
    conflict = db_client.post(
        _url(bank_id),
        headers=headers(),
        json=_payload(value="55", supersedes=first["id"]),
    )
    assert conflict.status_code == 409, conflict.text

    # Ending a superseded version conflicts too.
    end_old = db_client.post(
        f"{_url(bank_id)}/{first['id']}/end",
        headers=headers(),
        json={"effective_to": "2026-07-01"},
    )
    assert end_old.status_code == 409, end_old.text


def test_overlay_rbac_viewers_read_analysts_mutate(db_client: TestClient) -> None:
    bank_id = _bank_id(db_client)
    viewer = headers(roles=("viewer",))
    analyst = headers(roles=("analyst",))

    denied = db_client.post(_url(bank_id), headers=viewer, json=_payload())
    assert denied.status_code == 403, denied.text

    allowed = db_client.post(_url(bank_id), headers=analyst, json=_payload())
    assert allowed.status_code == 201, allowed.text

    readable = db_client.get(_url(bank_id), headers=viewer)
    assert readable.status_code == 200, readable.text
    assert readable.json()["total"] == 1

    end_denied = db_client.post(
        f"{_url(bank_id)}/{allowed.json()['id']}/end",
        headers=viewer,
        json={"effective_to": "2026-12-31"},
    )
    assert end_denied.status_code == 403, end_denied.text


def test_overlay_validation_rejects_malformed_shapes(db_client: TestClient) -> None:
    bank_id = _bank_id(db_client)

    no_curve_name = db_client.post(
        _url(bank_id), headers=headers(), json=_payload(base_curve_name=None)
    )
    assert no_curve_name.status_code == 400, no_curve_name.text

    fx_with_curve = db_client.post(
        _url(bank_id), headers=headers(), json=_payload(base_ref_kind="fx")
    )
    assert fx_with_curve.status_code == 400, fx_with_curve.text

    inverted_window = db_client.post(
        _url(bank_id),
        headers=headers(),
        json=_payload(effective_from="2026-06-01", effective_to="2026-01-01"),
    )
    assert inverted_window.status_code == 400, inverted_window.text

    negative_factor = db_client.post(
        _url(bank_id),
        headers=headers(),
        json=_payload(adjustment_type="multiplicative", value="-1"),
    )
    assert negative_factor.status_code == 400, negative_factor.text

    created = db_client.post(_url(bank_id), headers=headers(), json=_payload()).json()
    bad_end = db_client.post(
        f"{_url(bank_id)}/{created['id']}/end",
        headers=headers(),
        json={"effective_to": "2025-01-01"},
    )
    assert bad_end.status_code == 400, bad_end.text


def test_overlay_mutations_are_audited(db_client: TestClient) -> None:
    bank_id = _bank_id(db_client)
    created = db_client.post(_url(bank_id), headers=headers(), json=_payload()).json()
    db_client.post(
        f"{_url(bank_id)}/{created['id']}/end",
        headers=headers(),
        json={"effective_to": "2026-12-31"},
    )

    session = get_sessionmaker()()
    try:
        events = session.scalars(
            select(AuditEvent)
            .where(AuditEvent.entity_type == "market_data_overlay")
            .order_by(AuditEvent.created_at)
        ).all()
        event_types = [event.event_type for event in events]
        assert "market_data_overlay.created" in event_types
        assert "market_data_overlay.ended" in event_types
        created_event = next(e for e in events if e.event_type == "market_data_overlay.created")
        assert created_event.details["base_curve_name"] == CURVE
        assert created_event.details["value"] == "25"
    finally:
        session.close()


def test_overlays_are_tenant_isolated(db_client: TestClient) -> None:
    bank_id = _bank_id(db_client)
    db_client.post(_url(bank_id), headers=headers(), json=_payload())

    cross_read = db_client.get(_url(bank_id), headers=headers(ORG_2))
    assert cross_read.status_code == 404, cross_read.text
    cross_write = db_client.post(_url(bank_id), headers=headers(ORG_2), json=_payload())
    assert cross_write.status_code == 404, cross_write.text

    # The other tenant's own bank sees none of ORG_1's overlays.
    other_bank = _bank_id(db_client, ORG_2)
    other = db_client.get(_url(other_bank), headers=headers(ORG_2))
    assert other.status_code == 200, other.text
    assert other.json()["total"] == 0

    session = get_sessionmaker()()
    try:
        rows = session.scalars(select(MarketDataOverlay)).all()
        assert {row.organization_id for row in rows} == {ORG_1}
    finally:
        session.close()
