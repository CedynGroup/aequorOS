"""Runnable Push API client: file-upload/API equivalence on the same dataset.

Reads ``data/03_gl_accounts.csv`` and ``data/04_products.csv`` (the same files
the Data Engine console ingests as uploads), converts them to the push
contract documented in ``docs/API_INTEGRATION.md``, drives the three-call
flow — open, stage record pages, commit — and prints the resulting
validation report. Stdlib only; point it at any running backend:

    .venv/bin/python scripts/push_api_example.py \
        --base-url http://127.0.0.1:8003 \
        --org-id 11111111-1111-4111-8111-111111111111 \
        --user-id aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa \
        --as-of 2026-04-30

Without ``--bank-id`` the first bank visible to the tenant is used.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"

# The push contract caps a records page at 5,000 records; the example stages
# one page per source file, which stays far below the cap.
GL_FILE = DATA_DIR / "03_gl_accounts.csv"
PRODUCT_FILE = DATA_DIR / "04_products.csv"


class PushClient:
    def __init__(self, base_url: str, org_id: str, user_id: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "X-Org-Id": org_id,
            "X-User-Id": user_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}/api/v1{path}",
            method=method,
            headers=self.headers,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
        )
        try:
            with urllib.request.urlopen(request) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            sys.exit(f"{method} {path} -> HTTP {exc.code}\n{detail}")


def _null_if_blank(value: str) -> str | None:
    return value.strip() or None


def read_gl_accounts() -> list[dict[str, Any]]:
    """03_gl_accounts.csv -> canonical gl_account records (identity mapping)."""
    records: list[dict[str, Any]] = []
    with GL_FILE.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            records.append(
                {
                    "source_reference": row["gl_code"],
                    "account_code": row["gl_code"],
                    "name": row["gl_name"],
                    "account_class": row["gl_side"],
                    "currency": _null_if_blank(row["currency"]),
                    "balance": _null_if_blank(row["balance_ghs"]),
                }
            )
    return records


def read_products() -> list[dict[str, Any]]:
    """04_products.csv -> canonical product records (identity mapping)."""
    records: list[dict[str, Any]] = []
    with PRODUCT_FILE.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            records.append(
                {
                    "source_reference": row["product_code"],
                    "product_code": row["product_code"],
                    "name": row["product_name"],
                    "regulatory_category": _null_if_blank(row["regulatory_category"]),
                    "risk_weight_code": _null_if_blank(row["risk_weight"]),
                    # Columns with no dedicated canonical home ride along
                    # verbatim, exactly like attribute_columns do for uploads.
                    "attributes": {
                        "product_type": row["product_type"],
                        "currency": row["currency"],
                        "rate_type": row["rate_type"],
                        "min_tenor_months": row["min_tenor_months"],
                        "max_tenor_months": row["max_tenor_months"],
                        "typical_rate_low": row["typical_rate_low"],
                        "typical_rate_high": row["typical_rate_high"],
                    },
                }
            )
    return records


def print_report(started: dict[str, Any]) -> None:
    batch = started["batch"]
    summary = batch["validation_report"]["summary"]
    print(
        f"POST …/commit -> batch {batch['id']} {batch['status'].upper()} "
        f"(reused={str(started['reused']).lower()})"
    )
    print(
        f"  extracted={summary['records_extracted']} "
        f"translated={summary['records_translated']} "
        f"accepted={summary['records_accepted']} "
        f"warnings={summary['records_warning']} "
        f"errors={summary['records_error']} "
        f"blocked={summary['records_blocked']}"
    )
    print("  tables:")
    for entry in batch["validation_report"].get("tables", []):
        resolved = entry["resolved_to"] or "UNMATCHED"
        print(
            f"    {entry['source_table']:<14} -> {resolved:<14} "
            f"{entry['rows_extracted']:>4} extracted / {entry['rows_accepted']:>4} accepted"
        )
    failures = batch["validation_report"].get("failures", [])
    if failures:
        print("  findings:")
        for finding in failures:
            print(f"    [{finding['severity']}] {finding['rule']}: {finding['detail']}")
    print(f"  raw artifact: {batch['raw_artifact_path']}")
    print(f"  report artifact: {batch['report_artifact_path']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8003")
    parser.add_argument("--org-id", default="11111111-1111-4111-8111-111111111111")
    parser.add_argument("--user-id", default="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    parser.add_argument("--bank-id", default=None, help="Defaults to the tenant's first bank.")
    parser.add_argument("--as-of", default="2026-04-30")
    parser.add_argument(
        "--idempotency-key",
        default=None,
        help="Defaults to push-example-<as-of>; rerunning reuses the committed batch.",
    )
    args = parser.parse_args()

    client = PushClient(args.base_url, args.org_id, args.user_id)
    bank_id = args.bank_id
    if bank_id is None:
        banks = client.call("GET", "/banks")["banks"]
        if not banks:
            sys.exit("No banks visible to this tenant; pass --bank-id.")
        bank_id = banks[0]["id"]
        print(f"Using bank {bank_id} ({banks[0].get('name', 'unnamed')})")

    key = args.idempotency_key or f"push-example-{args.as_of}"
    opened = client.call(
        "POST",
        f"/banks/{bank_id}/push-batches",
        {
            "as_of_date": args.as_of,
            "idempotency_key": key,
            "reason": "Push API example: GL accounts + product catalog.",
        },
    )
    push_id = opened["push_batch_id"]
    print(f"POST /push-batches -> {opened['status']} push {push_id} (key={key})")

    if opened["status"] == "committed":
        print("Push batch already committed for this idempotency key; recommitting is a no-op.")
    elif opened["pages_staged"] > 0:
        print(
            f"Push batch already has {opened['pages_staged']} staged pages "
            "(pages accumulate); skipping staging and committing what is there."
        )
    else:
        pages: list[dict[str, Any]] = [
            {"entities": {"gl_account": read_gl_accounts()}},
            {"entities": {"product": read_products()}},
        ]
        for number, page in enumerate(pages, start=1):
            staged = client.call("POST", f"/banks/{bank_id}/push-batches/{push_id}/records", page)
            print(
                f"POST …/records (page {number}/{len(pages)}) -> staged {staged['records_staged']}"
            )

    started = client.call("POST", f"/banks/{bank_id}/push-batches/{push_id}/commit", None)
    print_report(started)


if __name__ == "__main__":
    main()
