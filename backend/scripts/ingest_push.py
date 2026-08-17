"""Generic CSV → AequorOS Push API client (the Data Engine's API-push path).

    uv run python scripts/ingest_push.py \\
        --base-url http://localhost:8001 --token "$AEQ_TOKEN" --bank BK-0PMD7Z5M \\
        --as-of 2026-06-30 --reason "Sample Bank onboarding: tariff schedule" \\
        --reference tariff_schedule=onboarding/sample_bank/tariff_schedule.csv \\
        --entity position=onboarding/sample_bank/loans_with_attributes.csv

Runs the three-call flow documented in docs/API_INTEGRATION.md §2 (open push
batch → stage record pages ≤ 5,000 records → commit) with a bearer token: an
admin access token or an ``aeq_live_…`` integration key. This is an ingestion
CLIENT any bank's IT could run — it carries NO data of its own and is not a
seed path: every row still passes the standard validation/translation pipeline
and lands with lineage under ``source_system = "API_PUSH"``.

CSV conventions: header row = field names; blank cell = field omitted; a
column named ``attributes.<key>`` on an entity file becomes ``attributes[key]``
(numeric-looking values are sent as numbers, ``true``/``false`` as booleans);
lines starting with ``#`` are ignored (dataset provenance comments).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

import httpx

PAGE_SIZE = 4_000  # under the API's 5,000-records-per-page cap


def _coerce(value: str) -> Any:
    text = value.strip()
    if text == "":
        return None
    low = text.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        if text.lstrip("-").isdigit():
            return int(text)
        return float(text.replace(",", ""))
    except ValueError:
        return text


def read_rows(path: Path, *, entity: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        lines = [line for line in fh if not line.lstrip().startswith("#")]
    reader = csv.DictReader(lines)
    for raw in reader:
        row: dict[str, Any] = {}
        attributes: dict[str, Any] = {}
        for key, value in raw.items():
            if key is None:
                continue
            if value is None or value.strip() == "":
                continue
            if entity and key.startswith("attributes."):
                attributes[key[len("attributes.") :]] = _coerce(value)
            elif entity:
                row[key] = _coerce(value)
            else:
                # reference rows: preserved verbatim by the platform (stringified),
                # so send text as-is
                row[key] = value.strip()
        if attributes:
            row["attributes"] = attributes
        if row:
            rows.append(row)
    return rows


def push(  # noqa: PLR0913
    *,
    base_url: str,
    token: str,
    bank: str,
    as_of: str,
    reason: str,
    idempotency_key: str,
    entities: dict[str, list[dict[str, Any]]],
    references: dict[str, list[dict[str, Any]]],
    timeout: float = 600.0,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(base_url=base_url, headers=headers, timeout=timeout) as client:
        opened = client.post(
            f"/api/v1/banks/{bank}/push-batches",
            json={"as_of_date": as_of, "idempotency_key": idempotency_key, "reason": reason},
        )
        opened.raise_for_status()
        push_id = opened.json()["push_batch_id"]
        # stage pages: flatten to (section, key, row) and cut every PAGE_SIZE
        items: list[tuple[str, str, dict[str, Any]]] = []
        for key, rows in entities.items():
            items.extend(("entities", key, r) for r in rows)
        for key, rows in references.items():
            items.extend(("reference", key, r) for r in rows)
        for start in range(0, len(items), PAGE_SIZE):
            page: dict[str, dict[str, list[dict[str, Any]]]] = {"entities": {}, "reference": {}}
            for section, key, row in items[start : start + PAGE_SIZE]:
                page[section].setdefault(key, []).append(row)
            body = {k: v for k, v in page.items() if v}
            staged = client.post(f"/api/v1/banks/{bank}/push-batches/{push_id}/records", json=body)
            staged.raise_for_status()
        committed = client.post(f"/api/v1/banks/{bank}/push-batches/{push_id}/commit")
        committed.raise_for_status()
        return committed.json()


def _kv(pairs: list[str]) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for pair in pairs:
        key, _, path = pair.partition("=")
        if not key or not path:
            msg = f"expected KEY=PATH, got {pair!r}"
            raise SystemExit(msg)
        out[key] = Path(path)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token", required=True, help="admin access token or aeq_live_… key")
    parser.add_argument("--bank", required=True, help="bank platform id, e.g. BK-0PMD7Z5M")
    parser.add_argument("--as-of", required=True, help="ISO business date the rows describe")
    parser.add_argument("--reason", required=True)
    parser.add_argument("--idempotency-key", default=None)
    parser.add_argument(
        "--entity",
        action="append",
        default=[],
        help="KIND=path.csv (gl_account|counterparty|product|position)",
    )
    parser.add_argument(
        "--reference",
        action="append",
        default=[],
        help="KIND=path.csv (a REFERENCE_DATASET_KINDS kind)",
    )
    args = parser.parse_args(argv)

    entities = {k: read_rows(p, entity=True) for k, p in _kv(args.entity).items()}
    references = {k: read_rows(p, entity=False) for k, p in _kv(args.reference).items()}
    if not entities and not references:
        print("nothing to push (give --entity and/or --reference)", file=sys.stderr)
        return 1
    key = args.idempotency_key or (
        "push:" + args.as_of + ":" + ",".join(sorted([*entities, *references]))
    )
    result = push(
        base_url=args.base_url,
        token=args.token,
        bank=args.bank,
        as_of=args.as_of,
        reason=args.reason,
        idempotency_key=key,
        entities=entities,
        references=references,
    )
    batch = result.get("batch", result)
    print(
        json.dumps(
            {
                "batch_id": batch.get("id"),
                "status": batch.get("status"),
                "records_extracted": batch.get("records_extracted"),
                "records_accepted": batch.get("records_accepted"),
                "summary": (batch.get("validation_report") or {}).get("summary"),
                "reused": result.get("reused"),
            },
            indent=1,
            default=str,
        )
    )
    return 0 if batch.get("status") in ("accepted", "accepted_with_warnings") else 2


if __name__ == "__main__":
    raise SystemExit(main())
