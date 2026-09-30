"""Drill paths over catalogue dimensions (spec §Phase 1 Catalogue, Hierarchies).

A hierarchy is an ordered tuple of dimension ids, coarsest first. Every level
must be a catalogue dimension (pinned by the architecture test); a hierarchy
carries no authorization of its own — each level's module and sensitivity
apply when it is used, so drilling from counterparty type into a named
counterparty crosses from ``aggregated`` into ``restricted`` and is evaluated
as such.
"""

from __future__ import annotations

from app.domain.bi.catalogue.members import HierarchyDef


def hierarchies() -> tuple[HierarchyDef, ...]:
    return (
        HierarchyDef("geography", "Region and branch", ("branch.region", "branch.code")),
        HierarchyDef(
            "product",
            "Position type, family and product",
            ("position.type", "product.family", "product.code"),
        ),
        HierarchyDef(
            "counterparty",
            "Counterparty type, group and counterparty",
            ("counterparty.type", "counterparty.group", "counterparty.id"),
        ),
        HierarchyDef("sector", "Sector", ("loan.sector",)),
        HierarchyDef(
            "calendar_time",
            "Calendar year, quarter, month and date",
            ("time.calendar_year", "time.calendar_quarter", "time.calendar_month", "time.date"),
        ),
        HierarchyDef(
            "fiscal_time",
            "Fiscal year, quarter and date",
            ("time.fiscal_year", "time.fiscal_quarter", "time.date"),
        ),
        HierarchyDef("grade", "Classification grade", ("loan.grade",)),
        HierarchyDef("dpd_band", "Days past due band", ("loan.dpd_band",)),
        HierarchyDef("ifrs9_stage", "IFRS 9 stage", ("loan.ifrs9_stage",)),
        HierarchyDef("maturity", "Contractual maturity bucket", ("position.maturity_bucket",)),
        HierarchyDef("repricing", "Repricing bucket", ("position.repricing_bucket",)),
        HierarchyDef("currency", "Currency", ("position.currency",)),
        HierarchyDef(
            "gl_account",
            "GL account class and account",
            ("gl_account.class", "gl_account.parent", "gl_account.code"),
        ),
    )
