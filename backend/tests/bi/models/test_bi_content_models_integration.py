"""The four tenant-authored BI tables: their vocabularies and their constraints.

Two kinds of test, and the second kind is the point of the file. The first is
parity: every vocabulary in ``app/bi/models/content.py`` is a deliberate mirror
of a source of truth elsewhere (the catalogue's value types and directions, the
formula language's own length cap, the role bundles), because ``app.models`` does
not import ``app.bi.domain``; a mirror nobody checks is a mirror that drifts.

The second is that the promotion rules hold in the DATABASE. Maker-checker for a
calculated measure is enforced by the service, audited, and expressed through the
platform's separation-of-duties machinery — and it is ALSO a CHECK constraint, so
a self-approved or half-approved row cannot be stored even if a future code path
is wrong about it. SQLite enforces CHECK constraints, so these run hermetically
and prove the constraint rather than the model's opinion of it.

This module's other-suite cases live in ``test_bi_content_models.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.bi.models.content import (
    BiDashboard,
    BiDashboardShare,
    BiDashboardVersion,
    BiMeasure,
)
from app.models import Bank
from tests.support.helpers import ORG_1, USER_1

BANK_ID = "BK-BICONT01"
NOW = datetime(2026, 9, 27, 12, tzinfo=UTC)
DIGEST = "a" * 64
OTHER_DIGEST = "b" * 64


@pytest.fixture
def bank(db_session: Session) -> Bank:
    existing = db_session.get(Bank, BANK_ID)
    if existing is not None:
        return existing
    row = Bank(
        id=BANK_ID,
        organization_id=ORG_1,
        name="BI content bank",
        short_name="BI content",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal_bank",
        institution_type="universal_bank",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _measure(**overrides: Any) -> BiMeasure:
    values: dict[str, Any] = {
        "organization_id": ORG_1,
        "bank_id": BANK_ID,
        "measure_key": "custom.cost_to_income",
        "owner_user_id": USER_1,
        "label": "Cost to income",
        "description": "",
        "expression": "[m:loans.balance_rc]",
        "expression_digest": DIGEST,
        "referenced_members": ["loans.balance_rc"],
        "value_type": "pct",
        "favourable_direction": "lower_better",
        "state": "personal",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return BiMeasure(**values)


def test_the_database_refuses_a_measure_its_proposer_approved(
    db_session: Session, bank: Bank
) -> None:
    """Maker-checker for a promotion holds where no code path can be wrong about it.

    The service refuses this through the platform's own separation-of-duties
    machinery and audits the decision. This is the second statement of the same
    rule: a row whose approver is its proposer cannot be stored at all.
    """

    _ = bank
    db_session.add(
        _measure(
            state="bank_certified",
            proposed_by_user_id=USER_1,
            proposed_at=NOW,
            proposed_expression_digest=DIGEST,
            proposal_reason="Board pack ratio",
            approved_by_user_id=USER_1,
            approved_at=NOW,
            approved_expression="[m:loans.balance_rc]",
            approved_expression_digest=DIGEST,
            approval_reason="Looks right to me",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_the_database_refuses_a_certification_that_is_not_the_current_formula(
    db_session: Session, bank: Bank
) -> None:
    """An approver approved a FORMULA: a certified row must hold that same formula.

    Which is what makes "an edit drops the certification" an invariant rather than
    a convention — the alternative row is unstorable.
    """

    _ = bank
    db_session.add(
        _measure(
            expression_digest=OTHER_DIGEST,
            state="bank_certified",
            proposed_by_user_id=USER_1,
            proposed_at=NOW,
            proposed_expression_digest=DIGEST,
            proposal_reason="Board pack ratio",
            approved_by_user_id=uuid4(),
            approved_at=NOW,
            approved_expression="[m:loans.balance_rc]",
            approved_expression_digest=DIGEST,
            approval_reason="Reviewed against the register",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_the_database_refuses_a_certified_measure_with_no_approver(
    db_session: Session, bank: Bank
) -> None:
    """A self-certification by omission is still a self-certification."""

    _ = bank
    db_session.add(
        _measure(
            state="bank_certified",
            proposed_by_user_id=USER_1,
            proposed_at=NOW,
            proposed_expression_digest=DIGEST,
            proposal_reason="Board pack ratio",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_the_database_refuses_an_approval_on_a_measure_that_is_not_certified(
    db_session: Session, bank: Bank
) -> None:
    """An approval recorded against a personal measure is an approval of nothing."""

    _ = bank
    db_session.add(
        _measure(
            state="personal",
            approved_by_user_id=uuid4(),
            approved_at=NOW,
            approved_expression="[m:loans.balance_rc]",
            approved_expression_digest=DIGEST,
            approval_reason="Reviewed",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_the_database_refuses_a_proposal_with_no_proposer(db_session: Session, bank: Bank) -> None:
    """A half-recorded proposal is a promotion nobody can be held to."""

    _ = bank
    db_session.add(_measure(state="proposed"))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_a_complete_promotion_stores(db_session: Session, bank: Bank) -> None:
    """The one shape the constraints admit: two people, one frozen formula."""

    _ = bank
    approver = uuid4()
    db_session.add(
        _measure(
            state="bank_certified",
            proposed_by_user_id=USER_1,
            proposed_at=NOW,
            proposed_expression_digest=DIGEST,
            proposal_reason="Board pack ratio",
            approved_by_user_id=approver,
            approved_at=NOW,
            approved_expression="[m:loans.balance_rc]",
            approved_expression_digest=DIGEST,
            approval_reason="Reviewed against the register",
        )
    )
    db_session.flush()
    stored = db_session.scalars(
        BiMeasure.__table__.select().with_only_columns(BiMeasure.__table__.c.approved_by_user_id)
    ).all()
    assert approver in stored


def test_a_role_visibility_without_a_role_is_refused(db_session: Session, bank: Bank) -> None:
    """A dashboard shared "by role" with no role named reaches nobody but its owner.

    Storing it would be a dashboard whose sharing setting says one thing and whose
    behaviour says another.
    """

    _ = bank
    db_session.add(
        BiDashboard(
            organization_id=ORG_1,
            bank_id=BANK_ID,
            owner_user_id=USER_1,
            title="Funding",
            description="",
            visibility="role",
            visibility_role=None,
            badge="personal",
            current_version=1,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_deleting_a_dashboard_takes_its_versions_and_shares(
    db_session: Session, bank: Bank
) -> None:
    """Only the owner may delete, and deleting is a complete act.

    The history is append-only against UPDATE, not against the parent's CASCADE:
    a dashboard's history cannot be REWRITTEN, and the only way a version goes
    away is with the document it belongs to.
    """

    _ = bank
    dashboard = BiDashboard(
        organization_id=ORG_1,
        bank_id=BANK_ID,
        owner_user_id=USER_1,
        title="Funding",
        description="",
        visibility="users",
        visibility_role=None,
        badge="personal",
        current_version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(dashboard)
    db_session.flush()
    db_session.add_all(
        [
            BiDashboardVersion(
                organization_id=ORG_1,
                bank_id=BANK_ID,
                dashboard_id=dashboard.id,
                version=1,
                title="Funding",
                description="",
                spec={"widgets": [], "layout": []},
                spec_digest=DIGEST,
                change_note="",
                created_by_user_id=USER_1,
                created_at=NOW,
            ),
            BiDashboardShare(
                organization_id=ORG_1,
                bank_id=BANK_ID,
                dashboard_id=dashboard.id,
                grantee_user_id=USER_1,
                shared_by_user_id=USER_1,
                created_at=NOW,
            ),
        ]
    )
    db_session.flush()
    db_session.delete(dashboard)
    db_session.flush()
    remaining_versions = db_session.scalars(
        BiDashboardVersion.__table__.select().with_only_columns(BiDashboardVersion.__table__.c.id)
    ).all()
    remaining_shares = db_session.scalars(
        BiDashboardShare.__table__.select().with_only_columns(BiDashboardShare.__table__.c.id)
    ).all()
    assert remaining_versions == []
    assert remaining_shares == []
