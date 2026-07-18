"""Small RawRecord builders shared across ML-ETL dedup/anomaly tests."""

from __future__ import annotations

from typing import Any

from app.domain.ingestion.contracts import RawRecord


def counterparty(
    cid: str,
    name: str,
    *,
    source: str = "upload",
    **fields: Any,
) -> RawRecord:
    data: dict[str, Any] = {"counterparty_id": cid, "counterparty_name": name, **fields}
    return RawRecord(
        entity_type="counterparty",
        source_locator=f"{source}#{cid}",
        source_table=source,
        data=data,
    )


def position(
    locator: str,
    *,
    source_reference: str,
    as_of_date: str,
    balance_ghs: str,
    **fields: Any,
) -> RawRecord:
    data: dict[str, Any] = {
        "position_id": locator,
        "source_reference": source_reference,
        "as_of_date": as_of_date,
        "balance_ghs": balance_ghs,
        **fields,
    }
    return RawRecord(
        entity_type="position",
        source_locator=locator,
        source_table="loans",
        data=data,
    )
