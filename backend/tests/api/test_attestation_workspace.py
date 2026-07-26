"""The signing-workspace API contract (docs/attestation_esignature.md §4.6).

The dashboard codes against these paths and these field names, so the assertions
below are deliberately about the *contract*: every endpoint, its gate, and the
shape it returns. A rename here is a break for the generated client, which is why
it is asserted rather than assumed.
"""

from __future__ import annotations

import base64
import io
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.core.config import get_settings
from app.services.attestation import pdf_signing
from app.services.sample_bank_seed import SAMPLE_BANK_ID
from tests.api.helpers import ORG_1, headers

REPORTING_DATE = "2026-03-31"
BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages"


@pytest.fixture(autouse=True)
def signer_pepper(monkeypatch: pytest.MonkeyPatch) -> None:
    """A signer identity is needed to own an adopted mark, and deriving one needs
    the pepper. Autouse so it is set before ``db_client`` builds the app."""
    monkeypatch.setenv("SIGNER_ID_PEPPER", "test-signer-pepper-not-for-production")
    get_settings.cache_clear()


def _drawn_png() -> str:
    image = Image.new("RGBA", (300, 110), (0, 0, 0, 0))
    ImageDraw.Draw(image).line([(20, 90), (120, 20), (280, 80)], fill=(10, 20, 60), width=6)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _validated_package(db_client: TestClient) -> dict[str, Any]:
    seeded = db_client.post("/api/v1/banks/seed-demo", headers=headers())
    assert seeded.status_code == 200, seeded.text
    periods = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/reporting-periods", headers=headers()
    ).json()["periods"]
    period = next(p for p in periods if p["period_end"] == REPORTING_DATE)
    run = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
        headers=headers(),
        json={
            "module": "liquidity",
            "reporting_period_id": period["id"],
            "scenario_code": "baseline",
        },
    )
    assert run.status_code == 201, run.text
    created = db_client.post(
        BASE,
        headers=headers(),
        json={"return_code": "BSD3", "reporting_date": REPORTING_DATE},
    )
    assert created.status_code == 201, created.text
    package = created.json()
    validated = db_client.post(
        f"{BASE}/{package['id']}/validate", headers=headers()
    )
    assert validated.status_code == 200, validated.text
    return package


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
    db_client: TestClient,
) -> None:
    """The palette needs a floor PER KIND to validate a drag before it posts it."""
    package = _validated_package(db_client)
    response = db_client.get(f"{BASE}/{package['id']}/attestation/placements", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "default"
    assert body["return_code"] == "BSD3"
    assert body["editable"] is True
    assert {entry["signing_role"] for entry in body["placements"]} == {"preparer", "approver"}
    assert {
        entry["field_type"]: (entry["min_box_width"], entry["min_box_height"])
        for entry in body["field_types"]
    } == pdf_signing.MIN_BOX_SIZES


def test_placing_fields_then_reading_them_back_round_trips(db_client: TestClient) -> None:
    package = _validated_package(db_client)
    response = db_client.put(
        f"{BASE}/{package['id']}/attestation/placements",
        headers=headers(),
        json={"placements": _default_boxes(), "reason": "placed in the workspace"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "package"
    placed = {entry["signing_role"]: entry for entry in body["placements"]}
    assert placed["preparer"]["page_index"] == 2
    assert (placed["preparer"]["x1"], placed["preparer"]["y2"]) == (60.0, 345.0)

    # An empty list clears the override rather than needing a DELETE.
    cleared = db_client.put(
        f"{BASE}/{package['id']}/attestation/placements",
        headers=headers(),
        json={"placements": [], "reason": "back to the template"},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["source"] == "default"


def test_a_placement_below_the_minimum_box_is_refused_by_the_api(
    db_client: TestClient,
) -> None:
    package = _validated_package(db_client)
    narrow = _default_boxes()
    narrow[0]["x2"] = narrow[0]["x1"] + pdf_signing.MIN_BOX_SIZES["signature"][0] - 1
    response = db_client.put(
        f"{BASE}/{package['id']}/attestation/placements",
        headers=headers(),
        json={"placements": narrow, "reason": "too small"},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["error_code"] == "placement_too_small"


def test_a_board_role_cannot_be_placed_at_the_contract_boundary(
    db_client: TestClient,
) -> None:
    """422 from the schema, not a 409 from the service: only two fields exist."""
    package = _validated_package(db_client)
    response = db_client.put(
        f"{BASE}/{package['id']}/attestation/placements",
        headers=headers(),
        json={
            "placements": [*_default_boxes(), _box("board", 2, (60, 100, 300, 185))],
            "reason": "board too",
        },
    )
    assert response.status_code == 422, response.text


def test_the_placement_template_is_admin_only_and_reason_required(
    db_client: TestClient,
) -> None:
    db_client.post("/api/v1/banks/seed-demo", headers=headers())
    payload = {
        "return_code": "BSD3",
        "bank_id": SAMPLE_BANK_ID,
        "placements": _default_boxes(page_index=1),
        "reason": "BoG BSD-3 signature block",
    }
    forbidden = db_client.put(
        "/api/v1/attestation/signature-placements",
        headers=headers(ORG_1, roles=("analyst",)),
        json=payload,
    )
    assert forbidden.status_code == 403

    created = db_client.put(
        "/api/v1/attestation/signature-placements", headers=headers(), json=payload
    )
    assert created.status_code == 200, created.text
    assert created.json()["return_code"] == "BSD3"
    assert created.json()["bank_id"] == SAMPLE_BANK_ID

    listed = db_client.get(
        "/api/v1/attestation/signature-placements?return_code=BSD3", headers=headers()
    )
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["templates"]) == 1

    without_reason = db_client.put(
        "/api/v1/attestation/signature-placements",
        headers=headers(),
        json={**payload, "reason": ""},
    )
    assert without_reason.status_code == 422


def test_a_return_inherits_its_template(db_client: TestClient) -> None:
    package = _validated_package(db_client)
    db_client.put(
        "/api/v1/attestation/signature-placements",
        headers=headers(),
        json={
            "return_code": "BSD3",
            "bank_id": SAMPLE_BANK_ID,
            "placements": _default_boxes(page_index=1),
            "reason": "BoG BSD-3 signature block",
        },
    )
    resolved = db_client.get(
        f"{BASE}/{package['id']}/attestation/placements", headers=headers()
    ).json()
    assert resolved["source"] == "bank_template"
    assert {entry["page_index"] for entry in resolved["placements"]} == {1}


# --- adopted appearance -----------------------------------------------------


def test_adopting_a_drawn_signature_returns_normalised_bytes(db_client: TestClient) -> None:
    db_client.post("/api/v1/banks/seed-demo", headers=headers())
    empty = db_client.get("/api/v1/attestation/my-signature-appearance", headers=headers())
    assert empty.status_code == 200, empty.text
    assert empty.json()["adopted"] is False
    assert empty.json()["signer_id"].startswith("SGN-")
    assert "times_italic" in empty.json()["available_fonts"]

    raw = _drawn_png()
    adopted = db_client.put(
        "/api/v1/attestation/my-signature-appearance",
        headers=headers(),
        json={"kind": "drawn", "image_png_base64": raw},
    )
    assert adopted.status_code == 200, adopted.text
    body = adopted.json()
    assert body["adopted"] is True
    assert body["kind"] == "drawn"
    assert (body["image_width"], body["image_height"]) == (600, 200)
    # Normalised, not echoed: the raw upload is never what is stored.
    assert body["image_png_base64"] != raw
    assert body["typed_name"] is None


def test_adopting_a_typed_signature_records_the_chosen_font(db_client: TestClient) -> None:
    db_client.post("/api/v1/banks/seed-demo", headers=headers())
    adopted = db_client.put(
        "/api/v1/attestation/my-signature-appearance",
        headers=headers(),
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
    db_client: TestClient, payload: dict[str, Any], expected: int
) -> None:
    db_client.post("/api/v1/banks/seed-demo", headers=headers())
    response = db_client.put(
        "/api/v1/attestation/my-signature-appearance", headers=headers(), json=payload
    )
    assert response.status_code == expected, response.text


# --- routing ----------------------------------------------------------------


def test_the_attestation_status_carries_the_recipient_list(db_client: TestClient) -> None:
    """The UI reads routing from the status payload it already fetches."""
    package = _validated_package(db_client)
    response = db_client.get(f"{BASE}/{package['id']}/attestation", headers=headers())
    assert response.status_code == 200, response.text
    assert response.json()["recipients"] == []


def test_awaiting_my_signature_is_empty_until_something_is_routed(
    db_client: TestClient,
) -> None:
    db_client.post("/api/v1/banks/seed-demo", headers=headers())
    response = db_client.get("/api/v1/attestation/awaiting-my-signature", headers=headers())
    assert response.status_code == 200, response.text
    assert response.json() == {"items": []}


def test_rerouting_is_approver_gated(db_client: TestClient) -> None:
    package = _validated_package(db_client)
    response = db_client.put(
        f"{BASE}/{package['id']}/attestation/recipients",
        headers=headers(ORG_1, roles=("analyst",)),
        json={"recipients": [], "reason": "reassign"},
    )
    assert response.status_code == 403, response.text


def test_certify_and_send_refuses_a_stale_digest_before_anything_is_signed(
    db_client: TestClient,
) -> None:
    """The digest guard applies to the combined act too, not just to ``certify``."""
    package = _validated_package(db_client)
    response = db_client.post(
        f"{BASE}/{package['id']}/attestation/certify-and-send",
        headers=headers(),
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
