"""The commentary draft row: what the database refuses, and why it is ``ai_*``.

Two kinds of check here. The declaration checks pin the shape the hermetic suite
and the Playwright stack build with ``create_all`` and the migration builds in a
deployment — including the table's NAME, which is load-bearing rather than
cosmetic (see ``test_the_table_is_in_the_ai_family_not_the_bi_marts``). The
enforcement checks insert rows the application should never write and prove the
database says no, because a lifecycle invariant that lives only in a service is
one direct write away from being untrue.

This module's other-suite cases live in ``test_bi_commentary_model.py``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.bi.models.commentary import (
    AI_COMMENTARY_DRAFT_NO_OUTPUT_STATUSES,
    AI_COMMENTARY_DRAFT_TERMINAL_STATUSES,
    AiCommentaryDraft,
)
from app.core.ids import new_uuid7
from app.db.base import utc_now
from app.models import Bank
from tests.support.helpers import ORG_1, ORG_2, USER_1, USER_2

AS_OF = date(2026, 6, 30)
PRIOR = date(2026, 5, 31)
BANK_ID = "BK-COMMMOD1"


@pytest.fixture
def bank(db_session: Session) -> Bank:
    existing = db_session.get(Bank, BANK_ID)
    if existing is not None:
        return existing
    row = Bank(
        id=BANK_ID,
        organization_id=ORG_1,
        name="Commentary Model Bank",
        short_name="CommMod",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal_bank",
        institution_type="universal_bank",
    )
    db_session.add(row)
    db_session.commit()
    return row


def _row(**overrides: Any) -> AiCommentaryDraft:
    values: dict[str, Any] = {
        "id": new_uuid7(),
        "organization_id": ORG_1,
        "bank_id": BANK_ID,
        "as_of": AS_OF,
        "compare_to": PRIOR,
        "catalogue_version": "2.0.0",
        "status": "queued",
        "requested_by": USER_1,
        "payload_mode": "descriptor_only",
        "payload": {"schema": "aequoros-bi-commentary-payload-v1"},
        "payload_sha256": "a" * 64,
        "fact_sheet_hash": "b" * 64,
        "fact_bindings": {},
        "entity_keys": ["bank"],
        "fallback_paragraphs": ["The platform's own commentary."],
        "fact_count": 1,
        "prompt_version": "bi-commentary-v1",
        "prompt_sha256": "c" * 64,
        "model_requested": "test-model",
        "effort": "high",
        "max_output_tokens": 16000,
        "fallbacks_mode": "default",
        "consent_version": "ai-consent-2026-09-v1",
    }
    values.update(overrides)
    return AiCommentaryDraft(**values)


def _refuses(db: Session, row: AiCommentaryDraft) -> None:
    db.add(row)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_a_valid_row_inserts(db_session: Session, bank: Bank) -> None:
    _ = bank
    db_session.add(_row())
    db_session.flush()
    stored = db_session.scalar(sa.select(AiCommentaryDraft))
    assert stored is not None
    assert stored.status == "queued"
    assert stored.fallback_used is False
    assert stored.validation_errors == []


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"status": "not_a_status"}, id="unknown status"),
        pytest.param({"payload_mode": "everything"}, id="unknown payload mode"),
        pytest.param({"payload_sha256": "short"}, id="payload digest is not a sha256"),
        pytest.param({"fact_sheet_hash": "short"}, id="sheet hash is not a sha256"),
        pytest.param({"compare_to": AS_OF}, id="comparison is not earlier"),
        pytest.param({"fact_count": -1}, id="negative fact count"),
    ],
)
def test_the_row_refuses_a_malformed_request(
    db_session: Session, bank: Bank, overrides: dict[str, Any]
) -> None:
    _ = bank
    _refuses(db_session, _row(**overrides))


@pytest.mark.parametrize("status", AI_COMMENTARY_DRAFT_TERMINAL_STATUSES)
def test_a_finished_row_must_say_when_it_finished(
    db_session: Session, bank: Bank, status: str
) -> None:
    _ = bank
    output = {"paragraphs": []} if status == "validated" else None
    _refuses(db_session, _row(status=status, completed_at=None, output=output))


def test_a_validated_row_must_carry_the_prose_it_validated(db_session: Session, bank: Bank) -> None:
    _ = bank
    _refuses(db_session, _row(status="validated", completed_at=utc_now(), output=None))


@pytest.mark.parametrize("status", AI_COMMENTARY_DRAFT_NO_OUTPUT_STATUSES)
def test_a_refused_or_cancelled_row_can_never_carry_prose(
    db_session: Session, bank: Bank, status: str
) -> None:
    """A refusal produced nothing and a cancelled request was never sent, so
    neither may hold output whatever a caller passes."""
    _ = bank
    _refuses(
        db_session,
        _row(status=status, completed_at=utc_now(), output={"paragraphs": [{"text": "x"}]}),
    )


def test_a_second_in_flight_request_for_the_same_reader_is_refused(
    db_session: Session, bank: Bank
) -> None:
    _ = bank
    db_session.add(_row())
    db_session.flush()
    _refuses(db_session, _row())


def test_a_finished_request_does_not_block_the_next_one(db_session: Session, bank: Bank) -> None:
    """The index is PARTIAL: yesterday's validated draft must not stop today's."""
    _ = bank
    db_session.add(
        _row(status="validated", completed_at=utc_now(), output={"paragraphs": [{"text": "x"}]})
    )
    db_session.flush()
    db_session.add(_row())
    db_session.flush()
    assert db_session.scalar(sa.select(sa.func.count()).select_from(AiCommentaryDraft)) == 2


def test_another_readers_request_is_its_own(db_session: Session, bank: Bank) -> None:
    """Commentary is built from the members ONE reader could query, so the row is
    per reader — and two readers may have one in flight each."""
    _ = bank
    db_session.add(_row())
    db_session.flush()
    db_session.add(_row(requested_by=USER_2))
    db_session.flush()
    assert db_session.scalar(sa.select(sa.func.count()).select_from(AiCommentaryDraft)) == 2


def test_a_sibling_tenants_institution_is_refused(db_session: Session, bank: Bank) -> None:
    """The composite foreign key: an organisation cannot write about another's bank."""
    _ = bank
    _refuses(db_session, _row(organization_id=ORG_2))


def test_created_at_defaults_and_is_timezone_aware(db_session: Session, bank: Bank) -> None:
    _ = bank
    row = _row()
    db_session.add(row)
    db_session.flush()
    assert isinstance(row.created_at, datetime)
    assert isinstance(row.id, UUID)
