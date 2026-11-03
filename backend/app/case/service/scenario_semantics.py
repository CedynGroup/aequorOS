from __future__ import annotations

from collections.abc import Iterable
from typing import Any

ENGINE_ASSUMPTION_CATEGORIES = {
    "revenue_growth_rate": "growth",
    "expense_growth_rate": "expenses",
    "cash_flow_delay_days": "cash_flow_timing",
    "credit_usage_rate": "credit_usage",
    "repayment_rate": "repayment_behavior",
}


def resolve_engine_assumptions(
    assumptions: Iterable[Any],
) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    rows = list(assumptions)
    resolved: dict[str, Any] = {}
    missing: list[str] = []
    ambiguous: list[dict[str, Any]] = []
    for engine_key, category in ENGINE_ASSUMPTION_CATEGORIES.items():
        candidates = [item for item in rows if item.category == category]
        canonical = [item for item in candidates if item.key == engine_key]
        if len(canonical) == 1:
            resolved[engine_key] = canonical[0]
        elif len(candidates) == 1:
            resolved[engine_key] = candidates[0]
        elif not candidates:
            missing.append(category)
        else:
            ambiguous.append(
                {
                    "category": category,
                    "assumption_ids": [str(item.id) for item in candidates],
                    "corrective_action": (
                        f"Keep one {category.replace('_', ' ')} assumption or use the canonical "
                        f"key '{engine_key}'."
                    ),
                }
            )
    return resolved, missing, ambiguous
