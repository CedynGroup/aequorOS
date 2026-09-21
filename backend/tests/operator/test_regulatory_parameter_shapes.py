"""The console may write a table-valued parameter — but only a well-formed one (D-037).

Before P2 the operator API accepted ``value_json`` for any code and checked
nothing about it, and the console had no way to send one at all. D-024 puts the
ICAAP Pillar 2 engine's band tables, shock tables and grids in the console, so
the write path is now real and is checked at BOTH steps: a malformed proposal is
refused, and so is the approval of a draft that reached the table some other way
(a direct insert, or a draft written before a code's shape existed).

Codes with no registered shape keep their old behaviour exactly — the
pre-existing ``sdi_rwa_composition`` is the live example.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import RegulatoryParameter
from tests.operator.conftest import operator_headers

BASE = "/operator/v1/regulatory-parameters"
FUTURE = "2027-01-01"

_GOOD_BANDS: dict[str, Any] = {
    "schema": "icaap-band-table-v1",
    "metric": "hhi",
    "dimension": "single_name",
    "scale": "unit_interval",
    "mode": "step",
    "basis": "pct_pillar1_credit_capital",
    "bands": [
        {"lower": "0", "upper": "0.02", "addon": "0"},
        {"lower": "0.02", "upper": None, "addon": "6"},
    ],
}
_GAPPED_BANDS: dict[str, Any] = {
    **_GOOD_BANDS,
    "bands": [
        {"lower": "0", "upper": "0.02", "addon": "0"},
        {"lower": "0.05", "upper": None, "addon": "6"},
    ],
}


def _details(response: Any) -> dict[str, Any]:
    """The app wraps an HTTPException dict detail as ``error.details``."""
    body = response.json()
    assert body["error"]["code"] == "http_error", body
    return dict(body["error"]["details"])


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "scope_type": "institution_class",
        "scope_key": "bank",
        "param_code": "ccr_name_bands_hhi",
        "jurisdiction_code": "GH",
        "value_json": _GOOD_BANDS,
        "unit": "band_table",
        "source_citation": "REPRESENTATIVE: revised name-HHI benchmark bands",
        "confirmation_status": "pending",
        "effective_from": FUTURE,
        "change_rationale": "recalibrate the name concentration benchmark",
    }
    body.update(overrides)
    return body


def test_a_well_formed_band_table_can_be_proposed(operator_client: TestClient) -> None:
    resp = operator_client.post(BASE, headers=operator_headers(), json=_body())
    assert resp.status_code == 201, resp.text
    row = resp.json()
    assert row["value_json"]["bands"][1]["addon"] == "6"
    assert row["value_numeric"] is None
    assert row["status"] == "draft"


def test_a_band_table_with_a_gap_is_refused_and_names_the_path(
    operator_client: TestClient,
) -> None:
    resp = operator_client.post(
        BASE, headers=operator_headers(), json=_body(value_json=_GAPPED_BANDS)
    )
    assert resp.status_code == 422, resp.text
    detail = _details(resp)
    assert detail["error_code"] == "parameter_shape_invalid"
    assert detail["param_code"] == "ccr_name_bands_hhi"
    assert detail["path"] == "value_json.bands[1].lower"
    assert "value_json.bands[1].lower" in detail["message"]


def test_a_band_table_pasted_into_the_wrong_code_is_refused(
    operator_client: TestClient,
) -> None:
    """A sector table in the single-name slot would silently change the add-on."""
    resp = operator_client.post(
        BASE,
        headers=operator_headers(),
        json=_body(param_code="ccr_sector_bands_hhi"),
    )
    assert resp.status_code == 422, resp.text
    assert _details(resp)["path"] == "value_json.dimension"


def test_a_structural_code_refuses_a_scalar_and_a_scalar_code_refuses_a_table(
    operator_client: TestClient,
) -> None:
    scalar_for_table = operator_client.post(
        BASE,
        headers=operator_headers(),
        json=_body(value_json=None, value_numeric="5", unit="percent"),
    )
    assert scalar_for_table.status_code == 422, scalar_for_table.text
    assert _details(scalar_for_table)["path"] == "value_json"

    table_for_scalar = operator_client.post(
        BASE,
        headers=operator_headers(),
        json=_body(param_code="ccr_name_cr_n", unit="count"),
    )
    assert table_for_scalar.status_code == 422, table_for_scalar.text
    assert _details(table_for_scalar)["path"] == "value_numeric"


def test_a_scalar_outside_its_structural_bounds_is_refused(
    operator_client: TestClient,
) -> None:
    resp = operator_client.post(
        BASE,
        headers=operator_headers(),
        json=_body(
            param_code="irrbb_outlier_threshold_pct_tier1",
            value_json=None,
            value_numeric="150",
            unit="percent",
        ),
    )
    assert resp.status_code == 422, resp.text
    assert _details(resp)["path"] == "value_numeric"


def test_a_code_with_no_registered_shape_is_unchanged(operator_client: TestClient) -> None:
    """``sdi_rwa_composition`` predates shapes and must keep working."""
    resp = operator_client.post(
        BASE,
        headers=operator_headers(),
        json=_body(
            param_code="sdi_rwa_composition",
            scope_key="sdi",
            value_json={"anything": {"the": "console sends"}},
            unit="mapping",
        ),
    )
    assert resp.status_code == 201, resp.text


def test_approve_revalidates_a_draft_that_reached_the_table_another_way(
    operator_client: TestClient, operator_db: Session
) -> None:
    """The approval is what makes a row govern a calculation, so it is checked
    again — a draft inserted directly (or before the shape was registered) must
    not become an approved malformed table."""
    row = RegulatoryParameter(
        id=uuid.uuid4(),
        scope_type="institution_class",
        scope_key="bank",
        param_code="ccr_name_bands_hhi",
        jurisdiction_code="GH",
        value_numeric=None,
        value_json=_GAPPED_BANDS,
        unit="band_table",
        source_citation="REPRESENTATIVE: inserted directly",
        confirmation_status="pending",
        effective_from=date(2027, 6, 1),
        status="draft",
        proposed_by="someone-else@aequoros.com",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    operator_db.add(row)
    operator_db.commit()

    resp = operator_client.post(
        f"{BASE}/{row.id}/approve",
        headers=operator_headers(),
        json={"change_rationale": "approve the direct insert"},
    )
    assert resp.status_code == 422, resp.text
    assert _details(resp)["path"] == "value_json.bands[1].lower"
    operator_db.expire_all()
    stored = operator_db.get(RegulatoryParameter, row.id)
    assert stored is not None
    assert stored.status == "draft"


def test_a_well_formed_table_completes_the_four_eyes_round_trip(
    operator_client: TestClient, operator_db: Session
) -> None:
    proposed = operator_client.post(BASE, headers=operator_headers(), json=_body())
    assert proposed.status_code == 201, proposed.text
    param_id = proposed.json()["id"]

    approved = operator_client.post(
        f"{BASE}/{param_id}/approve",
        headers=operator_headers(),
        json={"change_rationale": "second pair of eyes"},
    )
    # The dev-token operator is the proposer, so four-eyes refuses first: the
    # authority check must not be reachable only through a well-formed body.
    assert approved.status_code == 422, approved.text
    assert "four-eyes" in approved.json()["error"]["message"]

    stored = operator_db.get(RegulatoryParameter, uuid.UUID(param_id))
    assert stored is not None
    assert stored.value_numeric is None
    stored_body: dict[str, Any] = dict(stored.value_json or {})
    assert stored_body == _GOOD_BANDS
    bands: list[dict[str, Any]] = list(stored_body["bands"])
    assert Decimal(bands[1]["addon"]) == Decimal(6)
