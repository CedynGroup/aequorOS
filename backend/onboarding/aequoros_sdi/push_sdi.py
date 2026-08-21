#!/usr/bin/env python3
"""Push the per-book AequorOS SDI time-series files, grouped by as_of_date, through
the Data Engine API (three-call flow per date, docs/API_INTEGRATION.md §2).

    BASE_URL=http://localhost:8001 TOKEN=<admin token or aeq_live_...> BANK=BK-XREAZES1 \\
        python push_sdi.py --cadence monthly   # start light; then weekly, then daily, then all
"""
from __future__ import annotations
import argparse, csv, os, sys, time, urllib.request, json
from collections import defaultdict
from pathlib import Path
from urllib.error import HTTPError, URLError

HERE = Path(__file__).resolve().parent
BASE = os.environ["BASE_URL"].rstrip("/"); TOKEN = os.environ["TOKEN"]; BANK = os.environ["BANK"]
HDR = {"authorization": f"Bearer {TOKEN}", "content-type": "application/json"}

def _coerce(v):
    if v == "": return None
    try: return int(v) if v.lstrip("-").isdigit() else float(v)
    except ValueError:
        return {"true": True, "false": False}.get(v.lower(), v)

def _rows(fname):
    p = HERE / fname
    if not p.exists(): return []
    with p.open() as fh:
        return list(csv.DictReader(l for l in fh if not l.startswith("#")))

def _record(row, drop):
    rec, attrs = {}, {}
    for k, v in row.items():
        if k in drop: continue
        cv = _coerce(v)
        if cv is None: continue
        if k.startswith("attributes."): attrs[k.split(".", 1)[1]] = cv
        else: rec[k] = cv
    if attrs: rec["attributes"] = attrs
    return rec

def _call(method, path, body=None, attempts=6):
    # Retry transient connection resets / 5xx (a backend --reload drops in-flight
    # requests; cedynhq MinIO can blip) with exponential backoff, so one hiccup no
    # longer kills the whole multi-hour push. 4xx are logical → re-raised at once.
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(f"{BASE}{path}", method=method, headers=HDR, data=data)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r) if r.read else {}
        except HTTPError as e:
            if e.code in (500, 502, 503, 504) and attempt < attempts:
                time.sleep(min(2 ** attempt, 30)); continue
            raise
        except (URLError, OSError):
            if attempt < attempts:
                time.sleep(min(2 ** attempt, 30)); continue
            raise
    return {}

def _push_date(as_of, entities, reference, key=None):
    key = key or f"sdiload-{as_of}"
    pb = _call("POST", f"/api/v1/banks/{BANK}/push-batches",
               {"as_of_date": as_of, "idempotency_key": key,
                "reason": f"AequorOS SDI {as_of}"})
    if pb.get("status") == "committed":
        return True  # resumable: this date is already ingested, skip
    pid = pb["push_batch_id"]
    def page(ent, ref):
        _call("POST", f"/api/v1/banks/{BANK}/push-batches/{pid}/records",
              {"entities": ent, "reference": ref})
    # entity records, paged at 4000
    buf_e, n = defaultdict(list), 0
    for kind, rows in entities.items():
        for r in rows:
            buf_e[kind].append(r); n += 1
            if n >= 4000: page(dict(buf_e), {}); buf_e, n = defaultdict(list), 0
    if n: page(dict(buf_e), {})
    if reference: page({}, reference)
    _call("POST", f"/api/v1/banks/{BANK}/push-batches/{pid}/commit")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cadence", choices=["monthly","weekly","daily","all"], default="monthly")
    ap.add_argument("--reverse", action="store_true", help="push latest dates first")
    a = ap.parse_args()
    cal = {r["as_of_date"]: r["cadence"] for r in _rows("as_of_calendar.csv")}
    want = {"monthly": {"monthly"}, "weekly": {"monthly","weekly"},
            "daily": {"monthly","weekly","daily"}, "all": {"monthly","weekly","daily"}}[a.cadence]
    dates = sorted((d for d, c in cal.items() if c in want), reverse=a.reverse)
    latest = max(dates)
    # entities pushed once (counterparties/products) on a DISTINCT batch key, so the
    # first date's positions batch is not the already-committed entities batch.
    first = dates[0]
    _push_date(first, {"counterparty": _record_all("counterparties.csv"),
                       "product": _record_all("products.csv")},
               {"behavioral_assumptions": _record_all("behavioral_assumptions.csv")},
               key=f"sdiload-ent-{first}")
    by = {f: _group(f) for f in ["positions_deposits.csv","positions_loans.csv",
          "positions_cash.csv","positions_securities.csv","gl_accounts.csv"]}
    cs = _group("capital_structure.csv")
    # historical_cashflows: the whole daily series, kept with its `date` column
    # (the consumer reads the latest batch's full rows), pushed once on the last date.
    cf_all = [_record(r, set()) for r in _rows("daily_cashflows.csv")]
    for i, as_of in enumerate(dates, 1):
        ent = {"position": sum((by[f].get(as_of, []) for f in
               ["positions_deposits.csv","positions_loans.csv","positions_cash.csv",
                "positions_securities.csv"]), []),
               "gl_account": by["gl_accounts.csv"].get(as_of, [])}
        ref = {}
        if cs.get(as_of): ref["capital_structure"] = cs[as_of]
        if as_of == latest: ref["historical_cashflows"] = cf_all
        skipped = _push_date(as_of, ent, ref)
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
