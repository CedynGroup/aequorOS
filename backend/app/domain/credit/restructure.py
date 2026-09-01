"""Restructure cure rule (pure; credit PR-5).

BoG Notice BG/GOV/SEC/2025/23 ¶12: a restructured facility REMAINS classified
non-performing until the borrower has made 6 consecutive full repayments
(principal and interest) for monthly and quarterly schedules, 4 for
semi-annual — and a restructured BULLET loan remains non-performing until
fully settled at maturity. The cure counts arrive as governed parameters
(``restructure_cure_payments`` / ``restructure_cure_payments_semi_annual``);
the bullet rule is structural — no number of interim payments cures it, so
there is no parameter to tighten.

The ``payments_met`` count is the CORE SYSTEM's statement (the documented
``payments_met_since_restructure`` attribute) — this rule never derives it,
because deriving "consecutive full on-time repayments" from events would
silently overrule the servicer's own arrears accounting. The events plane
cross-checks it and raises a data-quality finding on disagreement.
"""

from __future__ import annotations

from decimal import Decimal

FREQUENCY_MONTHLY = "monthly"
FREQUENCY_QUARTERLY = "quarterly"
FREQUENCY_SEMI_ANNUAL = "semi_annual"
FREQUENCY_BULLET = "bullet"

REPAYMENT_FREQUENCIES: tuple[str, ...] = (
    FREQUENCY_MONTHLY,
    FREQUENCY_QUARTERLY,
    FREQUENCY_SEMI_ANNUAL,
    FREQUENCY_BULLET,
)


def restructure_holds_npl(
    *,
    restructured: bool,
    payments_met: int | None,
    repayment_frequency: str | None,
    cure_payments: Decimal | int,
    cure_payments_semi_annual: Decimal | int,
) -> bool:
    """Whether the Notice's ¶12 rule FORCES this facility non-performing.

    ``True`` = the facility is held at the entry NPL grade regardless of its
    days-past-due. A facility that is not restructured is never held. An
    unstated ``payments_met`` on a restructured facility holds it — the cure
    is evidence-based, and no evidence is not a cure. A bullet never cures
    here (settlement removes the loan from the book instead).
    """
    if not restructured:
        return False
    frequency = (repayment_frequency or "").strip().lower() or None
    if frequency == FREQUENCY_BULLET:
        return True
    required = (
        cure_payments_semi_annual if frequency == FREQUENCY_SEMI_ANNUAL else cure_payments
    )
    if payments_met is None:
        return True
    return payments_met < int(required)
