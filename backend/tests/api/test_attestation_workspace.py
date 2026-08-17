"""The signing-workspace API contract (docs/attestation_esignature.md §4.6),
exercised against the ACTUAL primary database.

The dashboard codes against these paths and these field names, so the assertions
below are deliberately about the *contract*: every endpoint, its gate, and the
shape it returns. A rename here is a break for the generated client, which is why
it is asserted rather than assumed. Invariants over the real Sample Bank: placement
resolution order (package > bank template > default) and the per-kind minimum
boxes; adoption normalises a mark for a signer identity; routing/inbox gates and
the digest guard on the one-act certify-and-send. Opt-in via
REAL_DATA_DATABASE_URL; everything rolls back inside ``real_client``.
"""

from __future__ import annotations

import base64
import io
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import ReturnSignaturePlacement, User
from app.services.attestation import pdf_signing
from tests.real_data import REAL_BANK_ID, REAL_ORG_ID, real_headers, requires_real_data

pytestmark = requires_real_data

RETURN_CODE = "LCR-NSFR"
BANK = f"/api/v1/banks/{REAL_BANK_ID}"
BASE = f"{BANK}/regulatory-packages"


@pytest.fixture(autouse=True)
def signer_pepper(monkeypatch: pytest.MonkeyPatch) -> None:
    """A signer identity is needed to own an adopted mark, and deriving one needs
    the pepper. Autouse so it is set before ``real_client`` builds the app. (Real
    users who already hold a persisted identity keep it — the row is the
    authority, not the pepper.)"""
    monkeypatch.setenv("SIGNER_ID_PEPPER", "test-signer-pepper-not-for-production")
    get_settings.cache_clear()


def _drawn_png() -> str:
    image = Image.new("RGBA", (300, 110), (0, 0, 0, 0))
    ImageDraw.Draw(image).line([(20, 90), (120, 20), (280, 80)], fill=(10, 20, 60), width=6)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _latest_period(client: TestClient) -> dict[str, Any]:
    response = client.get(f"{BANK}/reporting-periods", headers=real_headers())
    assert response.status_code == 200, response.text
    periods = response.json()["periods"]
    assert periods, "the real Sample Bank must have at least one reporting period"
    return periods[0]


def _ensure_baseline_run(client: TestClient, period_id: str) -> None:
    """Generation binds the period's latest succeeded baseline liquidity run;
    reuse the real bank's stored one and run the engine only when absent."""
    listed = client.get(
        f"{BANK}/regulatory-runs",
        headers=real_headers(),
        params={
            "module": "liquidity",
            "scenario_code": "baseline",
            "reporting_period_id": period_id,
            "limit": 100,
        },
    )
    assert listed.status_code == 200, listed.text
    if any(run["status"] == "succeeded" for run in listed.json()["runs"]):
        return
    run = client.post(
        f"{BANK}/regulatory-runs",
        headers=real_headers(),
        json={"module": "liquidity", "reporting_period_id": period_id, "scenario_code": "baseline"},
    )
    assert run.status_code == 201, run.text
    assert run.json()["status"] == "succeeded", run.json()


def _validated_package(client: TestClient) -> dict[str, Any]:
    period = _latest_period(client)
    _ensure_baseline_run(client, period["id"])
    created = client.post(
        BASE,
        headers=real_headers(),
        json={"return_code": RETURN_CODE, "reporting_date": period["period_end"]},
    )
    assert created.status_code == 201, created.text
    package = created.json()
    validated = client.post(f"{BASE}/{package['id']}/validate", headers=real_headers())
    assert validated.status_code == 200, validated.text
    return package


def _fresh_user(session: Session) -> dict[str, str]:
    """Headers for a brand-new ACTIVE user in the real org (nothing adopted,
    nothing routed to them). Written on the shared transaction — rolled back."""
    session.info["organization_id"] = REAL_ORG_ID
    user = User(
        id=uuid4(),
        organization_id=REAL_ORG_ID,
        email=f"real-suite.{uuid4().hex[:8]}@samplebank.test",
        display_name="Real-suite Signer",
        role="admin",
    )
    session.add(user)
    session.commit()
    return real_headers(user_id=user.id, email=user.email)


def _forget_return_templates(session: Session) -> None:
    """Start placement resolution from the palette default whatever templates the
    real bank has placed for the return; the rows come back with the rollback."""
    session.info["organization_id"] = REAL_ORG_ID
    session.execute(
        delete(ReturnSignaturePlacement).where(
            ReturnSignaturePlacement.organization_id == REAL_ORG_ID,
            ReturnSignaturePlacement.return_code == RETURN_CODE,
        )
    )
    session.commit()


def _box(
    role: str,
    page_index: int,
    box: tuple[int, int, int, int],
    field_type: str = "signature",
) -> dict[str, Any]:
    return {
        "signing_role": role,
        "field_type": field_type,
        "page_index": page_index,
        "x1": box[0],
        "y1": box[1],
        "x2": box[2],
        "y2": box[3],
    }


def _default_boxes(page_index: int = 2) -> list[dict[str, Any]]:
    return [
        _box("preparer", page_index, (60, 260, 300, 345)),
        _box("approver", page_index, (310, 260, 550, 345)),
    ]


# --- placement --------------------------------------------------------------


def test_resolved_placements_report_the_default_and_every_kind_of_minimum(
    real_client: TestClient, real_session: Session
) -> None:
    """The palette needs a floor PER KIND to validate a drag before it posts it."""
    _forget_return_templates(real_session)
    package = _validated_package(real_client)
    response = real_client.get(
        f"{BASE}/{package['id']}/attestation/placements", headers=real_headers()
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "default"
    assert body["return_code"] == RETURN_CODE
    assert body["editable"] is True
    assert {entry["signing_role"] for entry in body["placements"]} == {"preparer", "approver"}
    assert {
        entry["field_type"]: (entry["min_box_width"], entry["min_box_height"])
        for entry in body["field_types"]
    } == pdf_signing.MIN_BOX_SIZES


def test_placing_fields_then_reading_them_back_round_trips(
    real_client: TestClient, real_session: Session
) -> None:
    _forget_return_templates(real_session)
    package = _validated_package(real_client)
    response = real_client.put(
        f"{BASE}/{package['id']}/attestation/placements",
        headers=real_headers(),
        json={"placements": _default_boxes(), "reason": "placed in the workspace"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "package"
    placed = {entry["signing_role"]: entry for entry in body["placements"]}
    assert placed["preparer"]["page_index"] == 2
    assert (placed["preparer"]["x1"], placed["preparer"]["y2"]) == (60.0, 345.0)

    # An empty list clears the override rather than needing a DELETE.
    cleared = real_client.put(
        f"{BASE}/{package['id']}/attestation/placements",
        headers=real_headers(),
        json={"placements": [], "reason": "back to the template"},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["source"] == "default"


def test_a_placement_below_the_minimum_box_is_refused_by_the_api(
    real_client: TestClient,
) -> None:
    package = _validated_package(real_client)
    narrow = _default_boxes()
    narrow[0]["x2"] = narrow[0]["x1"] + pdf_signing.MIN_BOX_SIZES["signature"][0] - 1
    response = real_client.put(
        f"{BASE}/{package['id']}/attestation/placements",
        headers=real_headers(),
        json={"placements": narrow, "reason": "too small"},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["error_code"] == "placement_too_small"


def test_a_board_role_cannot_be_placed_at_the_contract_boundary(
    real_client: TestClient,
) -> None:
    """422 from the schema, not a 409 from the service: only two fields exist."""
    package = _validated_package(real_client)
    response = real_client.put(
        f"{BASE}/{package['id']}/attestation/placements",
        headers=real_headers(),
        json={
            "placements": [*_default_boxes(), _box("board", 2, (60, 100, 300, 185))],
            "reason": "board too",
        },
    )
    assert response.status_code == 422, response.text


def test_the_placement_template_is_admin_only_and_reason_required(
    real_client: TestClient, real_session: Session
) -> None:
    _forget_return_templates(real_session)
    payload = {
        "return_code": RETURN_CODE,
        "bank_id": REAL_BANK_ID,
        "placements": _default_boxes(page_index=1),
        "reason": "BoG LCR/NSFR signature block",
    }
    forbidden = real_client.put(
        "/api/v1/attestation/signature-placements",
        headers=real_headers(roles=("analyst",)),
        json=payload,
    )
    assert forbidden.status_code == 403

    created = real_client.put(
        "/api/v1/attestation/signature-placements", headers=real_headers(), json=payload
    )
    assert created.status_code == 200, created.text
    assert created.json()["return_code"] == RETURN_CODE
    assert created.json()["bank_id"] == REAL_BANK_ID

    listed = real_client.get(
        "/api/v1/attestation/signature-placements",
        headers=real_headers(),
        params={"return_code": RETURN_CODE},
    )
    assert listed.status_code == 200, listed.text
    templates = listed.json()["templates"]
    assert len(templates) == 1
    assert (templates[0]["return_code"], templates[0]["bank_id"]) == (RETURN_CODE, REAL_BANK_ID)

    without_reason = real_client.put(
        "/api/v1/attestation/signature-placements",
        headers=real_headers(),
        json={**payload, "reason": ""},
    )
    assert without_reason.status_code == 422


def test_a_return_inherits_its_template(real_client: TestClient, real_session: Session) -> None:
    _forget_return_templates(real_session)
    package = _validated_package(real_client)
    placed = real_client.put(
        "/api/v1/attestation/signature-placements",
        headers=real_headers(),
        json={
            "return_code": RETURN_CODE,
            "bank_id": REAL_BANK_ID,
            "placements": _default_boxes(page_index=1),
            "reason": "BoG LCR/NSFR signature block",
        },
    )
    assert placed.status_code == 200, placed.text
    resolved = real_client.get(
        f"{BASE}/{package['id']}/attestation/placements", headers=real_headers()
    ).json()
    assert resolved["source"] == "bank_template"
    assert {entry["page_index"] for entry in resolved["placements"]} == {1}


# --- adopted appearance -----------------------------------------------------


def test_adopting_a_drawn_signature_returns_normalised_bytes(
    real_client: TestClient, real_session: Session
) -> None:
    signer = _fresh_user(real_session)
    empty = real_client.get("/api/v1/attestation/my-signature-appearance", headers=signer)
    assert empty.status_code == 200, empty.text
    assert empty.json()["adopted"] is False
    assert empty.json()["signer_id"].startswith("SGN-")
    assert "times_italic" in empty.json()["available_fonts"]

    raw = _drawn_png()
    adopted = real_client.put(
        "/api/v1/attestation/my-signature-appearance",
        headers=signer,
        json={"kind": "drawn", "image_png_base64": raw},
    )
    assert adopted.status_code == 200, adopted.text
    body = adopted.json()
    assert body["adopted"] is True
    assert body["kind"] == "drawn"
    assert body["signer_id"] == empty.json()["signer_id"]
    assert (body["image_width"], body["image_height"]) == (600, 200)
    # Normalised, not echoed: the raw upload is never what is stored.
    assert body["image_png_base64"] != raw
    assert body["typed_name"] is None


def test_adopting_a_typed_signature_records_the_chosen_font(
    real_client: TestClient, real_session: Session
) -> None:
    signer = _fresh_user(real_session)
    adopted = real_client.put(
        "/api/v1/attestation/my-signature-appearance",
        headers=signer,
        json={"kind": "typed", "typed_name": "Ama Mensah", "typed_font": "times_italic"},
    )
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()["typed_name"] == "Ama Mensah"
    assert adopted.json()["typed_font"] == "times_italic"
    assert adopted.json()["image_png_base64"] is None


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"kind": "drawn", "image_png_base64": "not base64!!"}, 422),
        (
            {"kind": "drawn", "image_png_base64": base64.b64encode(b"%PDF-1.7").decode()},
            422,
        ),
        ({"kind": "drawn"}, 422),
        ({"kind": "typed", "typed_name": "Ama", "typed_font": "comic_sans"}, 422),
        ({"kind": "engraved"}, 422),
    ],
    ids=["bad_base64", "a_pdf", "no_payload", "unknown_font", "unknown_kind"],
)
def test_an_unacceptable_mark_is_refused_with_422(
    real_client: TestClient, payload: dict[str, Any], expected: int
) -> None:
    response = real_client.put(
        "/api/v1/attestation/my-signature-appearance", headers=real_headers(), json=payload
    )
    assert response.status_code == expected, response.text


# --- routing ----------------------------------------------------------------


def test_the_attestation_status_carries_the_recipient_list(real_client: TestClient) -> None:
    """The UI reads routing from the status payload it already fetches."""
    package = _validated_package(real_client)
    response = real_client.get(f"{BASE}/{package['id']}/attestation", headers=real_headers())
    assert response.status_code == 200, response.text
    assert response.json()["recipients"] == []


def test_awaiting_my_signature_is_empty_until_something_is_routed(
    real_client: TestClient, real_session: Session
) -> None:
    signer = _fresh_user(real_session)
    response = real_client.get("/api/v1/attestation/awaiting-my-signature", headers=signer)
    assert response.status_code == 200, response.text
    assert response.json() == {"items": []}


def test_rerouting_is_approver_gated(real_client: TestClient) -> None:
    package = _validated_package(real_client)
    response = real_client.put(
        f"{BASE}/{package['id']}/attestation/recipients",
        headers=real_headers(roles=("analyst",)),
        json={"recipients": [], "reason": "reassign"},
    )
    assert response.status_code == 403, response.text


def test_certify_and_send_refuses_a_stale_digest_before_anything_is_signed(
    real_client: TestClient,
) -> None:
    """The digest guard applies to the combined act too, not just to ``certify``."""
    package = _validated_package(real_client)
    response = real_client.post(
        f"{BASE}/{package['id']}/attestation/certify-and-send",
        headers=real_headers(),
        json={
            "signing_role": "preparer",
            "authorization_token": "not-a-real-token",
            "expected_certification_digest": "0" * 64,
            "recipients": [],
            "reason": "one act",
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["error_code"] == "figures_changed_since_preview"
    # The refusal left the package where it was: still validated, still unsigned.
    status = real_client.get(f"{BASE}/{package['id']}", headers=real_headers()).json()
    assert status["status"] == "validated"
    assert status["attestation_state"] == "unsigned"
