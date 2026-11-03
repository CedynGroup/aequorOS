"""A push page is bounded by the bytes it stages, not only by its record count.

Why this exists (security audit, 2026-10-28 — issue #707)
---------------------------------------------------------
``MAX_RECORDS_PER_PAGE = 5_000`` caps how many records a page carries, and each
record is a loose dict validated later by the ingestion pipeline. So the cap
bounded nothing a caller cared about: a valid integration key could stage 5,000
arbitrarily large records per call and repeat across pages until the batch
expired, growing the staging objects for as long as it kept posting.

The byte cap is measured on the records as they are staged, so it matches what
the batch actually holds. These tests pin the measurement itself and the
boundary the service applies to it.
"""

from __future__ import annotations

import json
from datetime import date
from typing import cast
from unittest.mock import Mock
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.data_engine.schemas.push import (
    MAX_PAGE_BYTES,
    MAX_RECORDS_PER_PAGE,
    PushBatchOpen,
    PushRecordsPage,
)
from app.data_engine.service import push_ingestion
from app.models import Bank
from app.storage.client import StorageLocation
from tests.support.inmemory_storage import InMemoryStorageClient


def _page(records: list[dict[str, object]]) -> PushRecordsPage:
    return PushRecordsPage(entities={"gl_account": records})


def test_an_ordinary_page_is_far_under_the_cap() -> None:
    page = _page(
        [
            {
                "source_reference": f"GL-{index}",
                "account_code": f"GL-{index}",
                "name": "Cash at central bank",
            }
            for index in range(500)
        ]
    )

    assert page.payload_bytes < MAX_PAGE_BYTES // 10


@pytest.mark.parametrize("section", ["entities", "reference", "both"])
def test_the_measurement_is_the_staged_bytes(tenant_ctx: TenantContext, section: str) -> None:
    """The number is what the staging object holds, so it cannot be gamed by
    envelope formatting."""
    record = {"source_reference": "GL-1", "name": "Café"}
    page = PushRecordsPage(
        entities={"gl_account": [record]} if section != "reference" else {},
        reference={"yield_curve": [record]} if section != "entities" else {},
    )
    db, storage, push_id = _open_batch(tenant_ctx)

    result = push_ingestion.stage_push_records(
        db, tenant_ctx, "BK-SYNTH001", push_id, page, storage
    )
    _, stream = storage.read(
        StorageLocation("synthetic", "temp", f"push/{push_id}/pages/00001.json")
    )
    content = stream.read()

    assert result.pages_staged == 1
    assert page.payload_bytes == len(content)
    assert json.loads(content) == {"entities": page.entities, "reference": page.reference}


def test_a_few_large_records_pass_the_count_cap_and_fail_the_byte_cap() -> None:
    """The whole point: the record count is not a proxy for the size."""
    page = _page(
        [{"source_reference": f"GL-{index}", "notes": "x" * 200_000} for index in range(60)]
    )

    assert page.record_count <= MAX_RECORDS_PER_PAGE
    assert page.payload_bytes > MAX_PAGE_BYTES


def test_the_count_cap_still_applies() -> None:
    page = _page([{"source_reference": f"GL-{index}"} for index in range(MAX_RECORDS_PER_PAGE + 1)])

    assert page.record_count > MAX_RECORDS_PER_PAGE


@pytest.mark.parametrize("size", [MAX_PAGE_BYTES, MAX_PAGE_BYTES + 1])
def test_the_boundary_is_measured_in_bytes(tenant_ctx: TenantContext, size: int) -> None:
    """The refusal is ``>``: a page exactly at the cap is accepted, as with the
    record cap beside it."""
    record: dict[str, object] = {"source_reference": "GL-1", "notes": ""}
    envelope = {"entities": {"gl_account": [record]}, "reference": {}}
    record["notes"] = "x" * (size - len(json.dumps(envelope).encode()))
    page = _page([record])
    db, storage, push_id = _open_batch(tenant_ctx)
    location = StorageLocation("synthetic", "temp", f"push/{push_id}/pages/00001.json")

    assert page.payload_bytes == size
    if size > MAX_PAGE_BYTES:
        with pytest.raises(HTTPException) as refusal:
            push_ingestion.stage_push_records(db, tenant_ctx, "BK-SYNTH001", push_id, page, storage)
        assert refusal.value.status_code == 413
        assert not storage.exists(location)
        assert (
            push_ingestion.get_push_batch(
                db, tenant_ctx, "BK-SYNTH001", push_id, storage
            ).pages_staged
            == 0
        )
    else:
        push_ingestion.stage_push_records(db, tenant_ctx, "BK-SYNTH001", push_id, page, storage)
        _, stream = storage.read(location)
        assert len(stream.read()) == MAX_PAGE_BYTES


def _open_batch(ctx: TenantContext) -> tuple[Session, InMemoryStorageClient, UUID]:
    bank = Bank(id="BK-SYNTH001", organization_id=ctx.organization_id, storage_slug="synthetic")
    db = cast("Session", Mock(spec=Session, scalar=Mock(return_value=bank)))
    storage = InMemoryStorageClient()
    opened = push_ingestion.open_push_batch(
        db,
        ctx,
        bank.id,
        PushBatchOpen(
            as_of_date=date(2026, 6, 30),
            idempotency_key="byte-cap",
            reason="Synthetic byte-boundary test.",
        ),
        storage,
    )
    return db, storage, opened.push_batch_id
