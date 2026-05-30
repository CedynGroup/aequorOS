from __future__ import annotations

from fastapi.testclient import TestClient

from app.integrations.storage.base import StoredObjectHead
from tests.api.factories import CaseFactory, DocumentFactory
from tests.api.helpers import ORG_2, headers


def test_upload_flow_validates_and_completes(db_client: TestClient, fake_storage) -> None:
    case_id = str(CaseFactory(db_client).create()["id"])
    documents = DocumentFactory(db_client, fake_storage)

    response = db_client.post(
        "/api/v1/documents/upload-request",
        headers=headers(),
        json={
            "case_id": case_id,
            "filename": "bad.exe",
            "content_type": "application/octet-stream",
            "byte_size": 1,
        },
    )
    assert response.status_code == 400

    response = db_client.post(
        "/api/v1/documents/upload-request",
        headers=headers(),
        json={
            "case_id": case_id,
            "filename": "big.pdf",
            "content_type": "application/pdf",
            "byte_size": 25_000_001,
        },
    )
    assert response.status_code == 400

    document_id = str(documents.request_upload(case_id=case_id)["document_id"])

    response = db_client.post(f"/api/v1/documents/{document_id}/complete-upload", headers=headers())
    assert response.status_code == 400

    fake_storage.head = StoredObjectHead(
        content_type="application/pdf",
        byte_size=25_000_001,
        etag='"oversized"',
    )
    response = db_client.post(f"/api/v1/documents/{document_id}/complete-upload", headers=headers())
    assert response.status_code == 400

    documents.complete_upload(document_id=document_id)
    response = db_client.get(f"/api/v1/cases/{case_id}/documents", headers=headers())
    assert response.status_code == 200
    assert response.json()[0]["id"] == document_id


def test_documents_download_delete_and_tenant_isolation(
    db_client: TestClient, fake_storage
) -> None:
    case_id = str(CaseFactory(db_client).create()["id"])
    documents = DocumentFactory(db_client, fake_storage)
    document_id = str(documents.create_uploaded(case_id=case_id)["document_id"])

    response = db_client.get(f"/api/v1/documents/{document_id}", headers=headers())
    assert response.status_code == 200

    response = db_client.get(f"/api/v1/documents/{document_id}/download-url", headers=headers())
    assert response.status_code == 200
    assert response.json()["url"] == fake_storage.download_url

    response = db_client.get(f"/api/v1/documents/{document_id}", headers=headers(ORG_2))
    assert response.status_code == 404

    response = db_client.delete(f"/api/v1/documents/{document_id}", headers=headers())
    assert response.status_code == 200
    assert response.json()["status"] == "deleted"

    fake_storage.head = StoredObjectHead(
        content_type="application/pdf",
        byte_size=1234,
        etag='"etag"',
    )
    response = db_client.post(f"/api/v1/documents/{document_id}/complete-upload", headers=headers())
    assert response.status_code == 409

    response = db_client.get(f"/api/v1/documents/{document_id}/download-url", headers=headers())
    assert response.status_code == 409


def test_parse_flow_creates_completed_job_and_rejects_bad_states(
    db_client: TestClient,
    fake_storage,
) -> None:
    case_id = str(CaseFactory(db_client).create()["id"])
    documents = DocumentFactory(db_client, fake_storage)
    unuploaded_document_id = str(documents.request_upload(case_id=case_id)["document_id"])

    response = db_client.post(
        f"/api/v1/documents/{unuploaded_document_id}/parse", headers=headers()
    )
    assert response.status_code == 400

    documents.complete_upload(document_id=unuploaded_document_id)
    response = db_client.post(
        f"/api/v1/documents/{unuploaded_document_id}/parse", headers=headers()
    )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"

    response = db_client.get(
        f"/api/v1/documents/{unuploaded_document_id}/parse-status", headers=headers()
    )
    assert response.json()["parse_status"] == "parsed"

    db_client.delete(f"/api/v1/documents/{unuploaded_document_id}", headers=headers())
    response = db_client.post(
        f"/api/v1/documents/{unuploaded_document_id}/parse", headers=headers()
    )
    assert response.status_code == 409
