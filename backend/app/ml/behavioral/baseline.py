"""Generic, product-code-agnostic priors used when a bank has too little history.

These are deliberately NOT keyed to our synthetic product codes — a real bank
has its own codes — so they act as a last-resort prior. The primary fallback is
always the bank's own per-product empirical mean (in ``estimator.estimate``);
these priors only fill a product that has no usable label at all.
"""

from __future__ import annotations

# Basel/ALM-conventional priors.
GENERIC_PRIOR: dict[str, float] = {
    "nmd-duration": 24.0,       # months
    "prepayment": 0.06,         # annual CPR
    "deposit-stability": 0.5,   # stable fraction
}

# Clamp ranges for each model's output value.
VALUE_RANGE: dict[str, tuple[float, float]] = {
    "nmd-duration": (1.0, 84.0),
    "prepayment": (0.0, 0.60),
    "deposit-stability": (0.0, 1.0),
}
