"""Tenant staging handles and trusted local sources have separate boundaries."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import cast

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.data_engine.domain.ingestion.locations import StagedIngestionLocation
from app.data_engine.schemas.ingestion import IngestionBatchCreate, InternalIngestionBatchCreate
from app.data_engine.service import ingestion
from tests.support.inmemory_storage import InMemoryStorageClient


@pytest.mark.parametrize(
    "location",
    [
        "/tmp/synthetic.xlsx",
        "relative.xlsx",
        "../synthetic.xlsx",
        r"C:\staging\synthetic.xlsx",
        "file:///tmp/synthetic.xlsx",
        "https://example.test/synthetic.xlsx",
        "s3://foreign-bank/synthetic.xlsx",
        "temp://",
        "TEMP://uploads/source.xlsx",
        "temp:///tmp/source.xlsx",
        "temp://uploads/../source.xlsx",
        "temp://uploads/./source.xlsx",
        "temp://uploads/..",
        "temp://uploads/%2E%2E/source.xlsx",
        "temp://uploads//source.xlsx",
        "temp://uploads/source.xlsx/",
        r"temp://uploads\..\source.xlsx",
        "temp://uploads/source.xlsx?version=other",
        "temp://uploads/source.xlsx#fragment",
        "temp://uploads/source.xlsx\n",
        "temp://uploads/source.xlsx\x00",
        {"path": "/tmp/source.xlsx"},
        {"root": "temp://uploads/source.xlsx"},
    ],
)
def test_api_schema_rejects_non_staged_locations(location: object) -> None:
    with pytest.raises(ValidationError):
        IngestionBatchCreate.model_validate(
            {
                "source_system": "EXCEL_CSV",
                "as_of_date": "2026-01-31",
                "location": location,
                "reason": "Synthetic schema check.",
            }
        )


@pytest.mark.parametrize(
    "object_path",
    ["uploads/abc/source.xlsx", "push/abc/source.json", "uploads/abc/.hidden..xlsx"],
)
def test_staged_handle_roundtrips_as_an_opaque_string(object_path: str) -> None:
    token = f"temp://{object_path}"
    location = StagedIngestionLocation(token)
    payload = IngestionBatchCreate(
        source_system="EXCEL_CSV",
        as_of_date=date(2026, 1, 31),
        location=location,
        reason="Synthetic roundtrip.",
    )
    assert location.object_path == object_path
    assert str(location) == token
    assert payload.model_dump(mode="json")["location"] == token
    assert IngestionBatchCreate.model_validate_json(payload.model_dump_json()) == payload


@pytest.mark.parametrize(
    "location",
    [
        "/tmp/source.xlsx",
        Path("/tmp/source.xlsx"),
        StagedIngestionLocation.model_construct(root="temp://uploads/../source.xlsx"),
    ],
)
def test_tenant_service_revalidates_bypassed_schema(
    tenant_ctx: TenantContext, location: object
) -> None:
    payload = IngestionBatchCreate.model_construct(
        source_system="EXCEL_CSV",
        as_of_date=date(2026, 1, 31),
        location=location,
        reason="Synthetic validation bypass.",
    )
    # An unbound session fails if the service attempts any database operation.
    with Session() as db, pytest.raises(HTTPException) as failure:
        ingestion.start_uploaded_ingestion(
            db, tenant_ctx, "BK-SYNTH001", payload, InMemoryStorageClient()
        )
    assert failure.value.status_code == 422


def test_tenant_service_rejects_an_internal_source(tenant_ctx: TenantContext) -> None:
    internal = InternalIngestionBatchCreate(
        source_system="EXCEL_CSV",
        as_of_date=date(2026, 1, 31),
        location=Path("/tmp/source.xlsx"),
        reason="Synthetic internal source.",
    )
    with Session() as db, pytest.raises(HTTPException) as failure:
        ingestion.start_uploaded_ingestion(
            db,
            tenant_ctx,
            "BK-SYNTH001",
            cast(IngestionBatchCreate, internal),
            InMemoryStorageClient(),
        )
    assert failure.value.status_code == 422


def test_internal_ingestion_revalidates_staged_sources(tenant_ctx: TenantContext) -> None:
    payload = IngestionBatchCreate.model_construct(
        source_system="EXCEL_CSV",
        as_of_date=date(2026, 1, 31),
        location=StagedIngestionLocation.model_construct(root="/tmp/source.xlsx"),
        reason="Synthetic validation bypass.",
    )
    with Session() as db, pytest.raises(ValidationError):
        ingestion.start_ingestion(db, tenant_ctx, "BK-SYNTH001", payload, InMemoryStorageClient())
