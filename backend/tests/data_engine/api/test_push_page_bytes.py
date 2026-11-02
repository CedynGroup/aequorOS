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

import pytest

from app.data_engine.schemas.push import (
    MAX_PAGE_BYTES,
    MAX_RECORDS_PER_PAGE,
    PushRecordsPage,
)


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


def test_the_measurement_is_the_staged_bytes() -> None:
    """The number is what the staging object holds, so it cannot be gamed by
    envelope formatting."""
    page = _page([{"source_reference": "GL-1", "name": "x"}])

    expected = len(
        json.dumps(
            {"entities": page.entities, "reference": page.reference},
            separators=(",", ":"),
            default=str,
        ).encode()
    )
    assert page.payload_bytes == expected


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
def test_the_boundary_is_measured_in_bytes(size: int) -> None:
    """The refusal is ``>``: a page exactly at the cap is accepted, as with the
    record cap beside it."""
    padding = "x" * size
    page = _page([{"source_reference": "GL-1", "notes": padding}])

    assert (page.payload_bytes > MAX_PAGE_BYTES) == (size > MAX_PAGE_BYTES - 1_000)
