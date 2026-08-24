#!/usr/bin/env python3
"""Push the per-book AequorOS SDI time-series files, grouped by as_of_date, through
the Data Engine API (three-call flow per date, docs/API_INTEGRATION.md §2).

    BASE_URL=http://localhost:8001 TOKEN=<admin token or aeq_live_...> BANK=BK-XREAZES1 \\
        python push_sdi.py --cadence monthly   # start light; then weekly, then daily, then all
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import urllib.request
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = os.environ["BASE_URL"].rstrip("/")
TOKEN = os.environ["TOKEN"]
BANK = os.environ["BANK"]
HDR = {"authorization": f"Bearer {TOKEN}", "content-type": "application/json"}


def _coerce(v):
    if v == "":
        return None
    try:
        return int(v) if v.lstrip("-").isdigit() else float(v)
    except ValueError:
        return {"true": True, "false": False}.get(v.lower(), v)


def _rows(fname):
    p = HERE / fname
    if not p.exists():
        return []
    with p.open() as fh:
        return list(csv.DictReader(line for line in fh if not line.startswith("#")))


def _record(row, drop):
    rec, attrs = {}, {}
    for k, v in row.items():
        if k in drop:
            continue
        cv = _coerce(v)
        if cv is None:
            continue
        if k.startswith("attributes."):
            attrs[k.split(".", 1)[1]] = cv
        else:
            rec[k] = cv
    if attrs:
        rec["attributes"] = attrs
    return rec


def _call(method, path, body=None):
    req = urllib.request.Request(
        f"{BASE}{path}",
        method=method,
        headers=HDR,
        data=json.dumps(body).encode() if body is not None else None,
    )
    with urllib.request.urlopen(req) as r:
        return json.load(r) if r.read else {}


def _push_date(as_of, entities, reference, key=None):
    key = key or f"sdiload-{as_of}"
    pb = _call(
        "POST",
        f"/api/v1/banks/{BANK}/push-batches",
        {"as_of_date": as_of, "idempotency_key": key, "reason": f"AequorOS SDI {as_of}"},
    )
    if pb.get("status") == "committed":
        return True  # resumable: this date is already ingested, skip
    pid = pb["push_batch_id"]

    def page(ent, ref):
        _call(
            "POST",
            f"/api/v1/banks/{BANK}/push-batches/{pid}/records",
            {"entities": ent, "reference": ref},
        )

    # entity records, paged at 4000
    buf_e, n = defaultdict(list), 0
    for kind, rows in entities.items():
        for r in rows:
            buf_e[kind].append(r)
            n += 1
            if n >= 4000:
                page(dict(buf_e), {})
                buf_e, n = defaultdict(list), 0
    if n:
        page(dict(buf_e), {})
    if reference:
        page({}, reference)
    _call("POST", f"/api/v1/banks/{BANK}/push-batches/{pid}/commit")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cadence", choices=["monthly", "weekly", "daily", "all"], default="monthly")
    ap.add_argument("--reverse", action="store_true", help="push latest dates first")
    # Extending an already-loaded tenant: push only the dates it does not have.
    # Without this the only way to add a month was to re-push the whole series
    # (504 batches on the reference SDI), so a two-month extension cost a full
    # reload. The generator is seeded, so re-running it reproduces the existing
    # dates byte-identically and --since selects exactly the new tail.
    ap.add_argument("--since", help="only push as-of dates AFTER this ISO date")
    ap.add_argument("--until", help="only push as-of dates up to and including this ISO date")
    # Backfilling ONE book across dates that already carry the others. The GL
    # used to be struck only on month-ends and Fridays, so 376 of this tenant's
    # 513 position dates had no same-date general ledger and could not satisfy
    # the balance-sheet identity — which the live plane, anchored on the freshest
    # ingested date, surfaced as an unreconciled tenant. Re-pushing every book
    # for every date to fix that costs hours; pushing just the missing book does
    # not.
    ap.add_argument("--books", help="comma-separated book list, e.g. gl_accounts,capital_structure")
    # A date already ingested is SKIPPED by idempotency key (``sdiload-<date>``),
    # which is what makes this script resumable. Backfilling a book onto such a
    # date therefore needs its own key — and should have one regardless: a
    # backfill is a distinct ingestion event and deserves its own batch in the
    # lineage rather than masquerading as the original load.
    ap.add_argument("--key-suffix", default="", help="batch idempotency-key suffix for a backfill")
    a = ap.parse_args()
    cal = {r["as_of_date"]: r["cadence"] for r in _rows("as_of_calendar.csv")}
    want = {
        "monthly": {"monthly"},
        "weekly": {"monthly", "weekly"},
        "daily": {"monthly", "weekly", "daily"},
        "all": {"monthly", "weekly", "daily"},
    }[a.cadence]
    dates = sorted((d for d, c in cal.items() if c in want), reverse=a.reverse)
    if a.since:
        dates = [d for d in dates if d > a.since]
    if a.until:
        dates = [d for d in dates if d <= a.until]
    if not dates:
        print("no as-of dates match the selected cadence/window")
        return
    print(f"pushing {len(dates)} as-of dates: {min(dates)} .. {max(dates)}")
    latest = max(dates)
    # entities pushed once (counterparties/products) on a DISTINCT batch key, so the
    # first date's positions batch is not the already-committed entities batch.
    first = dates[0]
    _push_date(
        first,
        {"counterparty": _record_all("counterparties.csv"), "product": _record_all("products.csv")},
        {"behavioral_assumptions": _record_all("behavioral_assumptions.csv")},
        key=f"sdiload-ent-{first}",
    )
    all_books = [
        "positions_deposits.csv",
        "positions_loans.csv",
        "positions_cash.csv",
        "positions_securities.csv",
        "gl_accounts.csv",
    ]
    if a.books:
        want_books = {b if b.endswith(".csv") else f"{b}.csv" for b in a.books.split(",")}
        all_books = [b for b in all_books if b in want_books]
        print(f"books: {', '.join(all_books) or '(none)'}")
    by = {f: _group(f) for f in all_books}
    # A filtered-out book contributes nothing rather than raising, so
    # ``--books gl_accounts`` backfills one ledger without touching positions.
    by = {
        f: by.get(f, {})
        for f in [
            "positions_deposits.csv",
            "positions_loans.csv",
            "positions_cash.csv",
            "positions_securities.csv",
            "gl_accounts.csv",
        ]
    }
    cs = _group("capital_structure.csv")
    # historical_cashflows: the whole daily series, kept with its `date` column
    # (the consumer reads the latest batch's full rows), pushed once on the last date.
    cf_all = [_record(r, set()) for r in _rows("daily_cashflows.csv")]
    fin_all = sorted(
        (_record(r, set()) for r in _rows("historical_financials.csv")),
        key=lambda r: r["period_end"],
    )
    for i, as_of in enumerate(dates, 1):
        ent = {
            "position": sum(
                (
                    by[f].get(as_of, [])
                    for f in [
                        "positions_deposits.csv",
                        "positions_loans.csv",
                        "positions_cash.csv",
                        "positions_securities.csv",
                    ]
                ),
                [],
            ),
            "gl_account": by["gl_accounts.csv"].get(as_of, []),
        }
        ref = {}
        if cs.get(as_of):
            ref["capital_structure"] = cs[as_of]
        # Trailing 36 months of P&L ending at this as-of date: the derivation
        # builds up to three annual windows and needs 12 months minimum.
        fin_win = [r for r in fin_all if r["period_end"] <= as_of][-36:]
        if fin_win:
            ref["historical_financials"] = fin_win
        if as_of == latest:
            ref["historical_cashflows"] = cf_all
        skipped = _push_date(
            as_of, ent, ref, key=f"sdiload-{as_of}{a.key_suffix}" if a.key_suffix else None
        )
        print(f"[{i}/{len(dates)}] {'skip' if skipped else 'pushed'} {as_of}", flush=True)
    print(f"Done — {len(dates)} dates ({a.cadence}) for {BANK}.")


def _record_all(fname):
    return [_record(r, {"as_of_date"}) for r in _rows(fname)]


def _group(fname):
    g = defaultdict(list)
    for r in _rows(fname):
        g[r["as_of_date"]].append(_record(r, {"as_of_date"}))
    return g


if __name__ == "__main__":
    main()
