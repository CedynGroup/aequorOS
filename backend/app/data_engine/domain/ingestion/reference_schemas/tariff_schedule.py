"""``tariff_schedule`` — the bank's published charges register keyed by the
official BSD15 rows (feeds BSD15A ``Domestic charges of banks`` + ``Range of
pdts i.r.o sav & cur``, and BSD15B ``International Banking Charges``).

One row per official tariff cell: ``(form, sheet, row_key)`` names the cell —
the key list is generated from the line maps
(``bog_forms.linemaps.bsd15a.tariff_row_keys``) and published in
docs/data_engine/datasets/tariff_schedule.md — and ``charge_value`` is what the
bank prints in that cell of the return: free text on the two "text" sheets
(``0.5% of value, min GHS 20``), a cedi amount on the Range sheet ("Amounts in
Cedis"). ``charge_basis`` / ``min_ghs`` / ``max_ghs`` are the structured
reading of the same tariff (optional; never used to compose the cell).
"""

from __future__ import annotations

from . import ReferenceSchema, register

FORMS: tuple[str, ...] = ("BSD15A", "BSD15B")
#: sheet codes a bank uses in the CSV (stable, spelt without the official
#: sheet names' trailing spaces / punctuation)
SHEETS: tuple[str, ...] = ("DOMESTIC", "RANGE", "INTL")
CHARGE_BASES: tuple[str, ...] = ("flat", "percent", "per_item", "range")

SCHEMA = register(
    ReferenceSchema(
        kind="tariff_schedule",
        description=(
            "Published tariff / charges register keyed by official BSD15A / BSD15B row "
            "(one row per official tariff cell)"
        ),
        grain="one row per (form, sheet, row_key) — one official BSD15A/BSD15B tariff cell",
        required=("form", "sheet", "row_key", "charge_value"),
        optional=(
            "label",
            "charge_basis",
            "min_ghs",
            "max_ghs",
            "currency",
            "effective_from",
            "notes",
        ),
        numeric=("min_ghs", "max_ghs"),
        dates=("effective_from",),
        enums={"form": FORMS, "sheet": SHEETS, "charge_basis": CHARGE_BASES},
    )
)
