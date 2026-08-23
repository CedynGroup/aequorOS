"""The current generation of a canonical row is decided by TWO columns.

``superseded_by IS NULL AND withdrawn_at IS NULL``. A reader that checks only
``superseded_by`` resurrects a withdrawn book — a duplicated source feed the bank
explicitly retired under maker-checker walking back into a filed CAR or LCR.
That failure is silent, so it is caught here instead of in production: any query
filtering one of the pair without the other fails this test.

There were 77 such predicates across 28 modules when withdrawal was built. The
count is not pinned (it will move); the pairing is.

Why this file was rewritten (audit 2026-08-22, D-22)
----------------------------------------------------
The first version matched only ``Canonical<Something>.`` and the literal name
``model.``. ``sources_ext/bsd4.py`` and ``sources_ext/bsd8.py`` bind the class to
a short local name first (``snap = CanonicalPositionSnapshot``) and then filter
``snap.superseded_by.is_(None)`` — so six predicates in the sectoral loan book
and the adverse-classification return were invisible to a guard whose whole
purpose was to see them, and the re-audit found both returns summing retired
rows. A guard that a one-line rebinding defeats is not a guard.

So the owner of every predicate is now RESOLVED rather than pattern-matched:

* which model classes are even capable of being withdrawn is read from the
  SQLAlchemy mapper registry (``superseded_by`` **and** ``withdrawn_at``
  columns), not from a name convention — a new canonical table is covered the
  day it is mapped;
* local rebindings (``x = Model``) and ORM aliases (``x = aliased(Model)``) are
  resolved per file;
* ``model.`` stays special-cased: it is the generic per-entity helper in
  ``ingestion`` and ``pull_runner`` whose argument is always a
  ``CanonicalMetadataMixin`` subclass;
* an owner that resolves to NOTHING is reported rather than skipped, so the next
  binding form nobody anticipated fails loudly here instead of silently in a
  filed return.
"""

from __future__ import annotations

import re
from pathlib import Path

import app.models  # noqa: F401 - imported for its side effect: every mapper registered
from app.db.base import Base
from app.models.canonical import CURRENT_GENERATION_SQL
from app.models.canonical_withdrawal import WITHDRAWABLE_ENTITIES

APP_ROOT = Path(__file__).resolve().parents[2] / "app"

#: Any ``<owner>.superseded_by.is_(None)`` predicate, however the owner is named.
_PREDICATE = re.compile(r"(?<![\w.])([A-Za-z_]\w*)\.superseded_by\.is_\(None\)")

#: ``x = Model`` / ``x = aliased(Model)`` — the two ways a query names a mapped
#: class by something other than its class name.
_BINDING = re.compile(r"^\s*([A-Za-z_]\w*)\s*(?::[^=]+)?=\s*(?:aliased\(\s*)?([A-Za-z_]\w*)")

#: The generic per-entity helpers in ``ingestion``/``pull_runner`` bind their
#: model through a parameter, so the name is all a static reader can see. Their
#: argument is always a ``CanonicalMetadataMixin`` subclass.
_GENERIC_OWNERS = frozenset({"model"})


def _withdrawable_models() -> frozenset[str]:
    """Mapped classes carrying BOTH lifecycle columns, from the registry itself.

    Read from SQLAlchemy rather than declared here so a canonical table added
    tomorrow is guarded tomorrow. Classes with ``superseded_by`` alone (the
    market-desk observation register, market-data overlays) are deliberately NOT
    in this set: they have no ``withdrawn_at`` to pair with.
    """
    names: set[str] = set()
    for mapper in Base.registry.mappers:
        columns = set(mapper.columns.keys())
        if {"superseded_by", "withdrawn_at"} <= columns:
            names.add(mapper.class_.__name__)
    return frozenset(names)


WITHDRAWABLE_MODELS = _withdrawable_models()


def _bindings(lines: list[str]) -> dict[str, str]:
    """Local names bound to a mapped class, anywhere in one module."""
    resolved: dict[str, str] = {}
    for line in lines:
        match = _BINDING.match(line)
        if match is None:
            continue
        name, target = match.group(1), match.group(2)
        if target in WITHDRAWABLE_MODELS or target in _GENERIC_OWNERS:
            resolved[name] = target
    return resolved


def _resolve(owner: str, bindings: dict[str, str]) -> str | None:
    """The mapped class a predicate's owner refers to, or ``None`` if not one."""
    if owner in _GENERIC_OWNERS or owner in WITHDRAWABLE_MODELS:
        return owner
    target = bindings.get(owner)
    if target in _GENERIC_OWNERS or target in WITHDRAWABLE_MODELS:
        return target
    return None


def offenders_in(source: str, label: str) -> list[str]:
    """Unpaired current-generation predicates in one module's text.

    The pair may sit on the same line (the ``is_current_generation`` tuple) or
    on the next one (the shape every query uses).
    """
    lines = source.splitlines()
    bindings = _bindings(lines)
    found: list[str] = []
    for index, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            continue
        for match in _PREDICATE.finditer(line):
            owner = match.group(1)
            resolved = _resolve(owner, bindings)
            if resolved is None:
                if owner in _KNOWN_NON_CANONICAL:
                    continue
                found.append(f"{label}:{index + 1}: owner {owner!r} does not resolve to a model")
                continue
            paired = f"{owner}.withdrawn_at.is_(None)"
            following = lines[index + 1] if index + 1 < len(lines) else ""
            if paired not in line and paired not in following:
                found.append(f"{label}:{index + 1}")
    return found


def _known_non_canonical() -> frozenset[str]:
    """Mapped classes with ``superseded_by`` and NO ``withdrawn_at``.

    These have their own supersession discipline and nothing to pair with, so
    their predicates are correct as written. Read from the registry for the same
    reason the withdrawable set is.
    """
    names: set[str] = set()
    for mapper in Base.registry.mappers:
        columns = set(mapper.columns.keys())
        if "superseded_by" in columns and "withdrawn_at" not in columns:
            names.add(mapper.class_.__name__)
    return frozenset(names)


_KNOWN_NON_CANONICAL = _known_non_canonical()


def _unpaired() -> list[str]:
    offenders: list[str] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        offenders.extend(offenders_in(path.read_text(), str(path.relative_to(APP_ROOT.parent))))
    return offenders


def test_every_canonical_current_generation_read_also_excludes_withdrawn_rows() -> None:
    offenders = _unpaired()
    assert not offenders, (
        "These queries select the current generation of a canonical entity but ignore "
        "`withdrawn_at`, so they would read a book that was explicitly withdrawn under "
        "maker-checker:\n  " + "\n  ".join(offenders) + "\nAdd the matching "
        "`<owner>.withdrawn_at.is_(None),` on the next line."
    )


def test_the_guard_sees_through_a_local_rebinding() -> None:
    """The negative control, and the exact D-22 defect.

    ``snap = CanonicalPositionSnapshot`` is what made six unpaired predicates in
    BSD4 and BSD8 invisible to the previous version of this test. If this ever
    stops failing, the guard has stopped guarding.
    """
    unpaired = "\n".join(
        [
            "snap = CanonicalPositionSnapshot",
            "query = select(snap).where(",
            "    snap.superseded_by.is_(None),",
            ")",
        ]
    )
    assert offenders_in(unpaired, "synthetic.py") == ["synthetic.py:3"]

    paired = unpaired.replace(
        "    snap.superseded_by.is_(None),",
        "    snap.superseded_by.is_(None),\n    snap.withdrawn_at.is_(None),",
    )
    assert offenders_in(paired, "synthetic.py") == []


def test_the_guard_sees_through_an_orm_alias() -> None:
    aliased_source = "\n".join(
        [
            "rows = aliased(CanonicalGlAccount)",
            "query = select(rows).where(rows.superseded_by.is_(None))",
        ]
    )
    assert offenders_in(aliased_source, "synthetic.py") == ["synthetic.py:2"]


def test_an_unresolvable_owner_is_reported_rather_than_skipped() -> None:
    """Fail loudly on the next binding form nobody anticipated."""
    mystery = "query = select(x).where(whatever.superseded_by.is_(None),)"
    assert offenders_in(mystery, "synthetic.py") == [
        "synthetic.py:1: owner 'whatever' does not resolve to a model"
    ]


def test_a_register_with_no_withdrawn_at_column_is_not_demanded_to_pair() -> None:
    """``DeskObservation`` supersedes but is never withdrawn — nothing to pair."""
    assert "DeskObservation" in _KNOWN_NON_CANONICAL
    assert "DeskObservation" not in WITHDRAWABLE_MODELS
    source = "query = select(x).where(DeskObservation.superseded_by.is_(None),)"
    assert offenders_in(source, "synthetic.py") == []


def test_the_withdrawable_model_set_is_read_from_the_mapper_registry() -> None:
    """A new canonical table is covered the day it is mapped, not the day
    someone remembers to add it to a list here."""
    assert "CanonicalPositionSnapshot" in WITHDRAWABLE_MODELS
    assert "CanonicalGlAccount" in WITHDRAWABLE_MODELS
    assert "CanonicalPosition" in WITHDRAWABLE_MODELS
    assert len(WITHDRAWABLE_MODELS) >= len(WITHDRAWABLE_ENTITIES)


def test_bsd4_and_bsd8_are_now_inside_the_guards_reach() -> None:
    """D-22 named these two files; pin that they are actually scanned and clean."""
    for name in ("bsd4.py", "bsd8.py"):
        path = APP_ROOT / "services" / "regulatory_reporting" / "bog_forms" / "sources_ext" / name
        source = path.read_text()
        assert "snap.superseded_by.is_(None)" in source, f"{name} lost its predicate"
        assert offenders_in(source, name) == []


def test_the_partial_indexes_use_the_same_predicate_as_the_readers() -> None:
    """A unique index narrower than the readers' predicate blocks re-ingestion.

    If the index still said ``superseded_by IS NULL`` alone, re-ingesting a
    withdrawn book would collide with the withdrawn row it is meant to replace.
    """
    assert CURRENT_GENERATION_SQL == "superseded_by IS NULL AND withdrawn_at IS NULL"
    source = (APP_ROOT / "models" / "canonical.py").read_text()
    assert 'sql_text("superseded_by IS NULL")' not in source


def test_withdrawable_entities_stay_the_bank_book_tables() -> None:
    """Widening withdrawal is a decision, not an accident.

    Market-data canonicals carry the same mixin columns but are vendor state
    with their own supersession discipline in ``pull_runner``. Adding one here
    means auditing every market-data reader for the pairing above first.
    """
    assert set(WITHDRAWABLE_ENTITIES) == {
        "position",
        "gl_account",
        "counterparty",
        "product",
    }
