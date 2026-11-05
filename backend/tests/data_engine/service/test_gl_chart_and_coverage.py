"""GL chart resolution and GL/sub-ledger coverage in the fact derivation.

These pin the three defects found on the primary database on 2026-08-21, each of
which put a wrong number on a filed balance sheet:

1. **A retired account code was resurrected forever.** The loader kept the
   newest row per account code across all history, so a code dropped from the
   chart kept contributing its last balance — 24m of phantom cash and 180m of
   phantom deposits on Sample Bank.
2. **A blank chart refresh silently republished last month's ledger.** The
   ``balance IS NOT NULL`` filter dropped an entire current generation that
   carried no balances, and the per-code fallback then served the prior
   period's figures with nothing said.
3. **The identity compared two different books.** GL securities were counted
   again from the position sub-ledger; the interbank asset leg was counted
   nowhere while its liability leg was counted; GL liabilities and GL equity
   were never read at all.

This module's other-suite cases live in ``test_gl_chart_and_coverage_integration.py``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.live.service.fact_derivation import (
    _CentralBankNames,
    _classify_gl_assets,
    _classify_gl_funding,
    _GlCoverage,
    _is_borrowing_gl,
    _is_deposit_gl,
    _is_interbank_borrowing_gl,
    _is_interbank_placement_gl,
    _is_loan_gl,
    _is_loan_loss_allowance_gl,
    _is_securities_gl,
    _resolve_gl_chart,
)
from app.models import CanonicalGlAccount
from tests.support.factories.canonical import FIXTURE_AS_OF

_MAY = date(2026, 5, 31)
_JUNE = FIXTURE_AS_OF


def _account(
    code: str,
    *,
    as_of: date,
    balance: str | None,
    name: str = "Account",
    account_class: str = "ASSET",
) -> CanonicalGlAccount:
    """An unpersisted GL row — ``_resolve_gl_chart`` is pure over its input."""
    return CanonicalGlAccount(
        account_code=code,
        name=name,
        account_class=account_class,
        as_of_date=as_of,
        balance=None if balance is None else Decimal(balance),
    )


def test_a_code_absent_from_the_current_chart_is_retired_not_carried_forward() -> None:
    rows = [
        (_account("1000", as_of=_MAY, balance="24000000", name="Cash and balances"), "full"),
        (_account("1001", as_of=_MAY, balance="14000000", name="Cash on Hand"), "full"),
        (_account("1001", as_of=_JUNE, balance="15000000", name="Cash on Hand"), "full"),
    ]

    resolved, chart_as_of, retired = _resolve_gl_chart(rows)

    assert chart_as_of == _JUNE
    assert [account.account_code for account in resolved] == ["1001"]
    assert resolved[0].balance == Decimal("15000000")
    # The drop is reported with the amount it removed — never silent.
    assert retired == (("1000", "Cash and balances", Decimal("24000000")),)


def test_a_charted_code_keeps_its_last_balance_when_the_new_generation_is_blank() -> None:
    """The primary's 2026-06-30 chart refresh carried NULL balances for all 37
    accounts. The chart is June's; the balances are honestly May's."""
    rows = [
        (_account("1399", as_of=_MAY, balance="-40277124.63"), "full"),
        (_account("1399", as_of=_JUNE, balance=None), "full"),
    ]

    resolved, chart_as_of, retired = _resolve_gl_chart(rows)

    assert chart_as_of == _JUNE
    assert retired == ()
    assert [(row.account_code, row.as_of_date, row.balance) for row in resolved] == [
        ("1399", _MAY, Decimal("-40277124.63"))
    ]


def test_a_charted_code_that_never_carried_a_balance_contributes_nothing() -> None:
    rows = [(_account("1404", as_of=_JUNE, balance=None), "full")]

    resolved, chart_as_of, retired = _resolve_gl_chart(rows)

    # Not read as a zero, not invented, not retired — simply absent.
    assert resolved == []
    assert chart_as_of == _JUNE
    assert retired == ()


def test_an_incremental_batch_cannot_retire_the_accounts_it_omits() -> None:
    """A delta upload carries the accounts that MOVED. Absence proves nothing."""
    rows = [
        (_account("1001", as_of=_MAY, balance="14000000"), "full"),
        (_account("1002", as_of=_MAY, balance="8000000"), "full"),
        (_account("1001", as_of=_JUNE, balance="15000000"), "incremental"),
    ]

    resolved, chart_as_of, retired = _resolve_gl_chart(rows)

    assert chart_as_of == _MAY  # the last FULL extraction defines the chart
    assert retired == ()
    assert [(row.account_code, row.balance) for row in resolved] == [
        ("1001", Decimal("15000000")),
        ("1002", Decimal("8000000")),
    ]


def test_with_no_full_extraction_nothing_is_retired() -> None:
    rows = [
        (_account("1001", as_of=_MAY, balance="14000000"), "incremental"),
        (_account("1002", as_of=_JUNE, balance="8000000"), "incremental"),
    ]

    resolved, chart_as_of, retired = _resolve_gl_chart(rows)

    assert chart_as_of is None
    assert retired == ()
    assert [row.account_code for row in resolved] == ["1001", "1002"]


def test_securities_and_loan_blocks_are_recognised_in_both_chart_schemes() -> None:
    # Sample Bank's bare numeric chart.
    assert _is_securities_gl("1201", "government of ghana t-bills (91d)")
    assert _is_loan_gl("1301", "loans to retail customers")
    # The SDI's prefixed chart, where the numeric backbone survives the prefix
    # AND the account name carries the same signal.
    assert _is_securities_gl("GL-1200", "investments — gog securities")
    assert _is_loan_gl("GL-1300", "loans & advances (gross)")
    # A chart with no numeric convention at all falls back to the name.
    assert _is_securities_gl("ASSET.INVEST", "held-to-maturity securities")
    assert _is_loan_gl("ASSET.CREDIT", "customer advances")


def test_the_loan_loss_allowance_is_never_treated_as_a_covered_loan_account() -> None:
    """The loan sub-ledger is GROSS, so nothing stands in for the allowance.

    Both charts name it, and neither may drop it — total assets are net of
    impairment.
    """
    assert _is_loan_loss_allowance_gl("1399", "loan loss provisions (contra)")
    assert _is_loan_loss_allowance_gl("GL-1390", "less: impairment allowance")
    assert not _is_loan_gl("1399", "loan loss provisions (contra)")
    assert not _is_loan_gl("GL-1390", "less: impairment allowance")


def test_interbank_and_deposit_blocks_are_recognised_in_both_schemes() -> None:
    assert _is_interbank_placement_gl("1101", "interbank placements - local")
    assert _is_interbank_placement_gl("GL-1150", "due from bank balances")
    assert _is_deposit_gl("2001", "current deposits - retail")
    assert _is_deposit_gl("GL-2100", "customer deposits")
    assert _is_interbank_borrowing_gl("2101", "interbank borrowings - local")
    # Borrowed money that is NOT interbank: never covered by the interbank
    # sub-ledger, or 33.9m of Tier 2 debt drops off the balance sheet.
    assert _is_borrowing_gl("2301", "subordinated debt (tier 2)")
    assert not _is_interbank_borrowing_gl("2301", "subordinated debt (tier 2)")
    assert _is_borrowing_gl("GL-2400", "borrowings")
    assert not _is_interbank_borrowing_gl("GL-2400", "borrowings")


class _Book:
    """The bare shape ``_classify_gl_*`` reads off ``_Canonical``."""

    base_currency = "GHS"

    def __init__(
        self,
        accounts: list[CanonicalGlAccount],
        central_bank_names: _CentralBankNames | None = None,
    ) -> None:
        self.gl_accounts = accounts
        # The classifier resolves the central bank from the bank's jurisdiction
        # registry row rather than a literal token (NEW-40). These cases carry
        # Ghana's forms because the fixture charts are Ghanaian; the assertions
        # below are unchanged.
        self.central_bank_names = central_bank_names or _CentralBankNames.from_registry(
            "Bank of Ghana", "BoG"
        )


_ALL_COVERED = _GlCoverage(
    securities=True,
    loans=True,
    interbank_placements=True,
    deposits=True,
    interbank_borrowings=True,
)
_NONE_COVERED = _GlCoverage(
    securities=False,
    loans=False,
    interbank_placements=False,
    deposits=False,
    interbank_borrowings=False,
)


def test_a_gl_block_with_a_sub_ledger_behind_it_is_counted_once() -> None:
    book = _Book(
        [
            _account(
                "GL-1200", as_of=_JUNE, balance="95971647.25", name="Investments — GoG securities"
            ),
            _account("GL-1900", as_of=_JUNE, balance="51485014.07", name="Other assets"),
        ]
    )

    _, other = _classify_gl_assets(book, _ALL_COVERED, [])  # pyright: ignore[reportArgumentType]

    # The securities block is carried by SECURITY_HOLDING positions, so it must
    # not appear again in other_assets. This is the SDI's 95.97m double count.
    assert other == Decimal("51485014.07")


def test_a_gl_block_with_no_sub_ledger_behind_it_is_still_counted() -> None:
    book = _Book(
        [
            _account(
                "GL-1200", as_of=_JUNE, balance="95971647.25", name="Investments — GoG securities"
            ),
        ]
    )

    _, other = _classify_gl_assets(book, _NONE_COVERED, [])  # pyright: ignore[reportArgumentType]

    assert other == Decimal("95971647.25")


def test_uncovered_gl_borrowings_are_carried_and_gl_equity_is_the_equity_line() -> None:
    warnings: list[str] = []
    book = _Book(
        [
            _account(
                "GL-2100",
                as_of=_JUNE,
                balance="502165877.45",
                name="Customer deposits",
                account_class="LIABILITY",
            ),
            _account(
                "GL-2400",
                as_of=_JUNE,
                balance="15064976.32",
                name="Borrowings",
                account_class="LIABILITY",
            ),
            _account(
                "GL-2900",
                as_of=_JUNE,
                balance="250000",
                name="Accounts payable",
                account_class="LIABILITY",
            ),
            _account(
                "GL-3100",
                as_of=_JUNE,
                balance="83289750.33",
                name="Stated capital",
                account_class="EQUITY",
            ),
        ]
    )

    borrowings, equity, unreconciled = _classify_gl_funding(
        book,  # pyright: ignore[reportArgumentType]
        _GlCoverage(
            securities=True,
            loans=True,
            interbank_placements=True,
            deposits=True,
            interbank_borrowings=False,
        ),
        warnings,
    )

    # Deposits: covered by the sub-ledger, so not double counted.
    # Borrowings: no sub-ledger, so the ledger's own amount stands.
    assert borrowings == Decimal("15064976.32")
    assert equity == Decimal("83289750.33")
    # A payable has no honest home in the balance-sheet taxonomy. It is named
    # rather than bucketed, and it widens the identity gap on purpose.
    assert unreconciled == [("GL-2900", "Accounts payable", Decimal("250000"))]
    assert any("Accounts payable" in warning for warning in warnings)
