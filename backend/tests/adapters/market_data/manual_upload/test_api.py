"""HTTP surface: template downloads and the manual market data upload flow."""

from __future__ import annotations

import io
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.adapters.market_data.manual_upload.templates import TEMPLATE_HEADERS, TEMPLATE_KINDS
from tests.adapters.market_data.manual_upload.fixtures import (
    FIXTURE_AS_OF,
    build_full_coverage_workbook,
    build_yield_curve_workbook,
)
from tests.api.helpers import ORG_2, headers
from tests.storage.inmemory import InMemoryStorageClient

AS_OF = FIXTURE_AS_OF.isoformat()
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture
def wired_storage(
    storage_engine: InMemoryStorageClient, monkeypatch: pytest.MonkeyPatch
) -> InMemoryStorageClient:
    """Point the pull runner, cache, and adapter at the same in-memory client
    the app's ingestion-storage dependency override serves."""
    for target in (
        "app.adapters.market_data.pull_runner.get_storage_client",
        "app.adapters.market_data.cache.get_storage_client",
        "app.adapters.market_data.manual_upload.adapter.get_storage_client",
    ):
        monkeypatch.setattr(target, lambda: storage_engine)
    return storage_engine


def _seed_bank(client: TestClient) -> str:
    response = client.post("/api/v1/banks/seed-demo", headers=headers())
    assert response.status_code == 200, response.text
    return response.json()["bank_id"]


def _upload(  # noqa: PLR0913 - one helper carries the full request shape
    client: TestClient,
    bank_id: str,
    content: bytes,
    *,
    filename: str = "curves.xlsx",
    as_of: str = AS_OF,
    request_headers: dict[str, str] | None = None,
) -> Any:
    return client.post(
        f"/api/v1/banks/{bank_id}/market-data/uploads",
        headers=request_headers or headers(),
        files={"file": (filename, io.BytesIO(content), XLSX_MEDIA_TYPE)},
        data={"as_of_date": as_of},
    )


# -- templates ----------------------------------------------------------------


@pytest.mark.parametrize("kind", TEMPLATE_KINDS)
def test_template_download(db_client: TestClient, kind: str) -> None:
    response = db_client.get(f"/api/v1/market-data/templates/{kind}", headers=headers())
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith(XLSX_MEDIA_TYPE)
    assert f'filename="{kind}_template.xlsx"' in response.headers["content-disposition"]
    workbook = openpyxl.load_workbook(io.BytesIO(response.content))
    sheet = workbook.active
    assert sheet is not None
    assert tuple(cell.value for cell in sheet[1]) == TEMPLATE_HEADERS[kind]  # type: ignore[index]


def test_template_unknown_kind_is_422(db_client: TestClient) -> None:
    response = db_client.get("/api/v1/market-data/templates/bond_ladder", headers=headers())
    assert response.status_code == 422


# -- uploads --------------------------------------------------------------------


def test_upload_full_workbook_accepted(
    db_client: TestClient, wired_storage: InMemoryStorageClient
) -> None:
    bank_id = _seed_bank(db_client)
    response = _upload(db_client, bank_id, build_full_coverage_workbook(), filename="full.xlsx")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["bank_id"] == bank_id
    assert body["status"] == "accepted"
    assert body["as_of_date"] == AS_OF
    assert body["quota_consumed"] == 0
    assert body["canonical_records_produced"] > 0
    assert body["errors"] == []
    assert "YIELD_CURVE_GHS" in body["scopes"]
    assert "FX_SPOT_USD_GHS" in body["scopes"]
    assert "CREDIT_RATING_GHANA_SOVEREIGN" in body["scopes"]
    assert "MACRO_GHANA_GDP_FORECAST" in body["scopes"]
    assert body["batch_id"]

    batch = db_client.get(
        f"/api/v1/banks/{bank_id}/ingestion-batches/{body['batch_id']}", headers=headers()
    )
    assert batch.status_code == 200, batch.text
    assert batch.json()["source_system"] == "MANUAL_UPLOAD"


def test_upload_reports_row_problems_as_warnings(
    db_client: TestClient, wired_storage: InMemoryStorageClient
) -> None:
    bank_id = _seed_bank(db_client)
    content = build_yield_curve_workbook(
        [
            ["GHS", "GHS_GOV_BOND", AS_OF, 3, 15.80],
            ["XXX", "XXX_GOV_BOND", AS_OF, 3, 9.10],
        ]
    )
    response = _upload(db_client, bank_id, content)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "accepted"
    assert body["scopes"] == ["YIELD_CURVE_GHS"]
    assert any("unsupported currency" in warning for warning in body["warnings"])


def test_upload_unrecognized_file_is_422(
    db_client: TestClient, wired_storage: InMemoryStorageClient
) -> None:
    bank_id = _seed_bank(db_client)
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(["name", "amount"])
    sheet.append(["something", 1])
    buffer = io.BytesIO()
    workbook.save(buffer)
    response = _upload(db_client, bank_id, buffer.getvalue(), filename="mystery.xlsx")
    assert response.status_code == 422
    assert "no recognizable market data rows" in response.json()["error"]["message"]


def test_upload_empty_file_is_422(
    db_client: TestClient, wired_storage: InMemoryStorageClient
) -> None:
    bank_id = _seed_bank(db_client)
    response = _upload(db_client, bank_id, b"")
    assert response.status_code == 422
    assert response.json()["error"]["message"] == "Uploaded file is empty."


def test_upload_unsupported_suffix_is_422(
    db_client: TestClient, wired_storage: InMemoryStorageClient
) -> None:
    bank_id = _seed_bank(db_client)
    response = _upload(db_client, bank_id, b"currency\n", filename="curves.txt")
    assert response.status_code == 422
    assert "Unsupported file type" in response.json()["error"]["message"]


def test_upload_is_tenant_scoped(
    db_client: TestClient, wired_storage: InMemoryStorageClient
) -> None:
    bank_id = _seed_bank(db_client)
    content = build_yield_curve_workbook([["GHS", "GHS_GOV_BOND", AS_OF, 3, 15.80]])
    response = _upload(db_client, bank_id, content, request_headers=headers(org_id=ORG_2))
    assert response.status_code == 404
