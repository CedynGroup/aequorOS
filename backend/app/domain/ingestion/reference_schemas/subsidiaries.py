"""``subsidiaries`` — the subsidiary register + book (feeds the consolidation-
only cells of BSD9 *Consolidated Balance Sheet* — 10. Minority interests and
the Annexure "Details of inter-company transactions" — and BSD5B *Consolidated
Capital Adequacy* — 3. Minority Interests, 18. Minority Interests in Tier 2
Preferred Shares; documents the group basis of BSD3B / BSD7B).

Guide, General Notes §1: subsidiaries are consolidated ONLY on BSD7B and BSD9;
the GROUP variants BSD3B / BSD5B are group-basis by their titles. The platform
holds the parent's books only, so every consolidation-only cell was
``input_required``. This register closes the gap the way a group finance team
already keeps it: **one row per subsidiary per reporting date** — identity
(id, name, country, entity type, functional currency), ownership and
consolidation treatment, and the subsidiary's own book at that date (total
assets / liabilities / equity, YTD net profit, Tier 1 and RWA where the
subsidiary is itself a regulated bank/NBFI) plus the inter-company balances
with the parent (amount due FROM the subsidiary = receivable, amount due TO
it = payable) and the group's minority-interest workings for a fully
consolidated subsidiary. Amounts are cedis (a subsidiary that reports in
another currency states its ``functional_currency`` and the bank's closing-rate
cedi equivalents; the platform never invents a rate).

**One reporting date per push** (batch ``as_of_date`` = ``reporting_date``):
BSD9 / BSD5B for a period read the latest batch on/before the period end, so a
batch must carry the whole register at that date (a multi-date file would be
read as one date; an omitted subsidiary reads as absent, not blank).

Roster cells of BSD3B (each subsidiary's twenty largest depositors / ten
largest monetary-sector / fifty largest non-monetary-sector exposures) are a
subsidiary POSITION book, a different grain from this register, and stay
``input_required`` — see docs/data_engine/datasets/subsidiaries.md
("BSD3B rosters") for the ``subsidiary_id``-keyed design.
"""

from __future__ import annotations

from . import ReferenceSchema, register

ENTITY_TYPES: tuple[str, ...] = ("bank", "nbfi", "insurance", "other")
CONSOLIDATION_METHODS: tuple[str, ...] = ("full", "equity", "none")
BOOLEANS: tuple[str, ...] = ("true", "false")

SCHEMA = register(
    ReferenceSchema(
        kind="subsidiaries",
        description=(
            "Subsidiary register + book: one row per subsidiary per reporting date — identity, "
            "ownership / consolidation treatment, the subsidiary's balance sheet and YTD result, "
            "inter-company balances with the parent and the group's minority-interest workings, "
            "in cedis"
        ),
        grain=(
            "one row per (reporting_date, subsidiary_id); one reporting date per push "
            "(as_of_date = reporting_date)"
        ),
        required=(
            "reporting_date",
            "subsidiary_id",
            "name",
            "country_code",
            "entity_type",
            "functional_currency",
            "ownership_pct",
            "consolidation_method",
            "control_via_board",
            "total_assets_ghs",
            "total_liabilities_ghs",
            "equity_ghs",
            "net_profit_ytd_ghs",
            "intercompany_receivable_ghs",
            "intercompany_payable_ghs",
        ),
        optional=(
            "tier1_capital_ghs",
            "rwa_ghs",
            "minority_interest_ghs",
            "minority_interest_tier2_pref_ghs",
            "investment_carrying_ghs",
            "intercompany_receivable_type",
            "intercompany_payable_type",
            "regulator",
            "licence_number",
            "notes",
        ),
        numeric=(
            "ownership_pct",
            "total_assets_ghs",
            "total_liabilities_ghs",
            "equity_ghs",
            "net_profit_ytd_ghs",
            "intercompany_receivable_ghs",
            "intercompany_payable_ghs",
            "tier1_capital_ghs",
            "rwa_ghs",
            "minority_interest_ghs",
            "minority_interest_tier2_pref_ghs",
            "investment_carrying_ghs",
        ),
        dates=("reporting_date",),
        enums={
            "entity_type": ENTITY_TYPES,
            "consolidation_method": CONSOLIDATION_METHODS,
            "control_via_board": BOOLEANS,
        },
    )
)


def validate_subsidiary_row(row: dict) -> list[str]:
    """Schema problems plus the cross-field rules a group finance team applies:
    ``ownership_pct`` within 0–100; a fully consolidated subsidiary owned below
    100 % must state its ``minority_interest_ghs`` (the group's own workings —
    the platform never derives it); ``ownership_pct`` of 100 carries none."""
    problems = SCHEMA.validate_row(row)
    try:
        pct = float(str(row.get("ownership_pct") or "").replace(",", ""))
    except ValueError:
        return problems  # already reported as non-numeric / missing
    if not 0 <= pct <= 100:  # noqa: PLR2004 — a percentage
        problems.append(f"ownership_pct must be between 0 and 100 (got {pct!r})")
    method = str(row.get("consolidation_method") or "").strip().lower()
    minority = row.get("minority_interest_ghs")
    if method == "full" and pct < 100 and minority in (None, ""):  # noqa: PLR2004
        problems.append(
            "a fully consolidated subsidiary owned below 100% must state minority_interest_ghs"
        )
    return problems
