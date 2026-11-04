"""Vocabulary and boundary rules for the case plane (advisory, never filed).

`risk_cases` and everything hanging off them — the `Financial*` records,
`CalculationRun`, `CalculationForecastPeriod`, `CapitalProjection`,
`CapitalIndicator` — form an internal credit-analysis workspace. They read
case-local data an analyst typed or uploaded about one borrower. They never
read `BankFinancialFact`, they never call the regulatory engines in
`app/domain/`, and nothing they produce reaches a `RegulatoryRun`, a BoG
return, or a filing.

The forensic audit of 2026-08-21
(`docs/FORENSIC_CALCULATION_ARCHITECTURE_AUDIT_2026-08-21.md` §10, and the
inventory at `docs/forensic_calculation_audit_2026-08-21.md` L857) found the
two planes already isolated at the data layer. The remaining risk was purely
one of naming: the case plane emitted outputs called "liquidity" and
"capital" — the same two words the regulatory plane uses for filed LCR/NSFR
and CAR — so a reader could not tell an advisory number from a filed one by
its label. This module holds the vocabulary that keeps them apart:

* case liquidity output is **cash-flow adequacy** — a cash roll-forward over
  the case's own forecast periods on book values. It is not Basel LCR or NSFR.
* case capital output is **solvency pressure** — unweighted equity over book
  assets against MVP review thresholds. It is not risk-weighted CAR.

Only labels moved. No formula, threshold, or stored value changed. Storage
names (`capital_projections`, `liquidity_analysis_results`,
`calculation_forecast_periods`), model class names, route paths, persisted
rule ids and persisted enum values are wire and storage contracts and
deliberately keep their historical spelling; the display wording is mapped at
the presentation boundary instead.

Boundary rule (audit §10, fourth bullet): case output must not be migrated
into `BankFinancialFact` without a reviewed canonical adapter and a
reconciliation. Case records are hand-editable and carry no ingestion batch,
so a case figure written as a bank fact would enter a sealed run's
`input_hash` with no provenance — and would breach the no-seeded-data order in
`CLAUDE.md`. Nothing does this today. The guard is structural:
`tests/architecture/test_case_plane_boundary.py` fails if any module reaches
across the line in either direction, or if a single module touches both a
case-plane model and `BankFinancialFact`. Amending that test is the review
gate — an adapter cannot be written without the change being visible in it.
"""

from __future__ import annotations

#: Standing note carried by every case-plane figure that a person can read.
#: One wording, reused — a metric tile and a finding must not disagree about
#: what the number is. Kept to one sentence because it repeats per item.
ADVISORY_NOTE = "Internal credit analysis for this case: not a regulatory figure, never filed."

#: What the case-scoped cash roll-forward is called to a person. Replaces the
#: bare word "liquidity", which belongs to the filed LCR/NSFR plane.
CASH_FLOW_ADEQUACY = "cash-flow adequacy"

#: What the case-scoped equity-to-assets analysis is called to a person.
#: Replaces the bare word "capital", which belongs to the filed CAR plane.
SOLVENCY_PRESSURE = "solvency pressure"


def with_advisory_note(text: str) -> str:
    """Append the standing advisory note to a user-facing string.

    Used for finding rationales and metric descriptions so an operator reading
    a single case figure out of context still sees that it is not filed.
    """
    stripped = text.rstrip()
    if stripped.endswith(ADVISORY_NOTE):
        return stripped
    return f"{stripped} {ADVISORY_NOTE}"
