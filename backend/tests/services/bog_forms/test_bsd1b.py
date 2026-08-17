"""BSD1B — Daily Net Open Position: line map, FX-run resolver, template arithmetic.

On the hermetic book, through the REAL package pipeline:

1. every data cell of ``FORM FXP``, ``AFOP`` and ``SCHEDULE B`` is bound (the
   FXP/AFOP grids are blank in the official workbook; SCHEDULE B's 12 ``0``
   placeholders + the blank EURO column);
2. without a succeeded FX run every ``bsd1b.nop`` cell is ``input_required``
   (nothing is invented);
3. after the FX baseline (``POST /fx/run-all-scenarios``) the per-currency NOP in
   units of currency equals the run's ``net_ccy``, net assets + net trading
   position = NOP (the engine's own decomposition), the cedi block equals
   ``net_ghs``, Σ signed cedi NOPs = sum_long − sum_short, AFOP = the run's
   ``nop_ghs`` and NOF = ``tier1_ghs``, and the template's own ``C38 = C36/C37``
   / ``B15 = B13/B14`` reproduce ``nop_pct_tier1``; the nature cells carry the
   template's ``( L )`` / ``( S )`` notation from the run's side;
4. SCHEDULE B's own SUM and IF long/short flag formulas evaluate over the (blank)
   contingents inputs.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import TenantContext
from app.db.session import get_sessionmaker
from app.models import Bank, BankReportingPeriod
from app.services.regulatory_reporting.bog_forms.layout import load_layout
from app.services.regulatory_reporting.bog_forms.linemaps import line_maps_for
from app.services.regulatory_reporting.bog_forms.sources import ResolveContext, get_resolver
from tests.api.helpers import ORG_1, headers
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

FXP = "FORM FXP"
AFOP = "AFOP"
SCHEDULE_B = "SCHEDULE B"
LONG = "( L )"
SHORT = "( S )"


def _materialize(db_client: TestClient) -> None:
    _ = db_client
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)
        session.commit()
    finally:
        session.close()


def _latest_period(db_client: TestClient) -> dict[str, Any]:
    periods = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/reporting-periods", headers=headers()
    ).json()["periods"]
    return periods[0]


def _run_fx_baseline(db_client: TestClient, period_id: str) -> dict[str, Any]:
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/fx/run-all-scenarios",
        headers=headers(),
        json={"reporting_period_id": period_id},
    )
    assert response.status_code == 201, response.text[:400]
    runs = response.json()["runs"]
    baseline = next(run for run in runs if run["scenario_code"] == "baseline")
    assert baseline["status"] == "succeeded"
    return baseline["metrics"]


def _generate(db_client: TestClient, reporting_date: str) -> dict[str, Any]:
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages",
        headers=headers(),
        json={"return_code": "BSD1B", "reporting_date": reporting_date},
    )
    assert response.status_code == 201, response.text[:400]
    detail = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{response.json()['id']}",
        headers=headers(),
    ).json()
    return detail["snapshot"]


def _lines(snapshot: dict[str, Any], sheet: str) -> dict[str, dict[str, Any]]:
    for section in snapshot["sections"]:
        if section["title"] == sheet:
            return {row["cell"]: row for row in section["rows"]}
    raise AssertionError(sheet)


def _cells(snapshot: dict[str, Any], sheet: str) -> dict[str, Any]:
    return snapshot["bog_form"]["cells"][sheet]


def _num(value: Any) -> Decimal:
    return Decimal(str(value or 0))


def _by_currency(metrics: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["currency"]: row for row in metrics["currencies"]}


# ---------------------------------------------------------------------------
# 1. structure
# ---------------------------------------------------------------------------


def test_bsd1b_line_map_binds_every_official_data_cell() -> None:
    layout = load_layout("BSD1B")
    maps = line_maps_for("BSD1B")
    # SCHEDULE B: the 12 captured placeholders + 3 blank EURO cells
    schedule_b = maps[SCHEDULE_B]
    bound = {ref for line in schedule_b for ref in line.cells.values()}
    assert bound >= {c.ref for c in layout.sheet(SCHEDULE_B).input_cells}
    assert bound == {f"{col}{row}" for row in (6, 7, 8) for col in "BCDEF"}
    assert all(line.source is None for line in schedule_b)  # contingents: research gap G5
    # FORM FXP: the currency-wise grid (rows 14/16/18/19 amount+nature, 22/24/26/27
    # amounts, 31–34 NOP+nature, 36/37) — 58 cells; every formula cell is left to BoG
    fxp = maps[FXP]
    fxp_cells = {ref for line in fxp for ref in line.cells.values()}
    assert len(fxp_cells) == 58  # noqa: PLR2004
    assert "C38" not in fxp_cells  # = C36/C37 is the template's own
    assert {f"C{r}" for r in (14, 18, 19, 22, 27, 31, 36, 37)} <= fxp_cells
    assert {line.source for line in fxp} == {"bsd1b.nop", None}
    unscaled_rows = {int(line.code.rsplit("R", 1)[1]) for line in fxp if line.unscaled}
    assert unscaled_rows == {14, 18, 19, 22, 27}  # units of currency / ¢'000 / rates
    # AFOP: 4 currency rows × (NOP, nature) + AFOP + NOF + regulatory limit
    afop = maps[AFOP]
    assert {ref for line in afop for ref in line.cells.values()} == {
        "B8", "C8", "B9", "C9", "B10", "C10", "B11", "C11", "B13", "B14", "B17",
    }  # fmt: skip
    assert all(line.source == "bsd1b.nop" for line in afop)


# ---------------------------------------------------------------------------
# 2. no FX run ⇒ nothing invented
# ---------------------------------------------------------------------------


def test_bsd1b_without_an_fx_run_every_nop_cell_is_input_required(db_client: TestClient) -> None:
    _materialize(db_client)
    period = _latest_period(db_client)
    snapshot = _generate(db_client, period["period_end"])
    assert not snapshot["bog_form"]["errors"]
    assert snapshot["bog_form"]["status_counts"]["mapped"] == 0
    fxp = _lines(snapshot, FXP)
    assert fxp["C19"]["status"] == "input_required"
    assert fxp["C19"]["source"] == "bsd1b.nop"
    assert _cells(snapshot, FXP).get("C36") is None
    # BoG's ratio over blanks: #DIV/0 → 0 (evaluator convention), never a fabricated %
    assert _num(_cells(snapshot, FXP)["C38"]) == 0


# ---------------------------------------------------------------------------
# 3. FX baseline run ⇒ NOP by currency, AFOP, NOF; template arithmetic
# ---------------------------------------------------------------------------


def test_bsd1b_reports_the_fx_runs_nop_and_bogs_ratios_hold(db_client: TestClient) -> None:
    _materialize(db_client)
    period = _latest_period(db_client)
    metrics = _run_fx_baseline(db_client, period["id"])
    by_ccy = _by_currency(metrics)
    snapshot = _generate(db_client, period["period_end"])
    assert not snapshot["bog_form"]["errors"], snapshot["bog_form"]["errors"]
    fxp = _cells(snapshot, FXP)
    fxp_lines = _lines(snapshot, FXP)

    # I) net assets + iii) net trading position = NOP (units of currency), per currency
    named = (("USD", "C", "D"), ("GBP", "E", "F"), ("EUR", "G", "H"))
    for currency, amount_col, nature_col in named:
        run = by_ccy[currency]
        nop_ccy = Decimal(run["net_ccy"])
        assert _num(fxp[f"{amount_col}19"]) == nop_ccy, currency
        assert _num(fxp[f"{amount_col}14"]) + _num(fxp[f"{amount_col}18"]) == nop_ccy, currency
        # nature = the run's side in the template's own notation
        assert fxp[f"{nature_col}19"] == (LONG if run["side"] == "long" else SHORT), currency
        assert fxp_lines[f"{nature_col}19"]["status"] == "mapped"
        # cedi equivalent (in 000) and the cedi NOP block
        net_ghs = Decimal(run["net_ghs"])
        assert _num(fxp[f"{amount_col}22"]) == net_ghs / 1000, currency
        assert _num(fxp[f"{amount_col}27"]) == Decimal(run["spot_ghs"]), currency
    assert by_ccy["USD"]["side"] == "long" and by_ccy["EUR"]["side"] == "short"  # noqa: PT018
    assert _num(fxp["C31"]) == Decimal(by_ccy["USD"]["net_ghs"])
    assert fxp["D31"] == LONG and fxp["D33"] == SHORT  # noqa: PT018
    other = sum(
        (Decimal(r["net_ghs"]) for ccy, r in by_ccy.items() if ccy not in {"USD", "GBP", "EUR"}),
        Decimal(0),
    )
    assert _num(fxp["C34"]) == other
    # 'Other Currencies' in units of currency is not a single unit ⇒ input_required
    assert fxp_lines["I19"]["status"] == "input_required"
    assert fxp_lines["I27"]["status"] == "input_required"
    # Σ signed cedi NOPs = sum_long − sum_short (the run's own legs)
    signed = sum((_num(fxp[f"C{r}"]) for r in (31, 32, 33, 34)), Decimal(0))
    assert signed == Decimal(metrics["sum_long_ghs"]) - Decimal(metrics["sum_short_ghs"])
    # AFOP / NOF and the template's own C38 = C36/C37 (= nop_pct_tier1 / 100)
    assert _num(fxp["C36"]) == Decimal(metrics["nop_ghs"])
    assert _num(fxp["C37"]) == Decimal(metrics["tier1_ghs"])
    assert abs(_num(fxp["C38"]) * 100 - Decimal(metrics["nop_pct_tier1"])) < Decimal("1e-4")
    # contingents / management limit / single-currency measure: honest blanks
    for cell in ("C16", "D16", "C24", "C26"):
        assert fxp_lines[cell]["status"] == "input_required", cell

    # ---- AFOP sheet mirrors the aggregate block, B15 = B13/B14 is BoG's -----------
    afop = _cells(snapshot, AFOP)
    assert _num(afop["B8"]) == Decimal(by_ccy["USD"]["net_ghs"])
    assert afop["C8"] == LONG and afop["C10"] == SHORT  # noqa: PT018
    assert _num(afop["B13"]) == Decimal(metrics["nop_ghs"])
    assert _num(afop["B14"]) == Decimal(metrics["tier1_ghs"])
    assert abs(_num(afop["B15"]) * 100 - Decimal(metrics["nop_pct_tier1"])) < Decimal("1e-4")
    assert _num(afop["B17"]) == Decimal(metrics["nop_aggregate_limit_pct"])
    # export units: AFOP sheet is cedis (units) — the section value is unscaled cedis;
    # FXP block 2 exports in ¢'Million; the ¢'000 row is unscaled by declaration
    afop_rows = _lines(snapshot, AFOP)
    assert Decimal(afop_rows["B13"]["value"]) == Decimal(metrics["nop_ghs"])
    assert Decimal(fxp_lines["C31"]["value"]) == Decimal(by_ccy["USD"]["net_ghs"]) / 1_000_000
    assert Decimal(fxp_lines["C22"]["value"]) == Decimal(by_ccy["USD"]["net_ghs"]) / 1000

    # ---- SCHEDULE B: the template's own SUM + long/short flag formulas ------------
    schedule_b = _cells(snapshot, SCHEDULE_B)
    schedule_lines = _lines(snapshot, SCHEDULE_B)
    assert all(
        schedule_lines[f"{c}{r}"]["status"] == "input_required" for r in (6, 7, 8) for c in "BCDEF"
    )
    assert _num(schedule_b["B10"]) == 0  # = SUM(B6:B8) over blanks
    assert schedule_b["B12"] == "-"  # = IF(B10<0,"( S )",IF(B10=0,"-","( L )"))


# ---------------------------------------------------------------------------
# 4. resolver unit test: long / short legs, aggregate measures, missing run
# ---------------------------------------------------------------------------


def test_bsd1b_nop_resolver_measures(db_client: TestClient) -> None:  # noqa: PLR0915
    _materialize(db_client)
    period = _latest_period(db_client)
    nop = get_resolver("bsd1b.nop")
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        bank = session.get(Bank, SAMPLE_BANK_ID)
        assert bank is not None
        period_row = session.scalar(
            select(BankReportingPeriod).where(BankReportingPeriod.id == UUID(period["id"]))
        )
        assert period_row is not None
        ctx = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)

        def rc(column: str) -> ResolveContext:
            return ResolveContext(db=session, ctx=ctx, bank=bank, period=period_row, column=column)

        # no run yet ⇒ None for every measure
        assert nop(rc("usd"), {"measure": "net"}) is None
        assert nop(rc("value"), {"measure": "afop"}) is None
    finally:
        session.close()

    metrics = _run_fx_baseline(db_client, period["id"])
    by_ccy = _by_currency(metrics)
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        bank = session.get(Bank, SAMPLE_BANK_ID)
        assert bank is not None
        period_row = session.scalar(
            select(BankReportingPeriod).where(BankReportingPeriod.id == UUID(period["id"]))
        )
        assert period_row is not None
        ctx = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)

        def rc(column: str) -> ResolveContext:
            return ResolveContext(db=session, ctx=ctx, bank=bank, period=period_row, column=column)

        usd = Decimal(by_ccy["USD"]["net_ghs"])
        eur = Decimal(by_ccy["EUR"]["net_ghs"])
        assert usd > 0 > eur
        # long / short legs in cedi: net = long − short per currency
        assert nop(rc("usd"), {"measure": "long"}) == usd
        assert nop(rc("usd"), {"measure": "short"}) == 0
        assert nop(rc("eur"), {"measure": "long"}) == 0
        assert nop(rc("eur"), {"measure": "short"}) == -eur
        assert nop(rc("eur"), {"measure": "net_ghs"}) == eur
        # explicit currency wins over the column key; nature flag from the sign
        assert nop(rc("nop"), {"measure": "net_ghs", "currency": "EUR"}) == eur
        assert nop(rc("nature"), {"measure": "net_ghs", "currency": "EUR"}) == SHORT
        assert nop(rc("usd_nature"), {"measure": "net"}) == LONG
        # aggregate legs reproduce the run
        assert nop(rc("value"), {"measure": "sum_long"}) == Decimal(metrics["sum_long_ghs"])
        assert nop(rc("value"), {"measure": "sum_short"}) == Decimal(metrics["sum_short_ghs"])
        assert nop(rc("value"), {"measure": "afop"}) == max(
            Decimal(metrics["sum_long_ghs"]), Decimal(metrics["sum_short_ghs"])
        )
        # units-of-currency measures come from the run's fx_position inputs
        assets = nop(rc("usd"), {"measure": "assets"})
        liabilities = nop(rc("usd"), {"measure": "liabilities"})
        derivatives = nop(rc("usd"), {"measure": "net_derivatives"})
        assert isinstance(assets, Decimal) and isinstance(liabilities, Decimal)  # noqa: PT018
        assert isinstance(derivatives, Decimal)
        assert nop(rc("usd"), {"measure": "net_assets"}) == assets - liabilities
        assert assets - liabilities + derivatives == Decimal(by_ccy["USD"]["net_ccy"])
        # 'Other' has no single unit
        assert nop(rc("other"), {"measure": "net"}) is None
        assert nop(rc("other"), {"measure": "spot"}) is None
        # a column that names no currency and no explicit currency ⇒ None
        assert nop(rc("value"), {"measure": "net"}) is None
    finally:
        session.close()
