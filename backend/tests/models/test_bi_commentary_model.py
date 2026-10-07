"""The commentary draft row: what the database refuses, and why it is ``ai_*``.

Two kinds of check here. The declaration checks pin the shape the hermetic suite
and the Playwright stack build with ``create_all`` and the migration builds in a
deployment — including the table's NAME, which is load-bearing rather than
cosmetic (see ``test_the_table_is_in_the_ai_family_not_the_bi_marts``). The
enforcement checks insert rows the application should never write and prove the
database says no, because a lifecycle invariant that lives only in a service is
one direct write away from being untrue.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, cast
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.ids import new_uuid7
from app.db.base import Base, utc_now
from app.models import Bank
from app.models.bi_commentary import (
    AI_COMMENTARY_DRAFT_NO_OUTPUT_STATUSES,
    AI_COMMENTARY_DRAFT_STATUSES,
    AI_COMMENTARY_DRAFT_TERMINAL_STATUSES,
    AI_COMMENTARY_PAYLOAD_MODES,
    AiCommentaryDraft,
)
from tests.support.helpers import ORG_1, ORG_2, USER_1, USER_2

AS_OF = date(2026, 6, 30)
PRIOR = date(2026, 5, 31)
BANK_ID = "BK-COMMMOD1"
#: The declared table. ``__table__`` is typed as a ``FromClause``; the checks
#: below read constraints and indexes, which belong to ``Table``.
TABLE = cast("sa.Table", AiCommentaryDraft.__table__)


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


# --- the declaration ---------------------------------------------------------


def test_the_table_is_in_the_ai_family_not_the_bi_marts() -> None:
    """The name is a boundary, not a label.

    ``tests/architecture/test_bi_plane_boundary.py`` derives the set of tables BI
    may write from the ``bi_``-prefixed tables of ``Base.metadata``, and separately
    asserts that set is exactly what ``app.models.bi`` declares. A ``bi_``-named
    table declared in this module would break that derivation — and it would be
    wrong anyway: this row is the record of what left the platform for an external
    model, so it belongs beside the consent row that gates it, and nothing under
    ``app/services/bi`` may write it.
    """
    assert AiCommentaryDraft.__tablename__ == "ai_commentary_drafts"
    assert not AiCommentaryDraft.__tablename__.startswith("bi_")
    assert "ai_commentary_settings" in Base.metadata.tables
    assert AiCommentaryDraft.__tablename__ in Base.metadata.tables


def test_the_row_is_tenant_scoped_with_the_bank_foreign_key() -> None:
    columns = {column.name for column in TABLE.columns}
    assert {"organization_id", "bank_id"} <= columns
    composite = [
        constraint
        for constraint in TABLE.constraints
        if isinstance(constraint, sa.ForeignKeyConstraint)
        and {element.parent.name for element in constraint.elements}
        == {"bank_id", "organization_id"}
    ]
    assert composite, "a bank reference is always (bank_id, organization_id)"


def test_json_columns_are_plain_json_never_jsonb() -> None:
    """``create_all`` builds this schema on SQLite for the hermetic suite."""
    for column in TABLE.columns:
        assert not isinstance(column.type, JSONB)
        if column.name in {
            "payload",
            "fact_bindings",
            "entity_keys",
            "fallback_paragraphs",
            "validation_errors",
            "output",
            "usage",
        }:
            assert isinstance(column.type, sa.JSON)


def test_the_check_constraints_derive_from_the_vocabulary_tuples() -> None:
    """The model and the database can never disagree about a vocabulary."""
    rendered = {
        constraint.name: str(constraint.sqltext)
        for constraint in TABLE.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    status_check = rendered["ck_ai_commentary_drafts_status"]
    for status in AI_COMMENTARY_DRAFT_STATUSES:
        assert f"'{status}'" in status_check
    mode_check = rendered["ck_ai_commentary_drafts_mode"]
    for mode in AI_COMMENTARY_PAYLOAD_MODES:
        assert f"'{mode}'" in mode_check
    no_output = rendered["ck_ai_commentary_drafts_no_output"]
    for status in AI_COMMENTARY_DRAFT_NO_OUTPUT_STATUSES:
        assert f"'{status}'" in no_output


def test_the_vocabularies_are_the_lifecycle_the_handler_implements() -> None:
    assert set(AI_COMMENTARY_DRAFT_TERMINAL_STATUSES) < set(AI_COMMENTARY_DRAFT_STATUSES)
    assert set(AI_COMMENTARY_DRAFT_NO_OUTPUT_STATUSES) <= set(AI_COMMENTARY_DRAFT_TERMINAL_STATUSES)
    # Only these two are in flight; everything else is finished.
    assert set(AI_COMMENTARY_DRAFT_STATUSES) - set(AI_COMMENTARY_DRAFT_TERMINAL_STATUSES) == {
        "queued",
        "running",
    }
    assert AI_COMMENTARY_PAYLOAD_MODES == ("standard", "descriptor_only")


def test_one_in_flight_request_per_reader_institution_and_date() -> None:
    """The debounce's race guard, as a partial unique index."""
    index = next(
        index for index in TABLE.indexes if index.name == "uq_ai_commentary_drafts_inflight"
    )
    assert index.unique
    assert [column.name for column in index.columns] == [
        "organization_id",
        "bank_id",
        "as_of",
        "requested_by",
    ]
    # Partial on both engines, and on the SAME predicate: the hermetic suite runs
    # on SQLite and a deployment on Postgres, so a predicate on only one of them
    # would make the guard true in tests and absent in production.
    for dialect in ("sqlite", "postgresql"):
        predicate = str(index.dialect_kwargs[f"{dialect}_where"])
        assert "queued" in predicate
        assert "running" in predicate


# --- what the database refuses ----------------------------------------------


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
