"""``bi_fact_gl_monthly`` mirrors BSD7's own P&L semantics (D-021), by construction.

A small INCOME/EXPENSE ledger is inserted beside the canonical fixture — the
shape ``tests/reporting/service/bog_forms/test_bsd7_integration.py`` uses, moved to the fixture's
fiscal year — with every case the h_b5 contract names: a year-to-date account
with a prior month (movement = difference), one booked only this month
(``missing_prior``, never a fabricated month), a foreign-currency account, a
``period``-basis account fed weekly (H-006: the mid-month row is a month-to-date
figure superseded by the month-end one), a register-mapped account with a
negative sign, an unmapped account, a prior-year generation and a superseded
generation (never read). The mart's per-line sums are then compared with the
``bsd7.pl_line`` resolver itself — exact equality, because the mart is computed by
the SAME ``pl_mapping`` functions the return resolver delegates to. That is a
property of how the rows are built, not a verdict BI issues on them: BI stopped
grading its figures against the returns the platform files on 2026-09-29.

This module's other-suite cases live in ``test_gl_monthly_integration.py``.
"""

from __future__ import annotations

__all__ = ["_APR", "_JUN", "_MAY"]
from dataclasses import fields
from datetime import date
from decimal import Decimal

from app.bi.domain import extract
from app.db.base import Base
from app.live.domain.gl import pl_mapping
from tests.bi.service.test_mart_builder_integration import AS_OF

_APR, _MAY, _JUN = date(2026, 4, 30), date(2026, 5, 31), AS_OF


def test_gl_monthly_row_produces_every_mart_column() -> None:
    columns = set(Base.metadata.tables["bi_fact_gl_monthly"].columns.keys())
    assert {f.name for f in fields(extract.GlMonthlyFactRow)} == columns - {
        "builder_version",
        "built_at",
    }


def test_gl_monthly_row_is_pure_and_keys_currency_by_the_latest_generation() -> None:
    generations = [
        pl_mapping.Generation("4001", _APR, "XRC", Decimal(100), ""),
        pl_mapping.Generation("4001", _MAY, "XRC", Decimal(210), ""),
        pl_mapping.Generation("4001", _JUN, None, Decimal(330), ""),  # unstated = base
    ]
    row = extract.gl_monthly_row(
        generations,
        organization_id="OR-X",
        bank_id="BK-X",
        month_end=_JUN,
        fy_start=date(2026, 1, 1),
        account_class="INCOME",
        rule=pl_mapping.MappingRule("1a", Decimal("-1"), None),
        base_currency="XRC",
    )
    assert row is not None
    assert (row.currency, row.calendar_month, row.month_end) == ("", date(2026, 6, 1), _JUN)
    assert (row.ytd_rc, row.prior_ytd_rc, row.movement_rc, row.missing_prior) == (
        Decimal(330),
        Decimal(210),
        Decimal(120),
        False,
    )
    assert (row.pl_line, row.pl_sign, row.balance_basis) == ("1a", -1, "ytd")
    # the rule's basis wins over whatever the caller stamped on the generations
    period = extract.gl_monthly_row(
        generations,
        organization_id="OR-X",
        bank_id="BK-X",
        month_end=_JUN,
        fy_start=date(2026, 1, 1),
        account_class="INCOME",
        rule=pl_mapping.MappingRule("16", Decimal(1), pl_mapping.PERIOD),
        base_currency="XRC",
    )
    assert period is not None
    assert (period.ytd_rc, period.movement_rc, period.balance_basis) == (
        Decimal(640),
        Decimal(330),
        "period",
    )
    unmapped = extract.gl_monthly_row(
        generations,
        organization_id="OR-X",
        bank_id="BK-X",
        month_end=_JUN,
        fy_start=date(2026, 1, 1),
        account_class="INCOME",
        rule=None,
        base_currency="XRC",
    )
    assert unmapped is not None and unmapped.pl_line is None and unmapped.pl_sign is None
    assert (
        extract.gl_monthly_row(
            generations,
            organization_id="OR-X",
            bank_id="BK-X",
            month_end=date(2026, 3, 31),
            fy_start=date(2026, 1, 1),
            account_class="INCOME",
            rule=None,
            base_currency="XRC",
        )
        is None
    )
