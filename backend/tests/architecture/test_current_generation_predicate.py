"""Current-generation predicates admit active rows and allow replacement of retired rows."""

from __future__ import annotations

import sqlite3
from importlib import import_module
from typing import cast

import pytest
from sqlalchemy import Table
from sqlalchemy.sql.elements import ClauseElement

from app.db.base import Base
from app.models.canonical_withdrawal import WITHDRAWABLE_ENTITIES
from tests.support.database import isolated_database

import_module("app.models")


def _partial_predicates() -> list[tuple[str, str, str]]:
    predicates: list[tuple[str, str, str]] = []
    for mapper in Base.registry.mappers:
        table = mapper.local_table
        if not isinstance(table, Table) or not table.name.startswith("canonical_"):
            continue
        if not {"superseded_by", "withdrawn_at"} <= set(table.columns.keys()):
            continue
        for index in table.indexes:
            for dialect in ("sqlite", "postgresql"):
                predicate = cast(ClauseElement | None, index.dialect_options[dialect].get("where"))
                if predicate is not None:
                    predicates.append((str(index.name), dialect, str(predicate.compile())))
    assert predicates
    return sorted(predicates)


@pytest.mark.parametrize(("index", "dialect", "predicate"), _partial_predicates())
def test_partial_indexes_allow_replacement_of_retired_rows(
    index: str, dialect: str, predicate: str
) -> None:
    with isolated_database("sqlite://") as owner, owner.sqlite_connection() as db:
        db.execute(
            "CREATE TABLE versions (id INTEGER, business_key TEXT, "
            "superseded_by TEXT, withdrawn_at TEXT)"
        )
        db.execute(f"CREATE UNIQUE INDEX current_key ON versions(business_key) WHERE {predicate}")
        db.executemany(
            "INSERT INTO versions VALUES (?, 'same-key', ?, ?)",
            [
                (0, None, None),
                (1, "newer", None),
                (2, None, "withdrawn"),
                (3, "newer", "withdrawn"),
            ],
        )
        assert db.execute(f"SELECT id FROM versions WHERE {predicate}").fetchall() == [(0,)], (
            index,
            dialect,
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO versions VALUES (4, 'same-key', NULL, NULL)")


def test_withdrawable_entities_stay_the_bank_book_tables() -> None:
    assert set(WITHDRAWABLE_ENTITIES) == {"position", "gl_account", "counterparty", "product"}


def test_withdrawable_models_have_both_lifecycle_columns() -> None:
    names = {
        cast(type[object], mapper.class_).__name__
        for mapper in Base.registry.mappers
        if {"superseded_by", "withdrawn_at"} <= set(mapper.columns.keys())
    }
    assert {"CanonicalPositionSnapshot", "CanonicalGlAccount", "CanonicalPosition"} <= names
    assert "DeskObservation" not in names
    assert len(names) >= len(WITHDRAWABLE_ENTITIES)
