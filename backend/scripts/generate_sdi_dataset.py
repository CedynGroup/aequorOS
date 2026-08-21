"""Generate a rich, 10-year, multi-cadence synthetic dataset for a Ghanaian
savings-&-loans (SDI) tenant, in the Data Engine's CSV / API-push format.

A *proper* time series, not one-off snapshots: each account keeps a STABLE
``source_reference`` and is valued on a continuous daily balance model, so the
same facility/account is tracked across every reporting date and the platform
reconciles daily → weekly → monthly exactly as a real core-banking feed would.

Cadence (real institutions have fine-grained recent data + coarser history):
  * MONTHLY  month-end snapshots for the full 10 years  (the BSD reporting spine)
  * WEEKLY   Friday-close snapshots for the recent years (the LMTD weekly return)
  * DAILY    every business day for the most recent window (live view + daily recon)
Each regulatory return draws the right period-end; the live engine builds the EOD
ladder from the daily pushes. Daily cash-flow transactions span the full 10 years.

    uv run python scripts/generate_sdi_dataset.py            # 10y monthly + 5y weekly + 2y daily
    uv run python scripts/generate_sdi_dataset.py --daily-days 3650 --weekly-years 10   # full daily

Output (per-book files carry an ``as_of_date`` column — a handful of files, not
thousands; ``push_sdi.py`` groups by date and runs the three-call flow per date):
    onboarding/aequoros_sdi/
      counterparties.csv products.csv behavioral_assumptions.csv
      positions_deposits.csv positions_loans.csv positions_cash.csv positions_securities.csv
      gl_accounts.csv capital_structure.csv daily_cashflows.csv
      as_of_calendar.csv  push_sdi.py  README.md
"""

from __future__ import annotations

import argparse
import csv
import math
import random
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

RNG = random.Random(20260821)
GHS = "GHS"
BRANCHES = ["ACC-HQ", "ACC-MADINA", "KUM-CENTRAL", "TAK-MARKET", "TEM-COMM7", "TAM-NORTH"]


# --- calendar ----------------------------------------------------------------
def _eom(d: date) -> date:
    nxt = date(d.year + (d.month // 12), (d.month % 12) + 1, 1)
    return nxt - timedelta(days=1)


def _month_ends(start: date, end: date) -> list[date]:
    out, cur = [], _eom(date(start.year, start.month, 1))
    while cur <= end:
        out.append(cur)
        after = cur + timedelta(days=1)
        cur = _eom(date(after.year, after.month, 1))
    return out


def _fridays(start: date, end: date) -> list[date]:
    d, out = start + timedelta(days=(4 - start.weekday()) % 7), []
    while d <= end:
        out.append(d)
        d += timedelta(days=7)
    return out


def _business_days(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def build_calendar(end: date, years: int, weekly_years: int, daily_days: int) -> list[date]:
    start = date(end.year - years, end.month, 1)
    dates = set(_month_ends(start, end))
    dates |= set(_fridays(date(end.year - weekly_years, end.month, min(end.day, 28)), end))
    dates |= set(_business_days(end - timedelta(days=daily_days), end))
    return sorted(d for d in dates if start <= d <= end)


# --- products / segments -----------------------------------------------------
PRODUCTS = [
    ("DEP-SAV", "Ordinary Savings", "DEPOSIT_SAVINGS", "SAVINGS"),
    ("DEP-CUR", "Current Account", "DEPOSIT_CURRENT", "CURRENT"),
    ("DEP-CALL", "Call Deposit", "DEPOSIT_CALL", "CALL"),
    ("DEP-FIX", "Fixed / Time Deposit", "DEPOSIT_TIME", "FIXED"),
    ("DEP-SUSU", "Susu Collection", "DEPOSIT_SAVINGS", "SAVINGS"),
    ("LN-SME", "SME Term Loan", "LOAN_SME", None),
    ("LN-CONS", "Consumer Loan", "LOAN_RETAIL", None),
    ("LN-SAL", "Salary-Backed Loan", "LOAN_RETAIL", None),
    ("LN-MICRO", "Group Microfinance", "LOAN_RETAIL", None),
    ("LN-MORT", "Home Improvement / Mortgage", "LOAN_MORTGAGE", None),
    ("SEC-TBILL", "GoG Treasury Bill", "SOVEREIGN_TBILL", None),
    ("SEC-BOND", "GoG Note / Bond", "SOVEREIGN_BOND", None),
    ("CASH-VAULT", "Vault Cash", "CASH", None),
    ("CASH-BOG", "Balances with Bank of Ghana", "CASH_CENTRAL_BANK", None),
]
DEP_TYPE = {p[0]: p[3] for p in PRODUCTS}

# code, count, avg balance, monthly growth band, deposit rate
DEPOSIT_SEGMENTS = [
    ("DEP-SAV", 250, 90_000, (0.002, 0.006), 0.09),
    ("DEP-SUSU", 120, 28_000, (0.002, 0.007), 0.08),
    ("DEP-CUR", 100, 330_000, (0.001, 0.005), 0.0),
    ("DEP-CALL", 50, 680_000, (0.001, 0.005), 0.11),
    ("DEP-FIX", 80, 1_150_000, (0.002, 0.006), 0.19),
]
# code, count, avg ticket, rate, npl propensity, term months
LOAN_SEGMENTS = [
    ("LN-SME", 80, 1_350_000, 0.30, 0.16, 36),
    ("LN-CONS", 120, 210_000, 0.34, 0.15, 24),
    ("LN-SAL", 90, 195_000, 0.28, 0.06, 30),
    ("LN-MICRO", 100, 45_000, 0.42, 0.20, 12),
    ("LN-MORT", 25, 1_150_000, 0.26, 0.08, 60),
]
SECTORS = {
    "LN-SME": ["commerce.import.other", "manufacturing.home.food_drink_tobacco",
               "construction.building_construction", "services.business",
               "agriculture.poultry_farming"],
    "LN-CONS": ["services.personal", "commerce.other", "services.recreation"],
    "LN-SAL": ["services.salary_credit", "services.other_incl_government"],
    "LN-MICRO": ["commerce.other", "agriculture.other", "services.personal"],
    "LN-MORT": ["commerce.mortgage_financing", "construction.building_construction"],
}
_FIRST = ["Kwame", "Ama", "Kofi", "Akosua", "Yaw", "Abena", "Kojo", "Adwoa", "Kwesi", "Efua",
          "Kwabena", "Akua", "Fiifi", "Esi", "Nana", "Afia", "Yaa", "Aba"]
_LAST = ["Mensah", "Owusu", "Boateng", "Asante", "Agyeman", "Adjei", "Darko", "Osei", "Appiah",
         "Yeboah", "Ofori", "Antwi", "Frimpong", "Baffour", "Dartey", "Quaye"]
_BIZ = ["Ventures", "Enterprise", "Trading", "Logistics", "Foods", "Agro", "Fabrics", "Hardware",
        "Pharmacy", "Motors", "Farms", "Cold Store", "Printing", "Tailoring"]


@dataclass
class Party:
    ref: str
    name: str
    ctype: str


@dataclass
class DepositAcct:
    ref: str
    product: str
    acct_type: str
    depositor: Party
    branch: str
    open_date: date
    base: float
    growth: float
    rate: float
    tenor_months: int | None
    phase: float


@dataclass
class LoanFacility:
    ref: str
    product: str
    borrower: Party
    branch: str
    sector: str
    open_date: date
    principal: float
    rate: float
    delinquency_start: date | None
    dpd_per_day: float


def _seeded(ref: str, d: date, lo: float, hi: float) -> float:
    h = hash((ref, d.toordinal())) & 0xFFFFFFFF
    return lo + (h / 0xFFFFFFFF) * (hi - lo)


def _seasonal(d: date) -> float:
    f = 1.0
    if d.month in (3, 6, 9, 12):
        f *= 1.02
    if d.month == 12:
        f *= 0.985
    return f


# --- rosters -----------------------------------------------------------------
def build_rosters(start: date, end: date):
    depositors: dict[str, Party] = {}
    borrowers: dict[str, Party] = {}
    deposits: list[DepositAcct] = []
    loans: list[LoanFacility] = []
    span = (end - start).days

    def person():
        return f"{RNG.choice(_FIRST)} {RNG.choice(_LAST)}"

    def biz():
        return f"{RNG.choice(_LAST)} {RNG.choice(_BIZ)}"

    d_seq = 0
    for code, count, avg, band, rate in DEPOSIT_SEGMENTS:
        for _ in range(count):
            d_seq += 1
            is_biz = code in ("DEP-CUR", "DEP-CALL") and RNG.random() < 0.6
            is_biz = is_biz or (code == "DEP-FIX" and RNG.random() < 0.4)
            ctype, name = ("SME", biz()) if is_biz else ("RETAIL_INDIVIDUAL", person())
            dref = f"CUST-D-{d_seq:04d}"
            depositors[dref] = Party(dref, name, ctype)
            opened = (start - timedelta(days=RNG.randint(0, 900)) if RNG.random() < 0.55
                      else start + timedelta(days=RNG.randint(0, max(60, span - 60))))
            deposits.append(DepositAcct(
                ref=f"DEP-{d_seq:04d}", product=code, acct_type=DEP_TYPE[code],
                depositor=depositors[dref], branch=RNG.choice(BRANCHES), open_date=opened,
                base=avg * RNG.uniform(0.25, 3.2), growth=RNG.uniform(*band), rate=rate,
                tenor_months=RNG.choice([3, 6, 9, 12]) if DEP_TYPE[code] == "FIXED" else None,
                phase=RNG.uniform(0, 6.28)))

    l_seq = 0
    for code, count, avg, rate, npl, _term in LOAN_SEGMENTS:
        for _ in range(count):
            l_seq += 1
            ctype = ("SME" if code == "LN-SME" or (code == "LN-MICRO" and RNG.random() < 0.3)
                     else "RETAIL_INDIVIDUAL")
            name = biz() if ctype == "SME" else person()
            bref = f"CUST-L-{l_seq:04d}"
            borrowers[bref] = Party(bref, name, ctype)
            opened = (start - timedelta(days=RNG.randint(0, 700)) if RNG.random() < 0.6
                      else start + timedelta(days=RNG.randint(0, max(120, span - 120))))
            delinq, dpd_per_day = None, 0.0
            if RNG.random() < npl:
                delinq = opened + timedelta(days=RNG.randint(150, max(200, span - 60)))
                dpd_per_day = RNG.uniform(0.85, 1.05)
            loans.append(LoanFacility(
                ref=f"LN-{l_seq:04d}", product=code, borrower=borrowers[bref],
                branch=RNG.choice(BRANCHES), sector=RNG.choice(SECTORS[code]), open_date=opened,
                principal=avg * RNG.uniform(0.35, 2.6), rate=rate * RNG.uniform(0.92, 1.08),
                delinquency_start=delinq, dpd_per_day=dpd_per_day))
    return deposits, loans, depositors, borrowers


# --- daily valuation (deterministic function of account + date) --------------
def deposit_balance(a: DepositAcct, d: date) -> float | None:
    if d < a.open_date:
        return None
    months = (d - a.open_date).days / 30.44
    val = a.base * ((1.0 + a.growth) ** months) * _seasonal(d)
    val *= 1.0 + 0.02 * math.sin((d.toordinal() / 7.0) + a.phase)
    return max(0.0, val * _seeded(a.ref, d, 0.99, 1.01))


def loan_state(ln: LoanFacility, d: date):
    if d < ln.open_date:
        return None
    months = (d - ln.open_date).days / 30.44
    out = ln.principal * (1.005**months) * _seeded(ln.ref, d, 0.97, 1.03)
    dpd = 0
    if ln.delinquency_start is not None and d >= ln.delinquency_start:
        dpd = int((d - ln.delinquency_start).days * ln.dpd_per_day)
        out *= _seeded(ln.ref, d, 1.0, 1.05)
    if dpd >= 360:
        grade, prov = "loss", 1.00
    elif dpd >= 180:
        grade, prov = "doubtful", 0.50
    elif dpd >= 90:
        grade, prov = "substandard", 0.20
    else:
        grade, prov = "standard", 0.0
    return out, dpd, grade, out * prov


STAGE = {"standard": 1, "substandard": 2, "doubtful": 3, "loss": 3}


def _m(x: float) -> str:
    return f"{max(0.0, x):.2f}"


def _s(x: float) -> str:
    return f"{x:.2f}"


# --- writers -----------------------------------------------------------------
def _open(path: Path, header: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("w", newline="")
    w = csv.writer(fh)
    w.writerow(header)
    return fh, w


def write_static(out: Path, depositors, borrowers) -> None:
    fh, w = _open(out / "counterparties.csv",
                  ["source_reference", "name", "counterparty_type", "country_code",
                   "resident", "attributes.segment"])
    for c in list(depositors.values()) + list(borrowers.values()):
        w.writerow([c.ref, c.name, c.ctype, "GH", "true", c.ctype.lower()])
    fh.close()
    fh, w = _open(out / "products.csv",
                  ["source_reference", "product_code", "name", "regulatory_category"])
    for code, name, reg, _ in PRODUCTS:
        w.writerow([code, code, name, reg])
    fh.close()
    fh, w = _open(out / "behavioral_assumptions.csv", ["product_code", "assumption", "value"])
    for row in [["DEP-SAV", "DEPOSIT_STABILITY", "0.82"], ["DEP-SAV", "NMD_DURATION", "30"],
                ["DEP-SUSU", "DEPOSIT_STABILITY", "0.75"], ["DEP-SUSU", "NMD_DURATION", "18"],
                ["DEP-CUR", "DEPOSIT_STABILITY", "0.55"], ["DEP-CUR", "NMD_DURATION", "12"],
                ["DEP-CALL", "DEPOSIT_STABILITY", "0.40"], ["DEP-CALL", "NMD_DURATION", "3"],
                ["LN-SME", "PREPAYMENT_RATE", "0.10"], ["LN-CONS", "PREPAYMENT_RATE", "0.08"],
                ["LN-SAL", "PREPAYMENT_RATE", "0.05"], ["LN-MORT", "PREPAYMENT_RATE", "0.04"]]:
        w.writerow(row)
    fh.close()


def generate(out: Path, calendar: list[date], deposits, loans) -> dict:  # noqa: PLR0912, PLR0915
    """Write every per-book time-series file in one streaming pass over the calendar."""
    dep_fh, dep_w = _open(out / "positions_deposits.csv",
                          ["as_of_date", "source_reference", "position_type", "currency", "balance",
                           "counterparty_reference", "product_code", "gl_account_code",
                           "contractual_maturity", "interest_rate", "rate_type",
                           "deposit_account_type", "attributes.balance_ghs",
                           "attributes.branch_id", "attributes.tenor_months"])
    ln_fh, ln_w = _open(out / "positions_loans.csv",
                        ["as_of_date", "source_reference", "position_type", "currency", "balance",
                         "counterparty_reference", "product_code", "gl_account_code",
                         "interest_rate", "rate_type", "ifrs9_stage", "attributes.balance_ghs",
                         "attributes.branch_id", "attributes.sector", "attributes.borrower_class",
                         "attributes.bog_classification", "attributes.days_past_due",
                         "attributes.ecl_provision_ghs"])
    cash_fh, cash_w = _open(out / "positions_cash.csv",
                            ["as_of_date", "source_reference", "position_type", "currency",
                             "balance", "counterparty_reference", "product_code", "gl_account_code",
                             "attributes.balance_ghs", "attributes.branch_id"])
    sec_fh, sec_w = _open(out / "positions_securities.csv",
                          ["as_of_date", "source_reference", "position_type", "currency", "balance",
                           "counterparty_reference", "product_code", "gl_account_code",
                           "contractual_maturity", "interest_rate", "attributes.balance_ghs",
                           "attributes.redeemable_within_two_days"])
    gl_fh, gl_w = _open(out / "gl_accounts.csv",
                        ["as_of_date", "source_reference", "account_code", "name",
                         "account_class", "currency", "balance"])
    cs_fh, cs_w = _open(out / "capital_structure.csv",
                        ["as_of_date", "capital_component", "amount_ghs", "tier"])
    cal_fh, cal_w = _open(out / "as_of_calendar.csv", ["as_of_date", "cadence"])

    month_ends = set(_month_ends(calendar[0], calendar[-1]))
    trend = []
    for d in calendar:
        iso = d.isoformat()
        cad = "monthly" if d in month_ends else ("weekly" if d.weekday() == 4 else "daily")
        cal_w.writerow([iso, cad])

        dep_total = 0.0
        for a in deposits:
            bal = deposit_balance(a, d)
            if bal is None:
                continue
            dep_total += bal
            maturity = ""
            if a.acct_type == "FIXED" and a.tenor_months:
                maturity = (d + timedelta(days=int(a.tenor_months * 30.44))).isoformat()
            dep_w.writerow([iso, a.ref, "DEPOSIT", GHS, _m(bal), a.depositor.ref, a.product,
                            "GL-2100", maturity, f"{a.rate:.4f}",
                            "FIXED" if a.acct_type == "FIXED" else "FLOATING", a.acct_type,
                            _m(bal), a.branch, a.tenor_months or ""])

        loan_total = prov_total = npl_total = 0.0
        for ln in loans:
            st = loan_state(ln, d)
            if st is None:
                continue
            out_bal, dpd, grade, ecl = st
            loan_total += out_bal
            prov_total += ecl
            if dpd >= 90:
                npl_total += out_bal
            bclass = ("household" if ln.borrower.ctype == "RETAIL_INDIVIDUAL"
                      else "private_indigenous")
            ln_w.writerow([iso, ln.ref, "LOAN", GHS, _m(out_bal), ln.borrower.ref, ln.product,
                           "GL-1300", f"{ln.rate:.4f}", "FIXED", STAGE[grade], _m(out_bal),
                           ln.branch, ln.sector, bclass, grade, dpd, _m(ecl)])

        vault = dep_total * 0.04 * _seeded("vault", d, 0.97, 1.03)
        bog = dep_total * 0.09 * _seeded("bog", d, 0.97, 1.03)
        cash_w.writerow([iso, "CASH-VAULT", "CASH", GHS, _m(vault), "", "CASH-VAULT", "GL-1010",
                         _m(vault), "ACC-HQ"])
        cash_w.writerow([iso, "CASH-BOG", "CASH", GHS, _m(bog), "CP-BOG", "CASH-BOG", "GL-1020",
                         _m(bog), "ACC-HQ"])

        sec_total = dep_total * 0.19 * _seeded("sec", d, 0.97, 1.03)
        weights = [_seeded(f"secw{i}", d, 0.6, 1.5) for i in range(10)]
        scale = sec_total / sum(weights)
        for i, wt in enumerate(weights):
            is_bill = i < 6
            code = "SEC-TBILL" if is_bill else "SEC-BOND"
            tenor = RNG.choice([91, 182, 364]) if is_bill else RNG.choice([730, 1095, 1825])
            sec_w.writerow([iso, f"{code}-{i:02d}", "SECURITY_HOLDING", GHS, _m(wt * scale),
                            "CP-GOG", code, "GL-1200",
                            (d + timedelta(days=tenor)).isoformat(),
                            f"{_seeded('secr', d, 0.20, 0.28):.4f}", _m(wt * scale), "true"])

        # GL + capital (identity closes; other assets is the plug); only on reporting
        # dates (month-ends + Fridays) — daily GL reconciliation rides the positions.
        if cad in ("monthly", "weekly"):
            months_since = (d - calendar[0]).days / 30.44
            paid_up = 22_000_000.0
            statutory = 8_000_000.0 + months_since * 180_000.0
            retained = 6_500_000.0 + months_since * 210_000.0
            equity = paid_up + statutory + retained
            net_loans = loan_total - prov_total
            hard = vault + bog + sec_total + net_loans
            borrowings = dep_total * 0.03
            other = max(dep_total * 0.02, dep_total + borrowings + equity - hard)
            for code, name, cls, bal in [
                ("GL-1010", "Cash on hand (vault)", "ASSET", vault),
                ("GL-1020", "Balances with Bank of Ghana", "ASSET", bog),
                ("GL-1200", "Investments — GoG securities", "ASSET", sec_total),
                ("GL-1300", "Loans & advances (gross)", "ASSET", loan_total),
                ("GL-1390", "Less: impairment allowance", "ASSET", -prov_total),
                ("GL-1900", "Other assets", "ASSET", other),
                ("GL-2100", "Customer deposits", "LIABILITY", dep_total),
                ("GL-2400", "Borrowings", "LIABILITY", borrowings),
                ("GL-3100", "Stated / paid-up capital", "EQUITY", paid_up),
                ("GL-3200", "Statutory reserve fund", "EQUITY", statutory),
                ("GL-3300", "Retained earnings", "EQUITY", retained),
            ]:
                gl_w.writerow([iso, code, code, name, cls, GHS, _s(bal)])
            for comp, amt, tier in [
                ("paid_up_capital", paid_up, "CET1"), ("statutory_reserves", statutory, "CET1"),
                ("retained_earnings", retained, "CET1"),
                ("credit_risk_reserve", prov_total * 0.15, "CET1"),
                ("intangible_assets", -1_200_000.0, "CET1_DEDUCTION"),
            ]:
                cs_w.writerow([iso, comp, _s(amt), tier])

        if d in month_ends:
            trend.append((iso, dep_total, loan_total, npl_total, prov_total))

    for fh in (dep_fh, ln_fh, cash_fh, sec_fh, gl_fh, cs_fh, cal_fh):
        fh.close()

    # daily cash-flows for the full span (the 90-day view + reconciliation)
    hcf_fh, hcf_w = _open(out / "daily_cashflows.csv",
                          ["date", "deposit_inflow_ghs", "deposit_outflow_ghs",
                           "net_cashflow_ghs"])
    d = calendar[0]
    while d <= calendar[-1]:
        if d.weekday() < 6:
            base = 7_500_000.0 * _seeded("cf", d, 0.7, 1.4)
            inflow = base * _seeded("in", d, 0.85, 1.2)
            outflow = base * _seeded("out", d, 0.8, 1.15)
            hcf_w.writerow([d.isoformat(), _m(inflow), _m(outflow), _s(inflow - outflow)])
        d += timedelta(days=1)
    hcf_fh.close()
    return {"trend": trend, "n_dates": len(calendar)}


_PUSH_SDI = '''#!/usr/bin/env python3
"""Push the per-book AequorOS SDI time-series files, grouped by as_of_date, through
the Data Engine API (three-call flow per date, docs/API_INTEGRATION.md §2).

    BASE_URL=http://localhost:8001 TOKEN=<admin token or aeq_live_...> BANK=BK-XREAZES1 \\\\
        python push_sdi.py --cadence monthly   # start light; then weekly, then daily, then all
"""
from __future__ import annotations
import argparse, csv, os, sys, urllib.request, json
from collections import defaultdict
from pathlib import Path

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

def _call(method, path, body=None):
    req = urllib.request.Request(f"{BASE}{path}", method=method, headers=HDR,
        data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req) as r: return json.load(r) if r.read else {}

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
'''


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="onboarding/aequoros_sdi")
    ap.add_argument("--bank", default="BK-XREAZES1")
    ap.add_argument("--end", default="2026-06-30")
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--weekly-years", type=int, default=5)
    ap.add_argument("--daily-days", type=int, default=730)
    args = ap.parse_args()

    out = Path(args.out)
    end = date.fromisoformat(args.end)
    calendar = build_calendar(end, args.years, args.weekly_years, args.daily_days)
    start = calendar[0]
    deposits, loans, depositors, borrowers = build_rosters(start, end)
    depositors["CP-BOG"] = Party("CP-BOG", "Bank of Ghana", "CENTRAL_BANK")
    depositors["CP-GOG"] = Party("CP-GOG", "Government of Ghana", "SOVEREIGN")

    write_static(out, depositors, borrowers)
    result = generate(out, calendar, deposits, loans)
    (out / "push_sdi.py").write_text(_PUSH_SDI)
    (out / "push_sdi.py").chmod(0o755)

    tr = result["trend"]
    first_m, last_m = tr[0], tr[-1]
    readme = [
        "# AequorOS SDI — 10-year multi-cadence ingestion dataset",
        "",
        f"A Ghanaian savings-&-loans as a continuous time series, **{start} → {end}** "
        f"({args.years}y monthly · {args.weekly_years}y weekly · {args.daily_days}d daily = "
        f"{result['n_dates']} reporting dates). Each account keeps a stable "
        "`source_reference`, valued daily — so the platform reconciles daily → weekly → monthly.",
        "",
        "## Load it",
        "Per-book files carry an `as_of_date` column. `push_sdi.py` groups by date and runs the",
        "three-call flow per date — start light, then widen:",
        "```bash",
        f"BASE_URL=http://localhost:8001 TOKEN=<admin or aeq_live_… key> BANK={args.bank} \\",
        "  python onboarding/aequoros_sdi/push_sdi.py --cadence monthly   # 10y month-ends",
        "#                                            --cadence weekly    # + Friday LMTD closes",
        "#                                            --cadence daily     # + recent daily EOD",
        "```",
        "",
        "## Trajectory (month-ends)",
        f"- {first_m[0]}: deposits GHS {first_m[1]:,.0f} · loans GHS {first_m[2]:,.0f} · "
        f"NPL {(first_m[3]/first_m[2]*100 if first_m[2] else 0):.1f}%",
        f"- {last_m[0]}: deposits GHS {last_m[1]:,.0f} · loans GHS {last_m[2]:,.0f} · "
        f"NPL {(last_m[3]/last_m[2]*100 if last_m[2] else 0):.1f}%",
        "",
        "## Files",
        "`positions_{deposits,loans,cash,securities}.csv` `gl_accounts.csv` "
        "`capital_structure.csv` `daily_cashflows.csv` (all with `as_of_date`) + "
        "`counterparties.csv` `products.csv` `behavioral_assumptions.csv` + `as_of_calendar.csv`.",
    ]
    (out / "README.md").write_text("\n".join(readme) + "\n")

    print(f"Generated {result['n_dates']} reporting dates ({start} → {end}) into {out}/")
    print(f"  {first_m[0]}: dep GHS {first_m[1]/1e6:.0f}M loans GHS {first_m[2]/1e6:.0f}M")
    print(f"  {last_m[0]}: dep GHS {last_m[1]/1e6:.0f}M loans GHS {last_m[2]/1e6:.0f}M "
          f"NPL {(last_m[3]/last_m[2]*100 if last_m[2] else 0):.1f}%")


if __name__ == "__main__":
    main()
