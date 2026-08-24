"""Who may call the OFFICIAL derivation — and who must use the live one.

There are two derivation tiers over one canonical store (ARCHITECTURE.md §3b,
CLAUDE.md "the live engine is two-tier"), and they behave differently ON PURPOSE
when a book does not reconcile:

* ``derive_current_facts`` (``live=True``) — the LIVE plane. Plugs the gap,
  stamps the fact ``status="blocked"``, and KEEPS SERVING, because an operator
  has to see a broken book in order to fix it. This is what Treasury and ALM run
  on.
* ``derive_facts`` (``live=False``) — the OFFICIAL/filing plane. REFUSES. A date
  that cannot produce a filable book produces nothing.

Why this test exists (founder review, 2026-08-23)
-------------------------------------------------
The refusal is not a fault signal. It is the fail-closed design working: a date
with, say, positions but no same-date general ledger is genuinely not filable,
and the official path says so. Read as a health check it is deeply misleading —
an assistant spent a session calling ``derive_facts`` directly to "verify" two
tenants, reported gaps of 86% of assets, and recommended withdrawing data from
the reference tenant. The platform's own ``live_metrics`` said ``ready``
throughout. Nothing was wrong.

An import-graph guard cannot stop someone doing that in a scratch script, but it
CAN stop the confusion entering the application: if a Treasury or ALM surface
ever reaches for the filing derivation, this fails at the moment it is written.

The allow-list is deliberately tiny and each entry is a filing or bulk-load
entry point, never a read surface.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"

#: The ONLY modules permitted to call the official derivation.
#:
#: ``pipeline``        — ``run_official``, the immutable filing-run job.
#: ``data_activation`` — ``activate_bank_data``, explicit operator activation.
#: ``history_loader``  — the bulk historical load, which mints the fact spine.
#:
#: A new entry here is a claim that the module produces FILING evidence. If it
#: serves a dashboard, it belongs on the live tier instead.
OFFICIAL_DERIVATION_CALLERS: frozenset[str] = frozenset(
    {
        "app/services/pipeline.py",
        "app/services/data_activation.py",
        "app/services/history_loader.py",
    }
)

#: The function that refuses, and the module that defines it (which is allowed
#: to name it).
OFFICIAL_DERIVATION = "derive_facts"
DERIVATION_MODULE = "app/services/fact_derivation.py"


def _calls_named(path: Path, name: str) -> bool:
    """Whether ``path`` CALLS ``name`` — a call node, not an import or a string.

    Importing the symbol without calling it is not the hazard; invoking the
    filing derivation is. ``derive_current_facts`` shares the prefix, so the
    match is on the exact identifier.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else None
        )
        if called == name:
            return True
    return False


def _app_modules() -> list[Path]:
    return sorted(p for p in APP.rglob("*.py") if "__pycache__" not in p.parts)


def test_only_filing_entry_points_call_the_official_derivation() -> None:
    offenders: list[str] = []
    for path in _app_modules():
        rel = path.relative_to(APP.parent).as_posix()
        if rel == DERIVATION_MODULE:
            continue  # defines it
        if _calls_named(path, OFFICIAL_DERIVATION) and rel not in OFFICIAL_DERIVATION_CALLERS:
            offenders.append(rel)
    assert not offenders, (
        f"{', '.join(offenders)} calls {OFFICIAL_DERIVATION}(), the OFFICIAL/filing "
        "derivation, which REFUSES a book that does not reconcile. A read surface "
        "must use derive_current_facts() (the live plane), which plugs and marks the "
        "result blocked instead of refusing. If this module really does produce "
        "filing evidence, add it to OFFICIAL_DERIVATION_CALLERS with a reason."
    )


def test_the_allow_list_has_no_stale_entries() -> None:
    """An allow-list that names modules which no longer call it is a lie about
    the boundary, and the next reader widens it by copying a dead entry."""
    stale = [
        rel
        for rel in sorted(OFFICIAL_DERIVATION_CALLERS)
        if not _calls_named(APP.parent / rel, OFFICIAL_DERIVATION)
    ]
    assert not stale, f"OFFICIAL_DERIVATION_CALLERS lists non-callers: {stale}"


def test_the_live_plane_is_reachable_and_distinct() -> None:
    """Both tiers must exist and be different functions.

    Cheap, but it is the invariant the whole boundary rests on: if the live
    derivation were ever aliased to the official one, every dashboard would
    start refusing on a ragged book and this guard would still pass.
    """
    from app.services import fact_derivation  # noqa: PLC0415

    assert callable(fact_derivation.derive_facts)
    assert callable(fact_derivation.derive_current_facts)
    assert fact_derivation.derive_facts is not fact_derivation.derive_current_facts
