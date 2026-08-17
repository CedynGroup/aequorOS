"""BSD17 — Foreign Inward Remittances (monthly, amounts in US$).

Official layout: ``BSG17-SHEET 1`` — recipients (Individuals / Exporters /
Service Providers / NGOs / Embassies / Others, rows 8–13) with the item number in
A (captured as numeric input cells; kept as constants) and the US$ amount in C;
row 15 ``Total (1+2+3+4+5+6)`` — the label spells the arithmetic but the official
cell C15 carries NO formula, so it is a data cell the bank totals (never invented
here). ``BSD17 -SHEET 2`` — the same amount by sending region (United Kingdom /
USA and Canada / European Union / ECOWAS / Rest of Africa / Others, rows 6–11)
and its Total (row 12), column B. Both grids are BLANK in the template and are
bound explicitly with :func:`_common.grid_lines`.

**Data-gap closure (2026-08-16):** every amount cell reads the bank's
``remittance_flows`` reference dataset (docs/data_engine/datasets/
remittance_flows.md — the built form of ``docs/remittance_scoping.md``: one
row per (month, direction, corridor_country, recipient_class, channel,
currency) with the bank's own US$ and cedi equivalents; ONE reporting month
per push, ``as_of_date`` = month-end; BSD17 takes the latest batch on/before
the period end) through ``refs.sum`` over ``amount_usd`` with
``direction=inbound``:

* Sheet 1 rows 8–13 filter ``recipient_class`` ∈ {individual, exporter,
  service_provider, ngo, embassy, other}; row 15 is the unfiltered inbound
  total (the bank's own Σ over its rows — the official cell has no formula,
  and the dataset's total is the bank's total, not a rule of ours).
* Sheet 2 rows 6–11 filter ``region`` ∈ {uk, usa_canada, eu, ecowas,
  rest_of_africa, other} — the roll-up the bank assigns per row (the ISO →
  region table is in the dataset doc); row 12 is the unfiltered inbound total.

Before the register is ingested every resolver returns ``None`` and each cell
stays ``input_required`` naming the dataset, exactly as before.
"""

from __future__ import annotations

from ._common import RowSource, grid_lines

_FORM = "BSD17"
_SHEET1 = "BSG17-SHEET 1"
_SHEET2 = "BSD17 -SHEET 2"
KIND = "remittance_flows"

#: official Sheet-1 row → ``recipient_class`` value
RECIPIENT_ROWS: dict[int, str] = {
    8: "individual",
    9: "exporter",
    10: "service_provider",
    11: "ngo",
    12: "embassy",
    13: "other",
}
#: official Sheet-2 row → ``region`` value
REGION_ROWS: dict[int, str] = {
    6: "uk",
    7: "usa_canada",
    8: "eu",
    9: "ecowas",
    10: "rest_of_africa",
    11: "other",
}

_BY_RECIPIENT = (
    "remittance_flows register required (docs/data_engine/datasets/remittance_flows.md — "
    "inbound remittances for the month, US$ equivalent, by recipient_class)"
)
_BY_REGION = (
    "remittance_flows register required (docs/data_engine/datasets/remittance_flows.md — "
    "inbound remittances for the month, US$ equivalent, by sending region)"
)
_TOTAL_NOTE = (
    "official Total row carries no template formula — bound to the register's own total "
    "(Σ amount_usd over every inbound row for the month)"
)


def _item(n: int) -> RowSource:
    return RowSource(
        "constant", {"value": n}, notes="template item number (official value kept)", unscaled=True
    )


def _inbound_usd(note: str, **filters: str) -> RowSource:
    return RowSource(
        "refs.sum",
        {
            "kind": KIND,
            "value_field": "amount_usd",
            "filters": {"direction": "inbound", **filters},
        },
        notes=note,
    )


LINES = {
    _SHEET1: (
        *grid_lines(
            _FORM,
            _SHEET1,
            rows=(8, 9, 10, 11, 12, 13, 15),
            value_columns={"item": "A"},
            row_sources={**{row: _item(row - 7) for row in range(8, 14)}, 15: _item(7)},
            code_prefix="BSD17.S1.item",
        ),
        *grid_lines(
            _FORM,
            _SHEET1,
            rows=(8, 9, 10, 11, 12, 13, 15),
            value_columns={"amount_usd": "C"},
            row_sources={
                **{
                    row: _inbound_usd(_BY_RECIPIENT, recipient_class=recipient)
                    for row, recipient in RECIPIENT_ROWS.items()
                },
                15: _inbound_usd(_TOTAL_NOTE),
            },
            code_prefix="BSD17.S1",
        ),
    ),
    _SHEET2: grid_lines(
        _FORM,
        _SHEET2,
        rows=range(6, 13),
        value_columns={"amount_usd": "B"},
        row_sources={
            **{row: _inbound_usd(_BY_REGION, region=region) for row, region in REGION_ROWS.items()},
            12: _inbound_usd(_TOTAL_NOTE),
        },
        code_prefix="BSD17.S2",
    ),
}
