"""Shared setup for the P3 filing tests: a complete framework, and a full cycle.

Ghana's instrument is 15/17 sections ``pending_primary_text`` (D-006), so a
non-rehearsal freeze against it is impossible by design and would give the
filing path no end-to-end proof at all. These tests therefore run against the
committed test instrument under ``tests/fixtures/icaap/frameworks``, published
through ``ICAAP_EXTRA_FRAMEWORKS_DIR`` — the same mechanism a developer uses,
and one that is refused outside ``local``/``test`` (pinned by its own test).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess, TenantContext
from app.core.config import get_settings
from app.domain.icaap.frameworks import registry
from app.models import Bank, User
from app.models.icaap import IcaapAttachment, IcaapCycle
from app.schemas.icaap import (
    IcaapCycleCreate,
    IcaapSectionCommit,
    IcaapSectionWorkingSave,
    IcaapStageDecisionCreate,
    IcaapSubmitForReview,
)
from app.services.icaap import guards, sections, workflow
from tests.api.helpers import ORG_1, USER_1
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID

FRAMEWORK_ROOT = Path(__file__).parents[2] / "fixtures" / "icaap" / "frameworks"
TEST_FRAMEWORK = ("test_icaap", "e2e.1")
#: The canonical book runs 2025-04 .. 2026-03, so FY2025 has a 31 December
#: period end and a real annual cycle can bind figures to it.
FY = 2025
AS_OF = date(2025, 12, 31)


@pytest.fixture
def extra_frameworks(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Publish the test instrument beside Ghana's, the way a deployment would."""
    monkeypatch.setenv("ICAAP_EXTRA_FRAMEWORKS_DIR", str(FRAMEWORK_ROOT))
    monkeypatch.setenv("ICAAP_FRAMEWORKS_ENABLED", "bog_icaap,test_icaap")
    get_settings.cache_clear()
    registry.set_extra_roots((FRAMEWORK_ROOT,))
    yield
    registry.set_extra_roots(())
    get_settings.cache_clear()


def access_for(db: Session, user_id: UUID = USER_1) -> IcaapAccess:
    bank = db.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    return IcaapAccess(
        ctx=TenantContext(organization_id=ORG_1, actor_user_id=user_id, authorization_version=1),
        bank=bank,
    )


def make_user(db: Session, *, email: str, name: str, job_title: str | None = None) -> UUID:
    """A second officer, so maker and checker can genuinely be different people."""
    user = User(
        id=uuid4(),
        organization_id=ORG_1,
        email=email,
        display_name=name,
        job_title=job_title,
        password_hash="x",
        role="analyst",
        is_active=True,
    )
    db.add(user)
    db.flush()
    db.commit()
    return user.id


def annual_payload(**overrides: object) -> IcaapCycleCreate:
    payload: dict[str, object] = {
        "fiscal_year": FY,
        "cycle_kind": "annual",
        "basis": "solo",
        "framework_code": TEST_FRAMEWORK[0],
        "framework_version": TEST_FRAMEWORK[1],
        "reason": "The annual ICAAP for the financial year.",
    }
    payload.update(overrides)
    return IcaapCycleCreate.model_validate(payload)


def _paragraph(text: str) -> dict[str, object]:
    return {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }


def write_every_section(db: Session, access: IcaapAccess, cycle_id: UUID) -> None:
    """Commit text in every section, so the report says something everywhere."""
    listing = sections.list_sections(db, access, cycle_id)
    for summary in listing.sections:
        sections.save_working(
            db,
            access,
            cycle_id,
            summary.key,
            IcaapSectionWorkingSave(
                doc=_paragraph(f"The institution's assessment for {summary.title}."),
                base_rev=0,
            ),
        )
        sections.commit_version(
            db, access, cycle_id, summary.key, IcaapSectionCommit(base_rev=1, note="First version")
        )


def answer_every_requirement(db: Session, access: IcaapAccess, cycle_id: UUID) -> None:
    from app.schemas.icaap import IcaapRequirementStateUpdate  # noqa: PLC0415 - local to the helper

    cycle = guards.get_cycle_or_404(db, access, cycle_id)
    framework = guards.require_framework(cycle)
    for section in framework.sections:
        for item in section.requirements:
            sections.set_requirement_state(
                db,
                access,
                cycle_id,
                section.key,
                item.id,
                IcaapRequirementStateUpdate(status="met", reason=None),
            )


def attach(db: Session, access: IcaapAccess, cycle_id: UUID, kind: str, *, sha: str) -> UUID:
    """A freeze-gate document, written straight to the table.

    The upload path is P1's and has its own tests; what P3 needs is the ROW,
    without standing up object storage for a workflow test.
    """
    cycle = db.get(IcaapCycle, cycle_id)
    assert cycle is not None
    row = IcaapAttachment(
        organization_id=cycle.organization_id,
        bank_id=cycle.bank_id,
        cycle_id=cycle.id,
        kind=kind,
        title=kind.replace("_", " ").capitalize(),
        original_filename=f"{kind}.pdf",
        media_type="application/pdf",
        byte_size=1024,
        sha256=sha,
        storage_tier="outputs",
        object_path=f"icaap/{cycle.fiscal_year}/{cycle.id}/{sha}.pdf",
        attributes={"commenting_functions": ["risk", "finance"]},
        uploaded_by=guards.actor_id(access),
    )
    db.add(row)
    db.flush()
    db.commit()
    return row.id


def submit(db: Session, access: IcaapAccess, cycle_id: UUID) -> None:
    digest = workflow.get_stages(db, access, cycle_id).review_digest
    workflow.submit_for_review(db, access, cycle_id, IcaapSubmitForReview(review_digest=digest))


def decide(  # noqa: PLR0913 - a decision is addressed by its named parts
    db: Session,
    access: IcaapAccess,
    cycle_id: UUID,
    seq: int,
    decision: str,
    *,
    comment: str | None = None,
    return_to_seq: int | None = None,
) -> None:
    stages = workflow.get_stages(db, access, cycle_id)
    workflow.decide_stage(
        db,
        access,
        cycle_id,
        seq,
        IcaapStageDecisionCreate(
            decision=decision,  # pyright: ignore[reportArgumentType]
            round=stages.round,
            review_digest=stages.review_digest,
            return_to_seq=return_to_seq,
            comment=comment,
        ),
    )


def return_payload(
    db: Session, access: IcaapAccess, cycle_id: UUID, *, return_to_seq: int, reason: str
):  # noqa: ANN202 - the schema is imported locally to keep this module's imports flat
    """A post-freeze send-back addressed to the round and text it is about.

    ``round`` and ``review_digest`` are required on the payload (audit F1), the
    same way they are on a stage decision: a send-back that voids the Board's
    signatures must not be takeable from a stale page. Read here rather than
    typed into every test, so a test says WHAT it sends back, not which round it
    happened to be.
    """
    from app.schemas.icaap import IcaapReturnCreate  # noqa: PLC0415 - local to the helper

    stages = workflow.get_stages(db, access, cycle_id)
    return IcaapReturnCreate(
        return_to_seq=return_to_seq,
        round=stages.round,
        review_digest=stages.review_digest,
        reason=reason,
    )


__all__ = [
    "AS_OF",
    "FRAMEWORK_ROOT",
    "FY",
    "TEST_FRAMEWORK",
    "access_for",
    "annual_payload",
    "answer_every_requirement",
    "attach",
    "decide",
    "extra_frameworks",
    "govern_first_as_of",
    "make_p2_ready",
    "seal_capital_run",
    "make_user",
    "return_payload",
    "submit",
    "write_every_section",
]


def seal_capital_run(db: Session, access: IcaapAccess) -> None:
    """A real sealed baseline capital run for the assessment date.

    The blocks bind to a SEALED run and refuse when there is none, so the proof
    runs the engine rather than fabricating a run row: a filing built on an
    invented run would be exactly what the withdrawn-evidence gate exists to
    stop.
    """
    from sqlalchemy import select as _select  # noqa: PLC0415

    from app.models import BankReportingPeriod  # noqa: PLC0415
    from app.schemas.regulatory_liquidity import RegulatoryRunCreate  # noqa: PLC0415
    from app.services import regulatory_capital  # noqa: PLC0415

    period = db.scalar(
        _select(BankReportingPeriod).where(
            BankReportingPeriod.bank_id == access.bank.id,
            BankReportingPeriod.period_end == AS_OF,
        )
    )
    assert period is not None, "the canonical book has a 31 December period"
    regulatory_capital.create_capital_run(
        db,
        access.ctx,
        access.bank.id,
        RegulatoryRunCreate(
            module="capital", reporting_period_id=period.id, scenario_code="baseline"
        ),
    )
    db.commit()


def make_p2_ready(db: Session, access: IcaapAccess, cycle_id: UUID) -> None:
    """Answer the risk-and-capital half of readiness.

    P2 owns these rules and tests them; P3 needs them SATISFIED so the freeze
    proof exercises the freeze rather than re-proving P2's checklist. Every
    step here goes through P2's own service, so a change to what P2 requires
    breaks this helper loudly rather than leaving a freeze test passing on a
    cycle P2 would refuse.
    """
    from datetime import date as _date  # noqa: PLC0415

    from app.schemas.icaap import IcaapDataBlockCreate  # noqa: PLC0415
    from app.schemas.icaap_risk_capital import (  # noqa: PLC0415
        IcaapChallengeCreate,
        IcaapChallengeResponseCreate,
        IcaapReason,
        IcaapResourcesLineCreate,
        IcaapRiskPut,
    )
    from app.services.icaap import blocks, challenges, reconciliation, risks  # noqa: PLC0415

    # The reconciliation needs the institution's own Pillar 1 figures, bound
    # from a sealed run — it refuses rather than assuming a denominator.
    for block_type in ("capital_position", "pillar1_rwa"):
        blocks.create_block(db, access, cycle_id, IcaapDataBlockCreate(block_type=block_type))
    cycle = guards.get_cycle_or_404(db, access, cycle_id)
    framework = guards.require_framework(cycle)
    for category in framework.risk_categories:
        risks.put_risk(
            db,
            access,
            cycle_id,
            category.key,
            IcaapRiskPut(
                likelihood_score=1,
                impact_score=1,
                materiality_rationale="Small, well collateralised and immaterial.",
                reason="Score the risk.",
            ),
        )
    reconciliation.compute_requirement(
        db, access, cycle_id, IcaapReason(reason="Compute the requirement.")
    )
    reconciliation.create_resources_line(
        db,
        access,
        cycle_id,
        IcaapResourcesLineCreate(
            line_key="cet1_paid_up_capital",
            label="Paid-up ordinary share capital",
            tier="cet1",
            internal_amount=Decimal("1"),
            regulatory_amount=Decimal("1"),
            regulatory_eligible=True,
            reason="List the capital the institution holds.",
        ),
    )
    raised = challenges.raise_challenge(
        db,
        access,
        cycle_id,
        IcaapChallengeCreate(
            raised_in="board_risk_committee",
            raised_by_name="Committee chair",
            raised_on=_date(cycle.as_of_date.year, 1, 20),
            target_kind="cycle",
            challenge_text="Is the capital assessment consistent with the strategy?",
            severity="medium",
        ),
    )
    challenges.respond(
        db,
        access,
        cycle_id,
        raised.id,
        IcaapChallengeResponseCreate(
            outcome="accepted_no_change",
            response_text="The assessment was prepared against the approved strategy.",
            responder_function="Chief Risk Officer",
        ),
    )
    # The reconciliation BLOCK, bound after the figures above exist. Without it
    # the report prints "Not available" for the total internal capital
    # requirement, the available internal capital and the coverage — and until
    # 2026-09-20 nothing said so, because the validation rule returned early
    # when the block was absent (independent audit F2). A fixture that calls
    # itself complete has to carry the figures a complete report carries.
    blocks.create_block(
        db, access, cycle_id, IcaapDataBlockCreate(block_type="capital_reconciliation")
    )


def govern_first_as_of(db: Session, as_of: date) -> None:
    """Set the governed commencement date, the way staff would in the console.

    The date an ICAAP report becomes filable is a control-plane row (D-032),
    seeded ``pending`` at 2026-12-31. A test that needs to freeze a FY2025
    assessment therefore changes the ROW rather than the code — which is also a
    proof that the console change takes effect without a release.
    """
    from app.models import RegulatoryParameter  # noqa: PLC0415
    from app.services.icaap.freeze import FIRST_AS_OF_PARAM  # noqa: PLC0415

    rows = db.query(RegulatoryParameter).filter(RegulatoryParameter.param_code == FIRST_AS_OF_PARAM)
    for row in rows:
        row.value_json = {"schema": "icaap-effective-date-v1", "date": as_of.isoformat()}
    db.flush()
    db.commit()
