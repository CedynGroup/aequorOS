"""Loan exposure categories and IRR/FTP product families (pure).

Lifted verbatim from ``app.services.fact_derivation`` (P0-8) so the BI plane
can reuse the taxonomy rather than re-implement it. Fact derivation still owns
HOW a position is classified (``_classify_loans``); this module owns only the
tables it classifies against and the two lookups over them.
"""

from __future__ import annotations

import re

#: Loan regulatory-category → (exposure category, risk weight code).
LOAN_CATEGORY_MAP: dict[str, tuple[str, str]] = {
    "CORPORATE_UNRATED": ("corporate_unrated", "RW100"),
    "CORPORATE_LOAN_UNRATED_100RW": ("corporate_unrated", "RW100"),
    "AGRICULTURE": ("corporate_unrated", "RW100"),
    "SME_UNRATED": ("sme_retail", "RW75"),
    "SME_RETAIL": ("sme_retail", "RW75"),
    "RETAIL_UNSECURED": ("retail_other", "RW75"),
    "RETAIL_OTHER": ("retail_other", "RW75"),
    "RESIDENTIAL_MORTGAGE": ("residential_mortgage", "RW35"),
    "COMMERCIAL_REAL_ESTATE": ("commercial_real_estate", "RW100"),
}
#: The (exposure category, risk weight code) a loan lands in once it is 90+
#: days past due, whatever its regulatory class.
PAST_DUE_CATEGORY: tuple[str, str] = ("past_due_90", "RW150")
#: The exposure categories that count as retail (LCR inflow treatment).
RETAIL_LOAN_CATEGORIES: tuple[str, ...] = ("retail_other", "residential_mortgage")

#: The IRR/FTP family for an exposure whose regulatory class is unrecognised.
#: Rate risk is measured on the whole book, so the balance is NOT dropped —
#: dropping it would understate the repricing gap and the funding-cost base.
#: It gets its own label rather than joining ``corporate_loans``, because the
#: platform does not know that it is corporate.
UNCLASSIFIED_FAMILY = "unclassified_loans"

#: Exposure category → IRR/FTP family label.
LOAN_FAMILY: dict[str, str] = {
    "corporate_unrated": "corporate_loans",
    "sme_retail": "sme_loans",
    "retail_other": "retail_loans",
    "residential_mortgage": "mortgages",
    "commercial_real_estate": "cre_loans",
    "past_due_90": "corporate_loans",
}

#: The category an exposure lands in when its regulatory classification is not
#: one this platform recognises. It is deliberately not a Basel exposure class:
#: it carries no risk weight, and the capital engine refuses the moment it reads
#: one of these facts.
UNCLASSIFIED_PREFIX = "unclassified_"
_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")


def loan_family(category: str) -> str:
    """The IRR/FTP family label for an exposure category."""
    return LOAN_FAMILY.get(category, UNCLASSIFIED_FAMILY)


def unclassified_category(regulatory_category: str | None) -> str:
    """The exposure category for a loan whose regulatory class is unrecognised.

    Named after the bank's OWN category token so the refusal downstream points at
    the product taxonomy that has to be fixed, rather than at a generic bucket.
    """
    token = _NON_SLUG_RE.sub("_", (regulatory_category or "unmapped").strip().lower()).strip("_")
    return f"{UNCLASSIFIED_PREFIX}{token or 'unmapped'}"
