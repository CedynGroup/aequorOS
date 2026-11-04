"""``app.bi.service.mart_builder`` on the canonical fixture bank.

The builder is run synchronously (``tests/bi/fixtures.py``, the same
call the e2e bootstrap makes) over ``tests/support/factories/canonical.py``'s book —
18 included current-generation snapshots plus one superseded and one
error-status row that must never appear — with the live plane materialised by
the product's own ``pipeline.recompute_live`` so the engine tier and the
engine tier has something to copy. Extra rows (an unconverted
foreign-currency loan, a withdrawn snapshot, a mid-month book, loan events,
an unmapped branch, a sealed run) are added per test through the canonical
models, never through the builder.

Every expected number is worked from the fixture's own amounts.

This module's other-suite cases live in ``test_mart_builder_integration.py``.
"""

from __future__ import annotations

from app.bi.service import mart_builder


def test_baseline_scenario_matches_the_modules_own_constant() -> None:
    from app.capital.service import regulatory_capital  # noqa: PLC0415
    from app.credit.service import regulatory_credit  # noqa: PLC0415
    from app.liquidity.service import regulatory_liquidity  # noqa: PLC0415

    for module in (regulatory_capital, regulatory_credit, regulatory_liquidity):
        assert module.BASELINE_SCENARIO == mart_builder.BASELINE_SCENARIO
