"""The AI commentary surface: what a reader can ask for, and what they get back.

The feature behind these two routes was complete and inert — a job type, an
exclusive worker lane, a handler, a row, a payload minimiser, a prompt, a
grounding validator, a deterministic fallback and passing tests, and no caller
anywhere. So the first thing this file is for is the property that made it
inert: a request from a real reader over HTTP must reach
``request_commentary``, queue a job in the ``ai`` lane, and write the egress
audit event. ``tests/architecture/test_job_enqueue_reachability.py`` proves the
path EXISTS statically; these prove it executes.

The rest are the ways a commentary panel lies, one test each:

* **a refusal renders as an empty panel.** A shut gate, a spent daily cap and a
  sheet with nothing quotable each produce commentary — the platform's own — and
  each says so. None of them is an HTTP error, and none of them serves nothing.
* **model prose and platform prose read the same.** ``author`` is on every
  answer, and the model's figures and names arrive as PLATFORM segments resolved
  server-side, never as text the model wrote.
* **one reader's draft is served to another.** A colleague polling the same
  institution and date gets their own request or none, whatever their grants.
* **a draft written against an older book is presented as current.** Staleness is
  reported.
* **a client polls forever.** An expired queued request stops being pollable, and
  ``poll_after_seconds`` is the contract that says so.
* **the surface invents its own authority.** A reader whose access covers none of
  the headline figures is refused, and a sibling institution their binding does
  not name is refused too.

The deployment flag's 404, the cross-tenant 404, the impersonated operator and
the machine principal are covered for BOTH routes by the shared sweeps in
``tests/bi/api/test_bi_routes.py`` (``ROUTES``), which is where every BI route
answers them together; the sibling-institution refusal is not in those sweeps and
is proven here.

This module's other-suite cases live in ``test_bi_commentary_routes_integration.py``.
"""

from __future__ import annotations

from app.bi.api import manage_bi_commentary as surface
from app.bi.service import commentary


def test_both_routes_are_mounted_on_the_tenant_api() -> None:
    """The half of reachability an AST guard cannot see.

    ``tests/architecture/test_job_enqueue_reachability.py`` walks from every
    ``@router.get``/``@router.post`` decorator, so it reports a handler as
    reachable whether or not ``app/api/router.py`` ever includes the router it
    hangs on. That is the same shape as the defect it was written for — a
    complete call site nothing calls — one level up, so it is asserted here
    against the app the platform actually serves.
    """

    from fastapi.routing import APIRoute  # noqa: PLC0415 - registry inspection only

    from app.main import create_app  # noqa: PLC0415 - one app, built for this check

    mounted = {
        (method, route.path)
        for route in create_app().routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }
    path = "/api/v1/banks/{bank_id}/bi/commentary"
    missing = sorted(pair for pair in (("GET", path), ("POST", path)) if pair not in mounted)
    assert not missing, (
        "the commentary router is not included in app/api/router.py, so the feature is "
        "unreachable however complete it is; it needs the import and the "
        f"include_router call with BANK_ROUTE_DEPENDENCIES + Depends(require_bi_enabled): {missing}"
    )


def test_the_author_vocabulary_matches_the_commentary_package() -> None:
    """The wire words and the service's own constants cannot drift apart."""

    assert surface.AUTHOR_MODEL == commentary.MODEL_SOURCE
    assert surface.AUTHOR_PLATFORM == commentary.FALLBACK_SOURCE
