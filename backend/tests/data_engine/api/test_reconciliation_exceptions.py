"""Persisted reconciliation proposals, authenticated four-eyes approval and revocation.

This module's other-suite cases live in
``test_reconciliation_exceptions_integration.py``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from app.data_engine.schemas.reconciliation import (
    ReconciliationExceptionCreate,
)
from tests.data_engine.api.test_reconciliation_exceptions_integration import PROPOSAL


@given(micros=st.integers(min_value=1, max_value=1_000_000), zeros=st.integers(0, 40))
def test_proposal_ceiling_precision_preserves_equivalent_decimals(micros: int, zeros: int) -> None:
    ceiling = Decimal(micros).scaleb(-6)
    equivalent = f"{ceiling:.6f}" + "0" * zeros
    parsed = ReconciliationExceptionCreate.model_validate(
        {**PROPOSAL, "max_gap_fraction": equivalent}
    )
    assert parsed.max_gap_fraction == ceiling
    with pytest.raises(ValidationError):
        ReconciliationExceptionCreate.model_validate(
            {**PROPOSAL, "max_gap_fraction": f"{ceiling:.6f}" + "0" * zeros + "1"}
        )
