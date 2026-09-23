"""The object-reference (IDOR) census must resolve on every route the app mounts.

The two IDOR suites (`tests/api/test_authorization_object_reference_coverage.py`
and `tests/db/test_authorization_object_reference_properties.py`) enumerate
their cases from the live FastAPI registry at collection time, so a route that
adds an object identifier the catalogue does not know breaks BOTH suites at
collection rather than failing one case.  That is the right severity — an
uncatalogued identifier is an unproven IDOR surface — but two green PRs can
each carry half of it: one adds the census, the other adds routes, and neither
base shows the collision until main has both (2026-09-20).

This guard runs in the architecture job, which needs no Postgres and runs on
every PR, and names the exact identifiers so the fix is mechanical: seed a kind
in `tests/fixtures/object_references.py` (path) or a ``REFERENCE_FIELDS`` row
(body/query), or list the route in ``KNOWN_UNCOVERED`` with its reason.  The
companion checks keep the exclusion and quarantine lists honest: an entry that
no longer names a live route is a stale decision, not a covered one.
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from app.main import create_app
from tests.fixtures.object_reference_routes import (
    KNOWN_DEFECTS,
    KNOWN_UNCOVERED,
    uncatalogued_references,
)

_APP = create_app()


def _live_routes() -> set[tuple[str, str]]:
    return {
        (method, route.path)
        for route in _APP.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }


def test_every_object_reference_is_catalogued() -> None:
    """Every ``*_id`` a mounted route accepts resolves to a seeded object kind."""
    unresolved = uncatalogued_references(_APP)
    assert not unresolved, (
        "object identifiers without a catalogue entry — add an ObjectKind or "
        "REFERENCE_FIELDS row in tests/fixtures/object_references.py, or list the "
        "route in KNOWN_UNCOVERED with its reason:\n  " + "\n  ".join(unresolved)
    )


def test_known_uncovered_routes_are_still_mounted() -> None:
    """An exclusion that names no live route is a stale decision to delete."""
    stale = sorted(f"{method} {path}" for method, path in KNOWN_UNCOVERED - _live_routes())
    assert not stale, f"KNOWN_UNCOVERED names routes the app no longer mounts: {stale}"


def test_known_defects_name_mounted_routes() -> None:
    """A quarantined defect must still be a route, or its pin proves nothing."""
    live = _live_routes()
    stale = sorted(
        f"{method} {path} [{layout}]"
        for method, path, layout in KNOWN_DEFECTS
        if (method, path) not in live
    )
    assert not stale, f"KNOWN_DEFECTS names routes the app no longer mounts: {stale}"
