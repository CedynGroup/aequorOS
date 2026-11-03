"""API schemas for the push ingestion flow (open → stage records → commit).

The record payloads are deliberately loose dicts here: the push endpoints
validate the ENVELOPE (known entity/reference keys, lists of objects, page
cap) and the ingestion pipeline validates the CONTENT — field coercion
failures land in ``translation_failures`` and business rules in the batch
validation report, exactly as they do for file uploads.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.data_engine.domain.ingestion.constants import ReferenceDatasetKind
from app.data_engine.domain.ingestion.contracts import EntityType

# Records staged per page; larger submissions are split across pages.
MAX_RECORDS_PER_PAGE = 5_000

# Serialized bytes of one page. The record cap bounds COUNT, and each record is
# a loose dict, so without this a page of 5,000 arbitrarily large records is
# accepted and staged (issue #707). A canonical position row is a few hundred
# bytes of JSON, so eight megabytes is roughly ten times the largest page
# anybody assembles from the documented contract.
MAX_PAGE_BYTES = 8 * 1024 * 1024

PushBatchStatus = Literal["staging", "committed"]


class PushBatchOpen(BaseModel):
    as_of_date: date
    # Client-chosen key, unique per bank: reopening with the same key returns
    # the same push batch, and committing it twice returns the same
    # ingestion batch.
    idempotency_key: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1)


class PushRecordsPage(BaseModel):
    """One page of records: canonical entities and/or reference datasets.

    Keys are constrained to the canonical entity types and reference dataset
    kinds; each value is a list of records whose field names follow the
    documented contract (or the bank's API_PUSH mapping config).
    """

    entities: dict[EntityType, list[dict[str, Any]]] = Field(default_factory=dict)
    reference: dict[ReferenceDatasetKind, list[dict[str, Any]]] = Field(default_factory=dict)

    @property
    def record_count(self) -> int:
        return sum(len(rows) for rows in self.entities.values()) + sum(
            len(rows) for rows in self.reference.values()
        )

    @property
    def payload_bytes(self) -> int:
        """The page as it will be staged: UTF-8 bytes of the records only.

        Measured on the records, not the request, so the number matches what the
        staging object holds and cannot be inflated or deflated by envelope
        whitespace. This measures the serialization before acceptance; staging
        serializes the accepted page again.
        """
        return len(
            json.dumps(
                {"entities": self.entities, "reference": self.reference},
            ).encode()
        )


class PushBatchStatusRead(BaseModel):
    push_batch_id: UUID
    bank_id: str
    as_of_date: date
    idempotency_key: str
    status: PushBatchStatus
    pages_staged: int
    # Running totals per entities/reference key.
    records_staged: dict[str, int]
    total_records_staged: int
    # Set once committed: the ingestion batch the staged records became.
    committed_batch_id: UUID | None
    expires_note: str
