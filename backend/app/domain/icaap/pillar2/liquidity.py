"""Liquidity: assessed, managed, and deliberately not capitalised.

Liquidity risk is not absent from the ICAAP — it is handled somewhere else. The
liquidity regime runs on ratios and a survival horizon (LCR, NSFR, the ILAAP
and the liquidity return), not on a capital add-on, and Table 5 has no
liquidity row to put one in. Charging capital for it here would double-count
the ratio requirement and invent a row the return cannot carry.

So the method exists, it is called ``not_capitalised``, and it carries the
ILAAP conclusion as evidence that the risk was assessed. A blank is not the
same as a considered zero, and this is how the difference is recorded.

A bank that wants to hold capital for the COST of liquidity under stress —
funding spread widening — raises that as a separate judgemental item against
"other" risks, where it is visible as a choice.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.domain.icaap.pillar2.types import MethodResult, MethodStatus, detail_of

METHOD = "not_capitalised"

#: The ILAAP facts the item carries as its evidence.
ILAAP_FACT_KEYS: tuple[str, ...] = (
    "ilaap_adequate",
    "lcr_pct",
    "nsfr_pct",
    "worst_stressed_lcr_pct",
)


def not_capitalised(*, ilaap_facts: Mapping[str, str | None] | None = None) -> MethodResult:
    """Record the risk as assessed with no capital line, with the ILAAP facts."""
    facts = ilaap_facts or {}
    detail = {key: facts.get(key) for key in ILAAP_FACT_KEYS}
    missing = tuple(
        f"ilaap_fact_missing:{key}" for key in ILAAP_FACT_KEYS if facts.get(key) is None
    )
    return MethodResult(
        method=METHOD,
        method_version="v1",
        status=MethodStatus.NOT_CAPITALISED,
        basis=None,
        basis_value=None,
        baseline_amount=None,
        stressed_amount=None,
        baseline_derivation="not_applicable",
        stressed_derivation="not_applicable",
        detail=detail_of(detail),
        reasons=missing,
    )


__all__ = ["ILAAP_FACT_KEYS", "METHOD", "not_capitalised"]
