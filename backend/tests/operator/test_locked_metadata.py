from datetime import date
from typing import cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.key_management import registry
from app.core.key_management.local import LocalKeyProvider
from app.core.key_management.types import KeyStatus
from app.models import IngestionBatch, Job
from tests.operator.conftest import BANK_KEY, operator_headers, provision_payload, start_inspection


@pytest.mark.parametrize("status", [KeyStatus.DISABLED, KeyStatus.UNAVAILABLE])
def test_operator_metadata_and_enqueue_do_not_open_protected_reports(
    operator_client: TestClient,
    operator_db: Session,
    status: KeyStatus,
) -> None:
    created = operator_client.post(
        "/operator/v1/tenants", json=provision_payload(), headers=operator_headers()
    )
    assert created.status_code == 200 and created.json()["succeeded"], created.text
    tenant = cast(dict[str, str], created.json())
    org, bank = tenant["organization_id"], tenant["bank_id"]
    batch = IngestionBatch(
        organization_id=org,
        bank_id=bank,
        source_system="DB_DIRECT",
        adapter_version="synthetic",
        extraction_mode="full",
        status="accepted",
        as_of_date=date(2026, 4, 30),
        etl_report={"dedup_status": "failed"},
    )
    operator_db.add(batch)
    operator_db.flush()
    operator_db.add(
        Job(
            organization_id=org,
            bank_id=bank,
            job_type="etl_dedup",
            status="failed",
            entity_type="ingestion_batch",
            entity_id=str(batch.id),
            attempts=3,
            max_attempts=3,
        )
    )
    batch.validation_report = {"identity": "Private identity"}
    batch.error_message = "Private identity"
    operator_db.commit()
    batch_id = batch.id
    start_inspection(operator_client, org)
    provider = cast(LocalKeyProvider, registry.provider_for("aws_kms"))
    provider.set_status(BANK_KEY, status)
    operator_db.expunge_all()
    for path in (
        "/operator/v1/tenants",
        f"/operator/v1/tenants/{org}",
        f"/operator/v1/tenants/{org}/activity",
        "/operator/v1/jobs/stuck-dedup",
    ):
        response = operator_client.get(path, headers=operator_headers())
        assert response.status_code == 200, (path, response.text)
        assert "Private identity" not in response.text
    for action in ("rerun-ingestion", "redrive-dedup"):
        response = operator_client.post(
            f"/operator/v1/tenants/{org}/fix/{action}",
            headers=operator_headers(),
            json={"batch_id": str(batch_id), "note": "Synthetic retry"},
        )
        assert response.status_code == 200, response.text
