"""The closed vocabulary of ``capital_structure`` tiers (pure).

A capital component's tier decides which capital ratio it counts toward, so an
unrecognised tier is refused rather than read as CET1: a subordinated loan
tagged "Tier 2 " would otherwise land in CET1, uncapped.

Basis: Prudential (BoG CRD 2018); input: the bank's IAS 32 ¶15-16 liability or
equity classification of each instrument, which this module does not revisit.
"""

from __future__ import annotations

import re

#: The capital engine's tier codes (``app.domain.capital.engine`` reads them here).
TIER_CET1 = "CET1"
TIER_AT1 = "AT1"
TIER_T2 = "T2"

#: Fact marker emitted when the authoritative register cannot be derived.
CAPITAL_REGISTER_REFUSED_CATEGORY = "capital_register_refused"

#: Every accepted spelling, after :func:`_compact`, → the canonical tier.
_TIER_ALIASES: dict[str, str] = {
    "CET1": TIER_CET1,
    "COMMONEQUITYTIER1": TIER_CET1,
    "AT1": TIER_AT1,
    "ADDITIONALTIER1": TIER_AT1,
    "T2": TIER_T2,
    "TIER2": TIER_T2,
}
_DEDUCTION_SUFFIX = "DEDUCTION"
_SEPARATORS = re.compile(r"[\s_\-]+")

#: The spellings a bank is told to use; every alias above is also accepted.
DOCUMENTED_TIERS: tuple[str, ...] = (
    "CET1",
    "AT1",
    "T2",
    "CET1_DEDUCTION",
    "AT1_DEDUCTION",
    "T2_DEDUCTION",
)

#: Components that are never capital, whatever tier they are tagged with. The
#: BoG Credit Risk Reserve is "EXCLUDED from the adjusted capital base for CAR
#: (Guide for Financial Publication BSD/2017 §2.2.1(ii)-(iv) p.10, §2.5 item 5
#: p.44; CRD June 2018 ¶32 omits it from CET1)" — as cited at
#: ``app/domain/stress/appendix_ii.py``.
EXCLUDED_CAPITAL_COMPONENTS: frozenset[str] = frozenset({"credit_risk_reserve"})


def _compact(raw: str) -> str:
    return _SEPARATORS.sub("", raw.strip().upper())


def parse_capital_tier(raw: object) -> tuple[str, bool] | None:
    """``(tier, is_deduction)`` for a recognised tier spelling, else ``None``.

    Spaces, underscores, hyphens and case are ignored, so ``"Tier 2"``,
    ``"TIER_2"`` and ``"t2"`` all read as T2. ``"T1"`` / ``"Tier 1"`` is refused:
    it does not say whether the instrument is CET1 or AT1.
    """
    if raw is None:
        return None
    compact = _compact(str(raw))
    is_deduction = compact.endswith(_DEDUCTION_SUFFIX)
    tier = _TIER_ALIASES.get(compact.removesuffix(_DEDUCTION_SUFFIX))
    if tier is None:
        return None
    return tier, is_deduction


def is_excluded_component(component: object) -> bool:
    """Whether a ``capital_component`` is kept out of the capital base entirely."""
    return str(component or "").strip().lower() in EXCLUDED_CAPITAL_COMPONENTS
