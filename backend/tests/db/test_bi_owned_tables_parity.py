"""Model↔migration parity for EVERY BI-owned table, with the subject DERIVED.

The subject must include BI-owned tables without the ``bi_`` prefix, such as
``ai_commentary_drafts``; a prefix-only census would silently omit their column
and CHECK parity.

This module closes the CLASS rather than the instance. Its subject is not a
tuple anybody maintains; it is computed two ways and the two must agree:

* every table declared by a module under ``app.bi.models`` (found with
  ``pkgutil``, so a new model module joins by existing);
* every ``bi_``-prefixed table in ``Base.metadata`` (so a BI table declared in
  some other module joins by its name).

The union is the subject of the four parity axes below. A separate test pins
that the union of the four hand tuples equals the derived subject, so a new
table that is declared but not registered fails by name — and so does a
registered table that no module declares.

One module-scoped forward migration serves every test here: the axes are
read-only against ``head`` and the round trip is already proven elsewhere.
Postgres-gated on ``TEST_DATABASE_URL``.

This module's other-suite cases live in ``test_bi_owned_tables_parity_integration.py``.
"""

from __future__ import annotations

import sqlalchemy as sa

from app.bi.models.commentary import BI_COMMENTARY_TABLES
from app.bi.models.content import BI_CONTENT_TABLES
from app.bi.models.marts import BI_TABLES
from app.bi.models.notifications import BI_NOTIFICATION_TABLES
from app.db.base import Base
from tests.db.test_bi_owned_tables_parity_integration import (
    BI_MODEL_MODULES,
    BI_OWNED_TABLES,
    DECLARED_BY_BI_MODULES,
    PREFIXED_IN_METADATA,
)

HAND_REGISTERED: frozenset[str] = frozenset(
    (*BI_TABLES, *BI_CONTENT_TABLES, *BI_NOTIFICATION_TABLES, *BI_COMMENTARY_TABLES)
)


def test_the_subject_is_derived_from_the_model_modules_and_the_prefix() -> None:
    """Both derivations are non-empty, and the module scan found more than one
    module — a scan that silently matched ``bi.py`` alone would be the Phase 3
    blind spot again."""
    assert len(BI_MODEL_MODULES) >= 4, [module.__name__ for module in BI_MODEL_MODULES]
    assert {module.__name__.rsplit(".", 1)[1] for module in BI_MODEL_MODULES} >= {
        "marts",
        "commentary",
        "content",
        "notifications",
    }
    # 24 since `202609290080` dropped `bi_reconciliation_results` (BI carries no
    # reconciliation verdict — founder's decision of 2026-09-29). The floor is a
    # coarse "the scan found the tables at all" guard, not a census: the exact
    # membership is proven in both directions by the next test, against the hand
    # tuples. Move this number only alongside a migration that adds or drops a
    # BI-owned table, and say which in the comment.
    assert len(BI_OWNED_TABLES) >= 24, BI_OWNED_TABLES
    # The one deliberately unprefixed BI table is in the subject BY DECLARATION,
    # which is the whole point of deriving from modules as well as from the prefix.
    assert "ai_commentary_drafts" in DECLARED_BY_BI_MODULES
    assert "ai_commentary_drafts" not in PREFIXED_IN_METADATA
    assert "ai_commentary_drafts" in BI_OWNED_TABLES


def test_every_bi_owned_table_is_registered_in_exactly_one_hand_tuple() -> None:
    unregistered = sorted(set(BI_OWNED_TABLES) - HAND_REGISTERED)
    undeclared = sorted(HAND_REGISTERED - set(BI_OWNED_TABLES))
    assert not unregistered, (
        "these BI-owned tables are registered in NO model tuple, so the suites that "
        "iterate BI_TABLES / BI_CONTENT_TABLES / BI_NOTIFICATION_TABLES / "
        f"BI_COMMENTARY_TABLES do not see them: {unregistered}"
    )
    assert not undeclared, f"these tuple entries name no declared table: {undeclared}"
    tuples = (BI_TABLES, BI_CONTENT_TABLES, BI_NOTIFICATION_TABLES, BI_COMMENTARY_TABLES)
    counted = [name for name in BI_OWNED_TABLES if sum(name in tuple_ for tuple_ in tuples) != 1]
    assert not counted, f"registered in more or fewer than one tuple: {counted}"


def test_the_check_axis_is_not_vacuous_for_the_unprefixed_table() -> None:
    """A CHECK parity test over a table with no CHECKs passes while proving nothing.
    The commentary table pins a status vocabulary, so it must have at least one."""
    declared = [
        constraint
        for constraint in Base.metadata.tables["ai_commentary_drafts"].constraints
        if isinstance(constraint, sa.CheckConstraint) and constraint.name
    ]
    assert declared, "ai_commentary_drafts declares no named CHECK constraint"
