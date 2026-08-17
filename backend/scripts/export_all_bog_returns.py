"""Generate EVERY official BoG return for a bank and export all three artifacts.

    uv run python scripts/export_all_bog_returns.py \\
        --base-url http://localhost:8001 --bank BK-0PMD7Z5M --out ../exports/bog_2026-06-30
    (token from the AEQ_TOKEN environment variable — never a command-line argument,
     which the process table exposes to every user on the machine)

For each registered BSD form this runs the REAL package pipeline through the API
(immutable snapshot + content digest + maker-checker lifecycle), then renders the
three artifacts of that one sealed run into ``--out``:

    <FORM>.pdf              values only — the Bank of Ghana submission package
    <FORM>.official.xlsx    official layout, values only, sheets protected (audit twin)
    <FORM>.working.xlsx     official layout with the template's LIVE formulas (ALM review)

and writes INDEX.md: per form the fill counts (mapped / input required / unmapped),
the package id and content digest, so the onboarding gap list is read off the real
bank rather than a fixture.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.regulatory_reporting.bog_forms.catalog import (
    all_form_codes,  # noqa: E402
    form_spec,  # noqa: E402
)
from app.services.regulatory_reporting.bog_forms.engine import FormResult  # noqa: E402
from app.services.regulatory_reporting.bog_forms.render import render_form_xlsx  # noqa: E402


def _client(base_url: str, token: str) -> httpx.Client:
    # BSD2 is 22 sheets and has taken >31 minutes to generate on a real book, so
    # a 30-minute ceiling times the client out on work the server then finishes.
    return httpx.Client(
        base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=7200.0
    )


def generate(client: httpx.Client, bank: str, code: str, reporting_date: str) -> dict[str, Any]:
    created = client.post(
        f"/api/v1/banks/{bank}/regulatory-packages",
        json={"return_code": code, "reporting_date": reporting_date},
    )
    created.raise_for_status()
    package = created.json()
    detail = client.get(f"/api/v1/banks/{bank}/regulatory-packages/{package['id']}")
    detail.raise_for_status()
    return detail.json()


def export_pdf(client: httpx.Client, bank: str, package_id: str, target: Path) -> bool:
    """Export + download the submission PDF through the real artifact endpoints."""
    exported = client.post(
        f"/api/v1/banks/{bank}/regulatory-packages/{package_id}/export", params={"kind": "pdf"}
    )
    if exported.status_code not in (200, 201):
        return False
    artifacts = client.get(f"/api/v1/banks/{bank}/regulatory-packages/{package_id}/artifacts")
    artifacts.raise_for_status()
    for artifact in artifacts.json()["artifacts"]:
        if artifact["kind"] == "pdf":
            blob = client.get(
                f"/api/v1/banks/{bank}/regulatory-artifacts/{artifact['id']}/download"
            )
            if blob.status_code == 200:
                target.write_bytes(blob.content)
                return True
    return False


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0915
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--bank", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--reporting-date", default=None, help="default: the bank's latest period end"
    )
    parser.add_argument("--only", default=None, help="comma-separated form codes")
    args = parser.parse_args(argv)

    token = os.environ.get("AEQ_TOKEN", "").strip()
    if not token:
        print("set AEQ_TOKEN (never pass a token on the command line)", file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC)

    with _client(args.base_url, token) as client:
        bank_row = client.get(f"/api/v1/banks/{args.bank}")
        bank_row.raise_for_status()
        bank_name = bank_row.json()["name"]
        reporting_date = args.reporting_date
        if reporting_date is None:
            periods = client.get(f"/api/v1/banks/{args.bank}/reporting-periods").json()["periods"]
            reporting_date = periods[0]["period_end"]

        codes = [c.strip() for c in args.only.split(",")] if args.only else list(all_form_codes())
        rows: list[dict[str, Any]] = []
        for code in codes:
            entry: dict[str, Any] = {"code": code}
            try:
                detail = generate(client, args.bank, code, reporting_date)
            except httpx.HTTPStatusError as exc:
                entry["error"] = f"{exc.response.status_code} {exc.response.text[:160]}"
                rows.append(entry)
                print(f"{code}: GENERATE FAILED {entry['error']}", flush=True)
                continue
            except httpx.HTTPError as exc:
                # transport-level failure (timeout, dropped connection): record
                # it and keep going — one slow return must not abandon the other
                # twenty-two, which is exactly what used to happen.
                entry["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
                rows.append(entry)
                print(f"{code}: GENERATE FAILED {entry['error']}", flush=True)
                continue
            snapshot = detail["snapshot"]
            payload = snapshot.get("bog_form", {})
            counts = payload.get("status_counts", {})
            entry |= {
                "package_id": detail["id"],
                "version": detail.get("version"),
                "digest": (detail.get("content_digest") or "")[:16],
                "mapped": counts.get("mapped", 0),
                "input_required": counts.get("input_required", 0),
                "unmapped": counts.get("unmapped", 0),
                "formulas": counts.get("derived", 0),
            }
            spec = form_spec(code)
            result = FormResult.from_snapshot(spec, snapshot)
            period_label = snapshot.get("reporting_period", {}).get("label", "")
            for mode, suffix in (("official", "official.xlsx"), ("working", "working.xlsx")):
                (args.out / f"{code}.{suffix}").write_bytes(
                    render_form_xlsx(
                        result,
                        bank_name=bank_name,
                        period_label=period_label,
                        reporting_date=reporting_date,
                        generated_at=stamp,
                        mode=mode,
                    )
                )
            entry["pdf"] = export_pdf(client, args.bank, detail["id"], args.out / f"{code}.pdf")
            rows.append(entry)
            print(
                f"{code}: mapped={entry['mapped']} input_required={entry['input_required']} "
                f"unmapped={entry['unmapped']} pdf={'yes' if entry['pdf'] else 'NO'}",
                flush=True,
            )

    index = args.out / "INDEX.md"
    with index.open("w") as fh:
        fh.write(f"# Bank of Ghana returns — {bank_name} — as at {reporting_date}\n\n")
        fh.write(f"Generated {stamp.isoformat()} through the real package pipeline ")
        fh.write("(immutable snapshot · content digest · maker-checker lifecycle).\n\n")
        fh.write("Each return ships three artifacts of the SAME sealed run:\n\n")
        fh.write("- `<FORM>.pdf` — values only: **the Bank of Ghana submission package**\n")
        fh.write(
            "- `<FORM>.official.xlsx` — official layout, values only, sheets protected "
            "(audit twin)\n"
        )
        fh.write(
            "- `<FORM>.working.xlsx` — official layout with the template's **live formulas** "
            "(ALM review; never filed)\n\n"
        )
        fh.write(
            "| Form | Package | Digest | Cells mapped | Input required | Unmapped | "
            "Template formulas | PDF |\n"
        )
        fh.write("|---|---|---|---|---|---|---|---|\n")
        for r in rows:
            if "error" in r:
                fh.write(f"| {r['code']} | — | — | — | — | — | — | {r['error']} |\n")
                continue
            fh.write(
                f"| {r['code']} | v{r['version']} | `{r['digest']}` | {r['mapped']} | "
                f"{r['input_required']} | {r['unmapped']} | {r['formulas']} | "
                f"{'✓' if r['pdf'] else '—'} |\n"
            )
        ok = [r for r in rows if "error" not in r]
        fh.write(
            f"\n**{len(ok)}/{len(rows)} returns generated.** "
            "'Input required' counts the official cells whose data the bank must still supply — "
            "each one is named cell-by-cell on that workbook's 'Completion notes' sheet.\n"
        )
    print(f"\nwrote {index}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
