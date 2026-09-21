"""Manual tables: figures a preparer types, with evidence attached.

Two block types share this resolver. ``financials`` is the ¶51 financial summary
with a fixed row set and three fiscal-year columns; ``manual_table`` is
free-form. Both are manual for the same reason: the BoG form snapshots the
platform holds carry empty totals, and profit before tax and dividends are not
derivable from them, so reading the workbook cells is its own mapping project
(proposed for P2). Typing them and attaching the audited accounts is honest;
deriving them from a form that does not carry them would not be.

A manual block never goes stale — it is exactly what somebody typed — but
readiness refuses a freeze until it carries an active evidence attachment.
"""

from __future__ import annotations

from typing import Any

from app.domain.icaap.blocks import SourceProbe
from app.services.icaap import resolvers
from app.services.icaap.resolvers import Resolution, ResolveContext, Unavailable

_VERSION = "1"

#: The ¶51(b), (d) and (e) lines, as row keys. Labels are the printed wording.
FINANCIALS_ROWS: tuple[tuple[str, str], ...] = (
    ("operating_profit", "Operating profit"),
    ("profit_before_tax", "Profit before tax"),
    ("profit_after_tax", "Profit after tax"),
    ("dividends_paid", "Dividends paid"),
    ("shareholders_funds", "Shareholders' funds"),
    ("total_assets", "Total assets"),
    ("customer_deposits", "Customer deposits"),
    ("interbank_funding", "Interbank funding"),
)

_NOT_ENTERED = (
    "No figures have been entered yet. Fill the table in and attach the evidence it comes from."
)


def financials_template(fiscal_year: int) -> dict[str, Any]:
    """The empty ¶51 table, so the preparer starts from the right rows."""
    return {
        "columns": [
            {"key": "fy2", "label": str(fiscal_year - 2), "kind": "amount"},
            {"key": "fy1", "label": str(fiscal_year - 1), "kind": "amount"},
            {"key": "fy0", "label": str(fiscal_year), "kind": "amount"},
        ],
        "rows": [
            {"key": key, "label": label, "fact_key": None, "cells": {}}
            for key, label in FINANCIALS_ROWS
        ],
    }


class _ManualResolver:
    """Manual blocks resolve only from what ``put_manual_table`` stored."""

    block_type = "manual_table"
    version = _VERSION

    def probe(self, rc: ResolveContext) -> SourceProbe:
        stored = (rc.block.params or {}).get("table")
        if not isinstance(stored, dict):
            return SourceProbe(current_key=None, reason="no figures entered yet")
        return SourceProbe(current_key="manual")

    def resolve(self, rc: ResolveContext) -> Resolution:
        raise Unavailable(_NOT_ENTERED)


class FinancialsResolver(_ManualResolver):
    block_type = "financials"


class ManualTableResolver(_ManualResolver):
    block_type = "manual_table"


def build_manual_resolution(
    rc: ResolveContext,
    *,
    columns: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    seq: int,
) -> Resolution:
    """Turn a saved manual table into a binding payload and its facts."""
    table = resolvers.TableBuilder("manual", rc.block.title or rc.spec.title)
    table.column("label", "Line", "text")
    for column in columns:
        table.column(str(column["key"]), str(column["label"]), str(column["kind"]))
    facts: dict[str, dict[str, Any]] = {}
    for row in rows:
        cells = {str(key): value for key, value in (row.get("cells") or {}).items()}
        table.row({"label": row.get("label"), **cells})
        fact_key = row.get("fact_key")
        if not fact_key:
            continue
        for column in columns:
            key = str(column["key"])
            value = cells.get(key)
            if value is None:
                # A blank cell stays blank. It is never read as a measured zero.
                continue
            facts[f"{fact_key}_{key}"] = resolvers.dynamic_fact(
                f"{fact_key}_{key}",
                f"{row.get('label')} ({column['label']})",
                str(column["kind"]),
                value,
                rc.currency,
            )
    body = resolvers.payload(
        title=rc.block.title or rc.spec.title,
        as_of=None,
        source_label="Entered by the preparer, with evidence attached",
        currency=rc.currency,
        tables=[table.build()],
    )
    return Resolution(
        source_kind="manual",
        source_ref={"entered": True, "row_count": len(rows), "column_count": len(columns)},
        source_key=f"manual:{seq}",
        source_as_of=None,
        payload=body,
        facts=facts,
    )


__all__ = [
    "FINANCIALS_ROWS",
    "FinancialsResolver",
    "ManualTableResolver",
    "build_manual_resolution",
    "financials_template",
]
