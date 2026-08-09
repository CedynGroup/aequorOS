"""Meeting-date step curve: jumps only on MPC dates, hand-computed compounding.

Fixture assumption (documented): MPR 8.00%, an overnight spread of -100 bps,
and synthetic 2026 MPC dates. These are test fixtures, not market assertions —
the curve is a pure function of whatever schedule the desk supplies.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.domain.curves.ois_step import MeetingDateStepCurve, StepCurveError

ANCHOR = date(2026, 1, 1)
THREE_STEP = MeetingDateStepCurve(
    anchor_date=ANCHOR,
    mpr=0.08,
    meeting_dates=(date(2026, 2, 1), date(2026, 3, 1)),
    spread_bps=-100.0,
    expected_moves_bps=(25.0, -50.0),
)


class TestOvernightRate:
    def test_levels_by_window(self) -> None:
        # Window 1: 8.00% - 100bps = 7.00%; window 2: +25bps = 7.25%;
        # window 3: -50bps = 6.75%.
        assert THREE_STEP.overnight_rate(date(2026, 1, 15)) == pytest.approx(0.0700)
        assert THREE_STEP.overnight_rate(date(2026, 2, 15)) == pytest.approx(0.0725)
        assert THREE_STEP.overnight_rate(date(2026, 3, 15)) == pytest.approx(0.0675)

    def test_jump_lands_on_the_meeting_date_itself(self) -> None:
        assert THREE_STEP.overnight_rate(date(2026, 1, 31)) == pytest.approx(0.0700)
        assert THREE_STEP.overnight_rate(date(2026, 2, 1)) == pytest.approx(0.0725)

    def test_no_moves_means_flat_forever(self) -> None:
        flat = MeetingDateStepCurve(
            anchor_date=ANCHOR,
            mpr=0.08,
            meeting_dates=(date(2026, 2, 1), date(2026, 3, 1)),
            spread_bps=-170.0,
        )
        assert flat.overnight_rate(date(2026, 1, 2)) == pytest.approx(0.063)
        assert flat.overnight_rate(date(2026, 12, 31)) == pytest.approx(0.063)

    def test_before_anchor_rejected(self) -> None:
        with pytest.raises(StepCurveError):
            THREE_STEP.overnight_rate(date(2025, 12, 31))


class TestDiscountFactor:
    def test_three_step_hand_computed_example(self) -> None:
        """DF to 15 Mar 2026 compounded by hand across the three windows:
        31 nights at 7.00%, 28 nights at 7.25%, 14 nights at 6.75%."""
        growth = (
            (1.0 + 0.0700 / 365.0) ** 31
            * (1.0 + 0.0725 / 365.0) ** 28
            * (1.0 + 0.0675 / 365.0) ** 14
        )
        assert THREE_STEP.df(date(2026, 3, 15)) == pytest.approx(1.0 / growth, abs=1e-14)

    def test_df_at_anchor_is_one(self) -> None:
        assert THREE_STEP.df(ANCHOR) == 1.0

    def test_daily_ratio_jumps_only_across_meeting_dates(self) -> None:
        """DF(d)/DF(d+1) = 1 + r/365: constant inside windows, changes only
        when the night crosses a meeting date."""
        previous_ratio: float | None = None
        day = ANCHOR
        jump_days: list[date] = []
        while day < date(2026, 4, 1):
            ratio = THREE_STEP.df(day) / THREE_STEP.df(day + timedelta(days=1))
            if previous_ratio is not None and abs(ratio - previous_ratio) > 1e-15:
                jump_days.append(day)
            previous_ratio = ratio
            day += timedelta(days=1)
        assert jump_days == [date(2026, 2, 1), date(2026, 3, 1)]

    def test_df_between_is_ratio(self) -> None:
        start, end = date(2026, 1, 10), date(2026, 3, 10)
        assert THREE_STEP.df_between(start, end) == pytest.approx(
            THREE_STEP.df(end) / THREE_STEP.df(start), abs=1e-15
        )

    def test_compounded_rate_single_window(self) -> None:
        # Entirely inside window 1: ((1 + 0.07/365)^10 - 1) * 365/10.
        start, end = date(2026, 1, 5), date(2026, 1, 15)
        expected = ((1.0 + 0.07 / 365.0) ** 10 - 1.0) * 365.0 / 10.0
        assert THREE_STEP.compounded_rate(start, end) == pytest.approx(expected, abs=1e-14)

    def test_compounded_rate_requires_positive_span(self) -> None:
        with pytest.raises(StepCurveError):
            THREE_STEP.compounded_rate(ANCHOR, ANCHOR)


class TestValidation:
    def test_unsorted_meetings_rejected(self) -> None:
        with pytest.raises(StepCurveError):
            MeetingDateStepCurve(
                anchor_date=ANCHOR,
                mpr=0.08,
                meeting_dates=(date(2026, 3, 1), date(2026, 2, 1)),
            )

    def test_misaligned_moves_rejected(self) -> None:
        with pytest.raises(StepCurveError):
            MeetingDateStepCurve(
                anchor_date=ANCHOR,
                mpr=0.08,
                meeting_dates=(date(2026, 2, 1), date(2026, 3, 1)),
                expected_moves_bps=(25.0,),
            )
