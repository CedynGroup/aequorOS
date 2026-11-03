"""Pure Basel II comprehensive-approach CRM haircut defaults.

A leaf module on purpose: forecasting and credit read these defaults and both
sit across a service-level import cycle with capital's plan service, so the
constant must be importable without pulling any capital service module.
"""

from __future__ import annotations

from decimal import Decimal

# Phase 2 item 9: Basel II comprehensive-approach supervisory haircuts
# (¶151 table; conservative representative pick per class — 10-business-day
# holding period, the table's upper band where a range applies). These are
# CODE defaults, overridable per class through the effective-dated
# ``param_crm_haircut`` register; a class absent from both gets zero
# recognition — a haircut is never invented.
DEFAULT_CRM_HAIRCUTS: dict[str, Decimal] = {
    "CASH": Decimal("0"),
    "GOLD": Decimal("15"),
    "SOVEREIGN_DEBT": Decimal("4"),
    "BANK_DEBT": Decimal("8"),
    "CORPORATE_DEBT": Decimal("8"),
    "EQUITY_MAIN_INDEX": Decimal("15"),
    "EQUITY_OTHER": Decimal("25"),
}

__all__ = ["DEFAULT_CRM_HAIRCUTS"]
