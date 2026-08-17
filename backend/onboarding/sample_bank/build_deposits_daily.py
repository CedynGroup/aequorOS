"""Generate Sample Bank's DAILY DEPOSIT sub-ledger for the BSD1 week.

BSD1's deposit block (lines 1–10 of the form) reports the PREVIOUS week, which
``sources_ext/bsd1.py`` resolves as ``period_end - 7 … period_end - 13``. The
EOD ladder already loaded covers only the liquid-asset book — its own header
says "the deposit and loan sub-ledgers are not in this file" — so every deposit
row correctly reported ``input_required``. This builds that missing sub-ledger.

Categories are kept mutually exclusive so the template's own roll-ups do not
double count: ordinary deposits carry a ``deposit_account_type`` of CURRENT /
CALL / SAVINGS / FIXED, while certificates of deposit, special deposits and
margins carry OTHER plus an ``instrument`` attribute — which is exactly how the
line map distinguishes them.

Deterministic (seeded) so a re-run reproduces the same book.

    uv run python onboarding/sample_bank/build_deposits_daily.py
"""

from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).parent / "deposits_daily"
PERIOD_END = date(2026, 6, 30)
# BSD1 deposits = the previous week: period_end-13 … period_end-7
DAYS = [PERIOD_END - timedelta(days=offset) for offset in range(13, 6, -1)]

COLUMNS = [
    "source_reference",
    "position_type",
    "currency",
    "balance",
    "deposit_account_type",
    "counterparty_reference",
    "product_code",
    "gl_account_code",
    "origination_date",
    "contractual_maturity",
    "interest_rate",
    "rate_type",
    "attributes.balance_ghs",
    "attributes.instrument",
    "attributes.branch_id",
]

BRANCHES = [f"BR{n:03d}" for n in range(1, 29)]

# Sample Bank's OWN reference data — positions may only cite products, GL
# accounts and counterparties that already exist (the Data Engine rejects a
# position whose references it cannot resolve, which is how the first attempt
# at this file was caught: 823/823 structural_unresolved_references).
EXISTING_COUNTERPARTIES = 15_000  # SBL-CUST-000001 … SBL-CUST-015000

# Deposit products the bank does not yet carry. Pushed as PRODUCT entities in
# the same batch, so the positions that cite them resolve.
NEW_PRODUCTS = [
    ("DEP.CORP.CALL", "Corporate Call Account (GHS)"),
    ("DEP.GHS.CD", "Certificate of Deposit (GHS)"),
    ("DEP.GHS.SPECIAL", "Special Deposit (GHS)"),
    ("DEP.GHS.MARGIN", "Margin against Contingent Liabilities (GHS)"),
    ("DEP.USD.CUR", "USD Current Account"),
    ("DEP.FX.SPECIAL", "Special Deposit (foreign currency)"),
    ("DEP.FX.MARGIN", "Margin against Contingent Liabilities (foreign currency)"),
]

# (bucket, accounts, total GHS at the start of the week, account type,
#  instrument, currency, product, gl account, rate band)
BOOK = [
    ("current_ret", 150, 1_720_000_000, "CURRENT", "", "GHS", "DEP.RET.CUR", "2001", (0.0, 0.5)),
    ("current_corp", 90, 1_330_000_000, "CURRENT", "", "GHS", "DEP.CORP.CUR", "2004", (0.0, 0.8)),
    ("call", 90, 980_000_000, "CALL", "", "GHS", "DEP.CORP.CALL", "2004", (2.5, 6.0)),
    ("savings", 220, 2_410_000_000, "SAVINGS", "", "GHS", "DEP.RET.SAV", "2002", (4.0, 7.5)),
    ("time_ret", 80, 1_150_000_000, "FIXED", "", "GHS", "DEP.RET.TRM", "2003", (12.0, 19.0)),
    ("time_corp", 50, 1_030_000_000, "FIXED", "", "GHS", "DEP.CORP.TRM", "2005", (12.5, 20.0)),
    ("cd", 25, 452_000_000, "OTHER", "certificate_of_deposit", "GHS", "DEP.GHS.CD", "2003", (14.0, 20.0)),
    ("special", 18, 231_000_000, "OTHER", "special_deposit", "GHS", "DEP.GHS.SPECIAL", "2000", (0.0, 3.0)),
    ("margin", 14, 178_000_000, "OTHER", "margin_against_contingent", "GHS", "DEP.GHS.MARGIN", "2000", (0.0, 0.0)),
]

FX_BOOK = [
    ("fx_current", 44, 1_240_000_000, "CURRENT", "", "USD", "DEP.USD.CUR", "2006", (0.0, 1.5)),
    ("fx_savings", 16, 430_000_000, "SAVINGS", "", "USD", "DEP.USD.SAV", "2006", (0.5, 2.0)),
    ("fx_time", 12, 415_000_000, "FIXED", "", "USD", "DEP.USD.TRM", "2006", (2.0, 5.5)),
    ("fx_special", 8, 118_000_000, "OTHER", "special_deposit", "USD", "DEP.FX.SPECIAL", "2006", (0.0, 2.0)),
    ("fx_margin", 6, 94_000_000, "OTHER", "margin_against_contingent", "USD", "DEP.FX.MARGIN", "2006", (0.0, 0.0)),
]

# cedi per unit — the week's spot, held flat (BSD1 reports the cedi equivalent
# the bank booked; a moving rate belongs in the FX ladder, not here)
FX_RATE = {"USD": 15.42, "EUR": 16.78, "GBP": 19.55}


def _accounts(rng: random.Random) -> list[dict]:
    """One row per deposit account, with its share of the bucket's total."""
    rows: list[dict] = []
    for book, is_fx in ((BOOK, False), (FX_BOOK, True)):
        for (
            bucket,
            count,
            total,
            account_type,
            instrument,
            currency,
            product,
            gl,
            rate_band,
        ) in book:
            # a realistic deposit book is concentrated: a few large accounts
            weights = [rng.paretovariate(1.6) for _ in range(count)]
            scale = total / sum(weights)
            for index, weight in enumerate(weights, start=1):
                ghs = weight * scale
                opened = PERIOD_END - timedelta(days=rng.randint(45, 2900))
                matures = ""
                if account_type == "FIXED" or instrument == "certificate_of_deposit":
                    matures = (PERIOD_END + timedelta(days=rng.randint(20, 700))).isoformat()
                rows.append(
                    {
                        "bucket": bucket,
                        "source_reference": f"DEP-{bucket.upper()}-{index:04d}",
                        "position_type": "DEPOSIT",
                        "currency": currency,
                        "ghs": ghs,
                        "is_fx": is_fx,
                        "deposit_account_type": account_type,
                        # an EXISTING customer — never a fabricated counterparty
                        "counterparty_reference": (
                            f"SBL-CUST-{rng.randint(1, EXISTING_COUNTERPARTIES):06d}"
                        ),
                        "product_code": product,
                        "gl_account_code": gl,
                        "origination_date": opened.isoformat(),
                        "contractual_maturity": matures,
                        # the canonical contract carries rates as FRACTIONS
                        # (validation: interest_rate must sit in [0, 1]), so a
                        # 12.5% term deposit is stored as 0.125, not 12.5
                        "interest_rate": round(rng.uniform(*rate_band) / 100.0, 6),
                        "rate_type": "FIXED" if account_type == "FIXED" else "FLOATING",
                        "attributes.instrument": instrument,
                        "attributes.branch_id": rng.choice(BRANCHES),
                    }
                )
    return rows


def _write_products() -> Path:
    """The deposit products the bank does not yet carry, as a product file."""
    path = OUT / "new_deposit_products.csv"
    with path.open("w", newline="") as handle:
        handle.write(
            "# Deposit products Sample Bank did not yet carry. Pushed as PRODUCT entities "
            "before the daily deposit sub-ledger so those positions resolve their product "
            "reference; existing products (DEP.RET.CUR, DEP.RET.SAV, …) are reused as-is.\n"
        )
        writer = csv.DictWriter(handle, fieldnames=["source_reference", "product_code", "name"])
        writer.writeheader()
        for code, name in NEW_PRODUCTS:
            writer.writerow({"source_reference": code, "product_code": code, "name": name})
    print(f"{path.name}: {len(NEW_PRODUCTS)} products")
    return path


def main() -> None:
    rng = random.Random(20260630)
    accounts = _accounts(rng)
    OUT.mkdir(parents=True, exist_ok=True)
    _write_products()
    for day_index, day in enumerate(DAYS):
        path = OUT / f"deposits_{day.isoformat()}.csv"
        with path.open("w", newline="") as handle:
            handle.write(
                f"# Sample Bank DAILY DEPOSIT sub-ledger — close of business {day.isoformat()}; "
                f"push with as_of_date={day.isoformat()}. Feeds BSD1 deposit lines 1-10 "
                "(the form's PREVIOUS-week block). Liquid assets are in eod_ladder/.\n"
            )
            writer = csv.DictWriter(handle, fieldnames=COLUMNS)
            writer.writeheader()
            for account in accounts:
                # deposits drift day to day; weekends barely move
                drift = rng.gauss(0.0, 0.0016 if day.weekday() < 5 else 0.0004)
                ghs = account["ghs"] * (1.0 + drift) * (1.0 + 0.0004 * day_index)
                if account["is_fx"]:
                    rate = FX_RATE[account["currency"]]
                    native = ghs / rate
                    balance, balance_ghs = round(native, 2), round(ghs, 2)
                else:
                    balance, balance_ghs = round(ghs, 2), ""
                writer.writerow(
                    {
                        "source_reference": account["source_reference"],
                        "position_type": account["position_type"],
                        "currency": account["currency"],
                        "balance": balance,
                        "deposit_account_type": account["deposit_account_type"],
                        "counterparty_reference": account["counterparty_reference"],
                        "product_code": account["product_code"],
                        "gl_account_code": account["gl_account_code"],
                        "origination_date": account["origination_date"],
                        "contractual_maturity": account["contractual_maturity"],
                        "interest_rate": account["interest_rate"],
                        "rate_type": account["rate_type"],
                        "attributes.balance_ghs": balance_ghs,
                        "attributes.instrument": account["attributes.instrument"],
                        "attributes.branch_id": account["attributes.branch_id"],
                    }
                )
        print(f"{path.name}: {len(accounts)} accounts")


if __name__ == "__main__":
    main()
