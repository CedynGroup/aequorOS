"""BSD1B — Daily Net Open Position (weekly return of the daily NOP report).

Official layout: ``FORM FXP`` (currency-wise positions — US Dollar / GBP / EURO
/ Other Currencies, each an Amount + "(Nature of Position)" pair; the position
components in UNITS OF CURRENCY, the cedi equivalent in ¢'000, revaluation
rates, then a per-currency NOP block and AFOP / NOF in cedis with ``C38 =
C36/C37`` the template's own AFOP-as-%-of-NOF), ``AFOP`` (the aggregate block
again, "Amounts in cedi equiv.", ``B15 = B13/B14``) and ``SCHEDULE B``
(crystallised liabilities under contingents by currency; ``B10…F10 = SUM`` and
the ``IF(...,"( S )","( L )")`` long/short flags are template formulas). The
official workbook ships its data cells BLANK (only SCHEDULE B carries ``0``
placeholders), so the FXP / AFOP grids are bound explicitly. Line/cell map:
docs/bog_returns/bsd1b_line_map.md.

Source: ``bsd1b.nop`` — the latest succeeded baseline FX run (the engine DBK-DAILY
reconstructs the daily NOP from) plus its ``fx_position`` inputs. Contingents
(FXP ii / SCHEDULE B) are ``input_required``: the FX run carries no
crystallised-contingent data (research gap G5, as DBK 102).
"""

from __future__ import annotations

from typing import Any

from ..spec import LineSpec
from ._common import INPUT_REQUIRED, RowSource, grid_lines, leaf_lines

FXP = "FORM FXP"
AFOP = "AFOP"
SCHEDULE_B = "SCHEDULE B"

#: FORM FXP currency columns: Amount / (Nature of Position) pairs.
FXP_PAIRS = {
    "usd": "C",
    "usd_nature": "D",
    "gbp": "E",
    "gbp_nature": "F",
    "eur": "G",
    "eur_nature": "H",
    "other": "I",
    "other_nature": "J",
}
FXP_AMOUNTS = {"usd": "C", "gbp": "E", "eur": "G", "other": "I"}

_NO_RUN = "; no succeeded FX baseline run for the period ⇒ input_required (run the FX engine)"
_UNITS = "units of currency (unscaled)"
_CONTINGENT_NOTE = (
    "crystallised liabilities under contingents by currency — the FX run and the canonical "
    "LC_GUARANTEE positions carry notionals but no crystallisation flag or LC/guarantee/"
    "other split (research gap G5, as DBK 102); bank must supply"
)


def nop(measure: str, notes: str, *, unscaled: bool = False, **params: Any) -> RowSource:
    return RowSource(
        "bsd1b.nop", {"measure": measure, **params}, notes=notes + _NO_RUN, unscaled=unscaled
    )


_FXP_COMPONENTS: dict[int, RowSource] = {
    14: nop(
        "net_assets",
        f"assets_ccy − liabilities_ccy of the run's fx_position input for the currency, {_UNITS}; "
        "'Other Currencies' mixes units ⇒ input_required",
        unscaled=True,
    ),
    16: RowSource(None, notes=_CONTINGENT_NOTE),
    18: nop(
        "net_derivatives",
        f"net_derivatives_ccy (signed FX_HEDGE deltas) of the run's fx_position input, {_UNITS}",
        unscaled=True,
    ),
    19: nop(
        "net",
        f"NOP = net_ccy of the FX run's per-currency result (assets − liabilities + derivatives), "
        f"{_UNITS}; nature column = the run's side as ( L ) / ( S )",
        unscaled=True,
    ),
}
_FXP_CEDI: dict[int, RowSource] = {
    22: nop(
        "net_ghs_thousands",
        "cedi equivalent of the currency's NOP (net_ghs) in ¢'000 as the label states; 'Other "
        "Currencies' = Σ signed net_ghs of every run currency other than USD/GBP/EUR",
        unscaled=True,
    ),
    24: RowSource(
        None,
        notes=(
            "'Single curr. Open Position' — the template does not state the unit (¢ or % of "
            "NOF); the FX run's |NOP| as % of NOF (abs_pct_tier1) is available once BoG "
            "confirms the basis; bank must supply meanwhile"
        ),
    ),
    26: RowSource(
        None,
        notes="management (internal) limit on the currency's NOP — bank policy, not platform state",
    ),
    27: nop(
        "spot",
        "revaluation rate = the run's spot_ghs (cedis per 1 unit) for the currency; not a single "
        "pair for 'Other Currencies' ⇒ input_required",
        unscaled=True,
    ),
}
_FXP_BLOCK2: dict[int, RowSource] = {
    31: nop("net_ghs", "NOP in cedi (base units → ¢'Million) for USD", currency="USD"),
    32: nop("net_ghs", "NOP in cedi for GBP", currency="GBP"),
    33: nop("net_ghs", "NOP in cedi for EUR", currency="EUR"),
    34: nop(
        "net_ghs",
        "NOP in cedi for other currencies = Σ signed net_ghs of run currencies not USD/GBP/EUR",
        currency="OTHER",
    ),
}
_FXP_AGGREGATE: dict[int, RowSource] = {
    36: nop("afop", "AFOP = the run's nop_ghs (aggregate NOP, as DBK-DAILY reports it)"),
    37: nop("nof", "Net own funds = the run's tier1_ghs (Tier 1 proxy for NOF, as DBK-DAILY)"),
}
_AFOP_CURRENCIES: dict[int, RowSource] = {
    8: nop("net_ghs", "NOP in cedi for USD (sheet unit: cedis)", currency="USD"),
    9: nop("net_ghs", "NOP in cedi for GBP", currency="GBP"),
    10: nop("net_ghs", "NOP in cedi for EUR", currency="EUR"),
    11: nop("net_ghs", "NOP in cedi for other currencies (Σ signed net_ghs)", currency="OTHER"),
}
_AFOP_AGGREGATE: dict[int, RowSource] = {
    13: nop("afop", "AFOP = the run's nop_ghs"),
    14: nop("nof", "Net own funds = the run's tier1_ghs (Tier 1 proxy, as DBK-DAILY)"),
    17: nop(
        "afop_limit_pct",
        "regulatory limit on AFOP = the run's nop_aggregate_limit_pct (% of NOF)",
        unscaled=True,
    ),
}


def _fxp(
    rows: tuple[int, ...], columns: dict[str, str], sources: dict[int, RowSource]
) -> tuple[LineSpec, ...]:
    return grid_lines(
        "BSD1B",
        FXP,
        rows=rows,
        value_columns=columns,
        row_sources=sources,
        code_prefix="BSD1B.FXP",
        default=INPUT_REQUIRED,
        label_column="B",
    )


LINES = {
    FXP: (
        *_fxp((14, 16, 18, 19), FXP_PAIRS, _FXP_COMPONENTS),
        *_fxp((22, 24, 26, 27), FXP_AMOUNTS, _FXP_CEDI),
        *_fxp((31, 32, 33, 34), {"nop": "C", "nature": "D"}, _FXP_BLOCK2),
        *_fxp((36, 37), {"value": "C"}, _FXP_AGGREGATE),
    ),
    AFOP: (
        *grid_lines(
            "BSD1B",
            AFOP,
            rows=(8, 9, 10, 11),
            value_columns={"nop": "B", "nature": "C"},
            row_sources=_AFOP_CURRENCIES,
            code_prefix="BSD1B.AFOP",
        ),
        *grid_lines(
            "BSD1B",
            AFOP,
            rows=(13, 14, 17),
            value_columns={"value": "B"},
            row_sources=_AFOP_AGGREGATE,
            code_prefix="BSD1B.AFOP",
        ),
    ),
    SCHEDULE_B: (
        *leaf_lines(
            "BSD1B",
            SCHEDULE_B,
            value_columns={"usd": "B", "gbp": "C", "dem": "D", "other_cedi_million": "F"},
            row_sources={},
            code_prefix="BSD1B.SCHB",
            default=RowSource(None, notes=_CONTINGENT_NOTE),
        ),
        *grid_lines(
            "BSD1B",
            SCHEDULE_B,
            rows=(6, 7, 8),
            value_columns={"eur": "E"},
            row_sources={},
            code_prefix="BSD1B.SCHB.eur",
            default=RowSource(
                None, notes=_CONTINGENT_NOTE + " (EURO column: blank in the template)"
            ),
        ),
    ),
}
