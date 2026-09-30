"""``app.domain.gl.pl_mapping``: BSD7's P&L rules, pinned on plain rows.

Every figure here is worked by hand from the contract in the module docstring
(``h_b5_report.md`` §2) — the fiscal windows, the CoA → item precedence, the
year-to-date and period-movement arithmetic, and the H-006 rule that a
``period``-basis account contributes only its LATEST generation per calendar
month. BSD7 delegates to these functions (its own suites pin byte-identical
output); ``bi_fact_gl_monthly`` is computed from the same ones (D-021).
Currencies are fictional ISO-shaped codes.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.gl import pl_mapping
from app.domain.gl.pl_mapping import (
    PERIOD,
    YTD,
    CoaMapping,
    Generation,
    MappingRule,
    account_rule,
    coa_mapping_from_rows,
    effective_rule,
    fiscal_quarter,
    fiscal_year,
    fiscal_year_start,
    in_currency,
    latest_generation,
    latest_per_month,
    line_total,
    mapping_rule,
    monthly_account_figures,
    period_total,
    window_start,
    ytd_total,
)

BASE = "XRC"
FOREIGN = "XFC"

_JAN, _FEB, _MAR = date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)
_APR, _MAY, _MID_MAY = date(2026, 4, 30), date(2026, 5, 31), date(2026, 5, 15)
_FY = date(2026, 1, 1)


def _gen(
    code: str, as_of: date, balance: int, *, currency: str | None = BASE, basis: str = YTD
) -> Generation:
    return Generation(code, as_of, currency, Decimal(balance), basis)


def _all(_currency: str | None) -> bool:
    return True


def _item(rule: MappingRule | None) -> str:
    assert rule is not None
    return rule.item


# --- windows ------------------------------------------------------------------------------


def test_window_start_follows_the_fiscal_year() -> None:
    """The BSD7 pin, on plain dates (the resolver wraps a period object)."""
    start, end = date(2025, 5, 1), date(2025, 5, 31)
    assert window_start(start, end, "ptd", 1) == date(2025, 1, 1)
    assert window_start(start, end, "quarter", 1) == date(2025, 4, 1)
    assert window_start(start, end, "month", 1) == date(2025, 5, 1)
    # a July fiscal year: May sits in Q4 (Apr–Jun), PTD from the previous July
    assert window_start(start, end, "ptd", 7) == date(2024, 7, 1)
    assert window_start(start, end, "quarter", 7) == date(2025, 4, 1)
    start2, end2 = date(2025, 8, 1), date(2025, 8, 31)
    assert window_start(start2, end2, "quarter", 7) == date(2025, 7, 1)
    assert window_start(start2, end2, "quarter", 1) == date(2025, 7, 1)
    # month is clipped to the fiscal year when a period straddles the year end
    assert window_start(date(2024, 12, 15), date(2025, 1, 10), "month", 1) == date(2025, 1, 1)


def test_fiscal_year_and_quarter_labels() -> None:
    assert fiscal_year_start(date(2026, 6, 30), 1) == date(2026, 1, 1)
    assert fiscal_year_start(date(2026, 6, 30), 7) == date(2025, 7, 1)
    assert fiscal_year(date(2026, 6, 30), 1) == 2026
    assert fiscal_year(date(2026, 6, 30), 7) == 2025  # labelled by the year it starts in
    assert [fiscal_quarter(date(2026, m, 1), 1) for m in range(1, 13)] == [1] * 3 + [2] * 3 + [
        3
    ] * 3 + [4] * 3
    assert fiscal_quarter(date(2026, 6, 30), 7) == 4
    assert fiscal_quarter(date(2026, 7, 1), 7) == 1


def test_column_keys_parse_to_window_and_currency_rule() -> None:
    assert pl_mapping.window_of("month_domestic") == "month"
    assert pl_mapping.window_of("quarter") == "quarter"
    assert pl_mapping.window_of("ptd_foreign") == "ptd"
    assert pl_mapping.currency_of("month_domestic") == "domestic"
    assert pl_mapping.currency_of("ptd_foreign") == "foreign"
    assert pl_mapping.currency_of("quarter") == "all"


# --- the CoA mapping ------------------------------------------------------------------------


def test_mapping_rule_normalises_a_register_row() -> None:
    assert mapping_rule(
        {"bsd7_item": " 1a ", "sign": "-1", "balance_basis": "PERIOD"}
    ) == MappingRule("1a", Decimal("-1"), PERIOD)
    assert mapping_rule({"bsd7_item": "12"}) == MappingRule("12", Decimal(1), None)
    assert mapping_rule({"bsd7_item": "12", "sign": "x"}) == MappingRule("12", Decimal(1), None)
    weekly = mapping_rule({"bsd7_item": "12", "balance_basis": "weekly"})
    assert weekly is not None and weekly.basis is None
    assert mapping_rule({"sign": "1"}) is None


def test_coa_mapping_orders_prefixes_longest_first_and_prefers_exact_codes() -> None:
    mapping = coa_mapping_from_rows(
        [
            {"gl_prefix": "4", "bsd7_item": "1a"},
            {"gl_prefix": "410", "bsd7_item": "5"},
            {"gl_account_code": "4101", "bsd7_item": "6", "sign": "-1"},
            {"gl_account_code": "", "gl_prefix": "41", "bsd7_item": "4"},
            {"gl_prefix": "9", "bsd7_item": ""},  # names no item: dropped
        ]
    )
    assert [prefix for prefix, _ in mapping.prefixes] == ["410", "41", "4"]
    assert mapping.rule_for("4101") == MappingRule("6", Decimal("-1"), None)
    assert _item(mapping.rule_for("4102")) == "5"
    assert _item(mapping.rule_for("4199")) == "4"
    assert _item(mapping.rule_for("4999")) == "1a"
    assert mapping.rule_for("5001") is None
    assert mapping.selectors_for("5") == ([], ["410"])
    assert mapping.selectors_for("6") == (["4101"], [])


def test_effective_rule_precedence_is_tag_then_register_then_declared_prefixes() -> None:
    mapping = coa_mapping_from_rows([{"gl_prefix": "40", "bsd7_item": "1a", "sign": "-1"}])
    # own tag wins, with the default sign — even over a register row
    assert effective_rule("4001", "1a", "1a", mapping, []) == MappingRule("1a", Decimal(1), None)
    # a tag for ANOTHER line deselects the account from this one
    assert effective_rule("4001", "1b", "1a", mapping, []) is None
    # no tag: the register decides (and carries its sign)
    assert effective_rule("4001", None, "1a", mapping, []) == MappingRule("1a", Decimal("-1"), None)
    assert effective_rule("4001", None, "1b", mapping, []) is None
    # the line map's declared prefixes are the last resort, default sign/basis
    assert effective_rule("4501", None, "1a", mapping, ["45"]) == MappingRule(
        "1a", Decimal(1), None
    )
    assert effective_rule("4501", "", "1a", mapping, ["46"]) is None


def test_account_rule_is_the_line_agnostic_precedence_for_a_whole_ledger() -> None:
    mapping = coa_mapping_from_rows([{"gl_prefix": "50", "bsd7_item": "12", "basis": "x"}])
    assert account_rule("5001", "2a_savings", mapping) == MappingRule(
        "2a_savings", Decimal(1), None
    )
    assert _item(account_rule("5001", None, mapping)) == "12"
    assert account_rule("6001", "", mapping) is None
    assert account_rule("6001", None, CoaMapping({}, ())) is None


# --- currency slices --------------------------------------------------------------------------


def test_currency_slices_treat_an_unstated_currency_as_base() -> None:
    assert in_currency("domestic", None, BASE) is True
    assert in_currency("domestic", BASE, BASE) is True
    assert in_currency("domestic", FOREIGN, BASE) is False
    assert in_currency("foreign", FOREIGN, BASE) is True
    assert in_currency("foreign", None, BASE) is False
    assert in_currency("all", FOREIGN, BASE) is True


# --- the arithmetic ---------------------------------------------------------------------------


def test_ytd_total_reads_the_latest_generation_per_account_in_the_slice() -> None:
    rows = [
        _gen("4001", _JAN, 100),
        _gen("4001", _FEB, 210),
        _gen("4001", _MAR, 330),
        _gen("4101", _MAR, 30, currency=FOREIGN),
        _gen("4001", date(2026, 4, 30), 9_999),  # after the bound: ignored
    ]
    assert ytd_total(rows, _MAR, in_slice=_all) == Decimal(360)
    assert ytd_total(rows, _MAR, in_slice=lambda c: in_currency("domestic", c, BASE)) == Decimal(
        330
    )
    assert ytd_total(rows, _FEB, in_slice=_all) == Decimal(210)
    assert ytd_total(rows, date(2025, 12, 31), in_slice=_all) == Decimal(0)


def test_period_total_keeps_only_the_latest_generation_per_month_h006() -> None:
    """A weekly book lands a month-to-date row mid-month: it is superseded by the
    month-end row, never added to it."""
    rows = [
        _gen("5301", _APR, 400, basis=PERIOD),
        _gen("5301", _MID_MAY, 200, basis=PERIOD),
        _gen("5301", _MAY, 550, basis=PERIOD),
    ]
    assert period_total(rows, date(2026, 5, 1), in_slice=_all) == Decimal(550)
    assert period_total(rows, date(2026, 4, 1), in_slice=_all) == Decimal(950)
    assert latest_per_month(rows, _FY, _MAY) == [rows[0], rows[2]]
    assert latest_per_month(rows, _FY, _MID_MAY) == [rows[0], rows[1]]


def test_latest_generation_respects_the_bound() -> None:
    rows = [_gen("4001", _JAN, 1), _gen("4001", _MAR, 3), _gen("4001", _FEB, 2)]
    assert latest_generation(rows, _MAR) is rows[1]
    assert latest_generation(rows, _FEB) is rows[2]
    assert latest_generation(rows, date(2025, 12, 31)) is None


def test_line_total_is_bsd7s_arithmetic() -> None:
    """The BSD7 resolver pin (Mar 300 → Apr 400 → May 550), on plain rows."""
    rows = [_gen("4001", _MAR, 300), _gen("4001", _APR, 400), _gen("4001", _MAY, 550)]
    ptd = {"window": "ptd", "lower": _FY, "upper": _MAY, "fy_start": _FY, "in_slice": _all}
    assert line_total(rows, sign=Decimal(1), **ptd) == Decimal(550)
    assert line_total(rows, sign=Decimal(-1), **ptd) == Decimal(-550)
    quarter = {**ptd, "window": "quarter", "lower": date(2026, 4, 1)}
    assert line_total(rows, sign=Decimal(1), **quarter) == Decimal(250)  # Apr–May
    month = {**ptd, "window": "month", "lower": date(2026, 5, 1)}
    assert line_total(rows, sign=Decimal(1), **month) == Decimal(150)
    foreign = {**month, "in_slice": lambda c: in_currency("foreign", c, BASE)}
    assert line_total(rows, sign=Decimal(1), **foreign) == Decimal(0)  # mapped, nothing arose
    assert line_total([], sign=Decimal(1), **ptd) is None  # no account selected


def test_line_total_period_basis_sums_months_and_ytd_basis_nets_the_prior_window() -> None:
    period_rows = [_gen("4001", _APR, 400, basis=PERIOD), _gen("4001", _MAY, 550, basis=PERIOD)]
    assert line_total(
        period_rows,
        window="quarter",
        lower=date(2026, 4, 1),
        upper=_MAY,
        fy_start=_FY,
        in_slice=_all,
        sign=Decimal(1),
    ) == Decimal(950)
    # first window of the fiscal year: nothing to net off
    assert line_total(
        [_gen("4001", _JAN, 100)],
        window="month",
        lower=_FY,
        upper=_JAN,
        fy_start=_FY,
        in_slice=_all,
        sign=Decimal(1),
    ) == Decimal(100)
    # no generation on/before the prior window's end: the split is refused, never the YTD figure
    assert (
        line_total(
            [_gen("5301", _MAR, 15)],
            window="month",
            lower=date(2026, 3, 1),
            upper=_MAR,
            fy_start=_FY,
            in_slice=_all,
            sign=Decimal(1),
        )
        is None
    )


# --- the monthly projection (bi_fact_gl_monthly) ------------------------------------------------


def test_monthly_figures_ytd_basis_are_ytd_minus_prior_month_ytd() -> None:
    rows = [_gen("4001", _JAN, 100), _gen("4001", _FEB, 210), _gen("4001", _MAR, 330)]
    march = monthly_account_figures(rows, month_end=_MAR, fy_start=_FY, basis=YTD)
    assert march is not None
    assert (march.ytd, march.prior_ytd, march.movement, march.missing_prior) == (
        Decimal(330),
        Decimal(210),
        Decimal(120),
        False,
    )
    january = monthly_account_figures(rows, month_end=_JAN, fy_start=_FY, basis=YTD)
    assert january is not None
    # the first month of the fiscal year has no prior by definition: movement = YTD
    assert (january.ytd, january.prior_ytd, january.movement, january.missing_prior) == (
        Decimal(100),
        None,
        Decimal(100),
        False,
    )


def test_monthly_figures_ytd_basis_flag_a_missing_prior_instead_of_inventing_a_month() -> None:
    """The tax charge booked only at quarter end: PTD known, the month is not."""
    rows = [_gen("5301", _MAR, 15)]
    march = monthly_account_figures(rows, month_end=_MAR, fy_start=_FY, basis=YTD)
    assert march is not None
    assert (march.ytd, march.prior_ytd, march.movement, march.missing_prior) == (
        Decimal(15),
        None,
        None,
        True,
    )
    assert monthly_account_figures(rows, month_end=_FEB, fy_start=_FY, basis=YTD) is None


def test_monthly_figures_period_basis_take_the_latest_generation_per_month_h006() -> None:
    rows = [
        _gen("5301", _MAR, 300, basis=PERIOD),
        _gen("5301", _APR, 400, basis=PERIOD),
        _gen("5301", _MID_MAY, 200, basis=PERIOD),
        _gen("5301", _MAY, 550, basis=PERIOD),
    ]
    may = monthly_account_figures(rows, month_end=_MAY, fy_start=_FY, basis=PERIOD)
    assert may is not None
    assert (may.ytd, may.prior_ytd, may.movement, may.missing_prior) == (
        Decimal(1250),
        Decimal(700),
        Decimal(550),
        False,
    )
    # mid-month build: the 15-May figure IS May's movement so far, not an addition
    mid = monthly_account_figures(rows, month_end=_MID_MAY, fy_start=_FY, basis=PERIOD)
    assert mid is not None
    assert (mid.ytd, mid.movement) == (Decimal(900), Decimal(200))
    march = monthly_account_figures(rows, month_end=_MAR, fy_start=_FY, basis=PERIOD)
    assert march is not None
    assert (march.ytd, march.prior_ytd, march.movement) == (Decimal(300), Decimal(0), Decimal(300))
    first = monthly_account_figures(
        [_gen("5301", _JAN, 40, basis=PERIOD)], month_end=_JAN, fy_start=_FY, basis=PERIOD
    )
    assert first is not None
    assert (first.ytd, first.prior_ytd, first.movement) == (Decimal(40), None, Decimal(40))


def test_monthly_figures_never_read_a_prior_fiscal_year() -> None:
    rows = [_gen("4001", date(2025, 12, 31), 9_999), _gen("4001", _JAN, 100)]
    # the caller selects [fy_start, month_end]; a prior-year row must not be passed, but if
    # it were, January is still the first month of the year and nets nothing off
    january = monthly_account_figures(rows[1:], month_end=_JAN, fy_start=_FY, basis=YTD)
    assert january is not None
    assert january.movement == Decimal(100)


@pytest.mark.parametrize("basis", [YTD, PERIOD])
def test_monthly_figures_are_none_without_a_generation_in_the_window(basis: str) -> None:
    assert monthly_account_figures([], month_end=_MAR, fy_start=_FY, basis=basis) is None


# --- the register's sign vocabulary (A5-08) -------------------------------------------------


def test_the_register_admits_only_a_normal_and_a_contra_sign() -> None:
    """Pinned against the ingestion contract's own enum, not restated by hand."""
    from app.domain.ingestion.reference_schemas.gl_mapping_bsd7 import (  # noqa: PLC0415
        SIGNS,
    )

    assert tuple(Decimal(value) for value in SIGNS) == pl_mapping.REGISTER_SIGNS


def test_mapping_rule_still_takes_the_registers_number_as_given() -> None:
    """BSD7 multiplies the register's sign into a FILED line, so the parse must
    not narrow: a value the schema forbids is carried here exactly, and refused
    only by a consumer that cannot represent it."""
    odd = mapping_rule({"bsd7_item": "16", "sign": "0.5"})
    assert odd is not None and odd.sign == Decimal("0.5")


def test_register_sign_as_int_carries_the_two_admitted_signs() -> None:
    assert pl_mapping.register_sign_as_int(Decimal(1), account_code="4001", item="1a") == 1
    assert pl_mapping.register_sign_as_int(Decimal(-1), account_code="1399", item="21") == -1


@pytest.mark.parametrize("sign", ["0.5", "0", "2", "-0.5"])
def test_register_sign_as_int_refuses_what_it_cannot_carry_exactly(sign: str) -> None:
    """``int(Decimal("0.5"))`` is 0 — the account would silently leave every line
    that sums ``pl_sign × ytd_rc`` while the return kept filing it."""
    with pytest.raises(pl_mapping.PlSignError) as excinfo:
        pl_mapping.register_sign_as_int(Decimal(sign), account_code="5301", item="16")
    message = str(excinfo.value)
    assert "5301" in message and "16" in message and sign in message
    assert pl_mapping.MAPPING_KIND in message  # names the register to correct
    assert issubclass(pl_mapping.PlSignError, ValueError)
