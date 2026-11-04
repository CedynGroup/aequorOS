"""Current-generation predicates admit active rows and allow replacement of retired rows.

This module's other-suite cases live in

``test_current_generation_predicate_integration.py``.
"""

from __future__ import annotations

from importlib import import_module
from typing import cast

from app.data_engine.models.canonical_withdrawal import WITHDRAWABLE_ENTITIES
from app.db.base import Base

import_module("app.models")


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
