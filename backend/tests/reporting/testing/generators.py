"""Synthetic filing inputs, owned by the reporting test package (never runtime imports)."""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from hypothesis import strategies as st

from app.core.authorization import (
    MACHINE_ROLE_BUNDLES,
    ModuleScope,
    RoleBundle,
    SensitivityScope,
)
from app.reporting.schemas.filing_workflow import FilingStageInput
from app.reporting.service.bog_forms.catalog import WEEKLY_ANCHOR_WEEKDAY
from app.reporting.service.registry import ACCRA_TZ, REGISTRY, ReturnDefinition
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID
from tests.support.generators import ReviewHistory, review_histories

#: A SECOND bank of the demo organization: institution narrowing has to be
#: exercised against a sibling, because two banks of one tenant share an
#: organization-wide row but never each other's.
SIBLING_BANK_ID = "BK-EXAM0002"

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


@dataclass(frozen=True)
class EvidenceBinding:
    """One declared authority sentence for the examiner evidence surfaces.

    Shape sources: ``core/authorization.ROLE_PERMISSIONS`` (fixed bundles),
    ``identity/service/authorization.create_role_binding`` (the dimensions a row
    stores) and ``docs/authorization_foundation.md`` §Fixed role bundles.
    """

    bundle: RoleBundle
    module: ModuleScope = ModuleScope.AUDIT
    sensitivity: SensitivityScope = SensitivityScope.RESTRICTED
    #: ``None`` is an organization-wide row (it covers every institution of the
    #: organization); a bank id narrows it to exactly that institution.
    institution_id: str | None = None
    #: Validity, not a stored dimension: a row outside its window or with a
    #: non-active status is present and must still deny.
    lifecycle: Literal["active", "future", "expired", "suspended", "revoked"] = "active"


@st.composite
def evidence_bindings(draw: st.DrawFn) -> EvidenceBinding:
    """Legal HUMAN grant shapes, including the ones that must never allow.

    Machine bundles are excluded because a human principal cannot hold one (the
    service and the database both refuse it); generating one would fabricate an
    impossible row rather than exercise a denial. MEMBER keeps its documented
    permanent organization-wide Account/restricted shape, and the two audit
    officer bundles keep whole-institution Audit coverage.
    """

    bundle = draw(
        st.sampled_from(
            tuple(bundle for bundle in RoleBundle if bundle not in MACHINE_ROLE_BUNDLES)
        )
    )
    module = draw(
        st.sampled_from(
            (
                ModuleScope.AUDIT,
                ModuleScope.AUDIT,
                ModuleScope.ALL,
                ModuleScope.LIQUIDITY,
                ModuleScope.CAPITAL,
                ModuleScope.REGULATORY,
                ModuleScope.CREDIT,
            )
        )
    )
    sensitivity = draw(
        st.sampled_from(
            (
                SensitivityScope.RESTRICTED,
                SensitivityScope.RESTRICTED,
                SensitivityScope.CONFIDENTIAL,
                SensitivityScope.AGGREGATED,
                SensitivityScope.ALL,
            )
        )
    )
    institution_id = draw(st.sampled_from((None, None, SAMPLE_BANK_ID, SIBLING_BANK_ID)))
    lifecycle = draw(
        st.sampled_from(("active", "active", "future", "expired", "suspended", "revoked"))
    )
    if bundle is RoleBundle.MEMBER:
        module, sensitivity = ModuleScope.ACCOUNT, SensitivityScope.RESTRICTED
        institution_id, lifecycle = None, "active"
    elif bundle in (RoleBundle.SECURITY_OFFICER, RoleBundle.COMPLIANCE_OFFICER):
        module = ModuleScope.AUDIT
    return EvidenceBinding(bundle, module, sensitivity, institution_id, lifecycle)


def evidence_binding_sets() -> st.SearchStrategy[list[EvidenceBinding]]:
    """Empty, complete, partial and split grants — rows never compose authority."""

    return st.one_of(
        st.lists(evidence_bindings(), max_size=4),
        st.lists(
            st.builds(
                EvidenceBinding,
                bundle=st.sampled_from((RoleBundle.VIEWER, RoleBundle.AUDITOR, RoleBundle.ANALYST)),
                module=st.sampled_from((ModuleScope.AUDIT, ModuleScope.LIQUIDITY)),
                sensitivity=st.sampled_from(tuple(SensitivityScope)),
            ),
            min_size=1,
            max_size=3,
        ),
    )
