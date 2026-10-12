"""Synthetic filing inputs, owned by the reporting test package (never runtime imports)."""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from hypothesis import strategies as st

from app.reporting.schemas.filing_workflow import FilingStageInput
from app.reporting.service.bog_forms.catalog import WEEKLY_ANCHOR_WEEKDAY
from app.reporting.service.registry import ACCRA_TZ, REGISTRY, ReturnDefinition
from tests.support.generators import ReviewHistory, review_histories

OUTCOMES = ("acknowledged", "rejected", "declined")
CHECKPOINTS = ("prepared", "review", "decided", "validation", "approved", "submitted", *OUTCOMES)


def filing_checkpoints() -> st.SearchStrategy[str]:
    """Reachable prefixes of the three-officer chain; reporting/domain/workflow.py."""
    return st.sampled_from(CHECKPOINTS)


def regulator_outcomes() -> st.SearchStrategy[str]:
    """Terminal regulator decisions; reporting/service/workflow.py records these events."""
    return st.sampled_from(OUTCOMES)


def stage_titles() -> st.SearchStrategy[str]:
    """Human governance titles with schema edges (1/200 chars); FilingStageInput.title."""
    return st.one_of(
        st.sampled_from(("Preparer", "Risk review", "Finance approval", "Compliance sign-off")),
        st.sampled_from(("A", "Review " + "a" * 193)),
    )


def filing_stages(title: str) -> list[FilingStageInput]:
    """Minimal runnable chain; reporting/domain/workflow.validate_stages shape rules."""
    return [
        FilingStageInput(seq=1, stage_key="preparer", title=title, decision_kind="prepare"),
        FilingStageInput(
            seq=2,
            stage_key="validator",
            title="Validator",
            decision_kind="approve",
            transmit_on_approve=True,
        ),
    ]


@dataclass(frozen=True)
class FilingCalendarCase:
    definition: ReturnDefinition
    reporting_date: date
    due_date: date | None
    observed_at: datetime


@st.composite
def filing_calendar_cases(draw: st.DrawFn) -> FilingCalendarCase:
    """Registry cadence/deadlines, biased just before/at/after the Accra cutoff.

    Sources: reporting/service/anchors.py, registry.py (ACCRA_TZ, due_time,
    deadline_parameter) and bog_forms/catalog.py (Friday weekly convention).
    Governed deadlines remain absent: no guessed ICAAP time limit. Date-only
    rules are exercised at local midnight; this does not invent a time cutoff.
    Years bound synthetic test size, not regulatory eligibility.
    """
    definition = draw(
        st.sampled_from(tuple(entry for entry in REGISTRY.values() if not entry.event_driven))
    )
    year = draw(st.sampled_from((2024, 2025, 2026, 2028)))
    step = {"monthly": 1, "quarterly": 3, "semiannual": 6, "annual": 12}
    if definition.frequency in step:
        month = draw(
            st.sampled_from(
                tuple(range(step[definition.frequency], 13, step[definition.frequency]))
            )
        )
        reporting_date = date(year, month, monthrange(year, month)[1])
    else:
        reporting_date = draw(st.dates(date(year, 1, 1), date(year, 12, 31)))
        if definition.frequency == "weekly":
            reporting_date -= timedelta(days=(reporting_date.weekday() - WEEKLY_ANCHOR_WEEKDAY) % 7)
        elif reporting_date.weekday() >= 5:
            reporting_date -= timedelta(days=reporting_date.weekday() - 4)
    due_date = None if definition.deadline_parameter else definition.deadline_rule(reporting_date)
    cutoff = time.fromisoformat(definition.due_time) if definition.due_time else time.min
    observed_at = datetime.combine(due_date or reporting_date, cutoff, ZoneInfo(ACCRA_TZ))
    observed_at += timedelta(seconds=draw(st.sampled_from((-1, 0, 1))))
    return FilingCalendarCase(definition, reporting_date, due_date, observed_at)


def filing_review_histories() -> st.SearchStrategy[ReviewHistory]:
    """Three-officer review/return histories; reporting/domain/workflow.DEFAULT_STAGES."""
    return review_histories(review_decision="approved")
