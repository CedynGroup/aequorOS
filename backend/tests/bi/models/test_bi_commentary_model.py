"""The commentary draft row: what the database refuses, and why it is ``ai_*``.

Two kinds of check here. The declaration checks pin the shape the hermetic suite
and the Playwright stack build with ``create_all`` and the migration builds in a
deployment — including the table's NAME, which is load-bearing rather than
cosmetic (see ``test_the_table_is_in_the_ai_family_not_the_bi_marts``). The
enforcement checks insert rows the application should never write and prove the
database says no, because a lifecycle invariant that lives only in a service is
one direct write away from being untrue.

This module's other-suite cases live in ``test_bi_commentary_model_integration.py``.
"""

from __future__ import annotations

from typing import cast

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from app.bi.models.commentary import (
    AI_COMMENTARY_DRAFT_NO_OUTPUT_STATUSES,
    AI_COMMENTARY_DRAFT_STATUSES,
    AI_COMMENTARY_DRAFT_TERMINAL_STATUSES,
    AI_COMMENTARY_PAYLOAD_MODES,
    AiCommentaryDraft,
)
from app.db.base import Base

#: The declared table. ``__table__`` is typed as a ``FromClause``; the checks
#: below read constraints and indexes, which belong to ``Table``.
TABLE = cast("sa.Table", AiCommentaryDraft.__table__)


def test_the_table_is_in_the_ai_family_not_the_bi_marts() -> None:
    """The name is a boundary, not a label.

    ``tests/architecture/test_bi_plane_boundary.py`` derives the set of tables BI
    may write from the ``bi_``-prefixed tables of ``Base.metadata``, and separately
    asserts that set is exactly what ``app.bi.models.marts`` declares. A ``bi_``-named
    table declared in this module would break that derivation — and it would be
    wrong anyway: this row is the record of what left the platform for an external
    model, so it belongs beside the consent row that gates it, and nothing under
    ``app/bi/service`` may write it.
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
