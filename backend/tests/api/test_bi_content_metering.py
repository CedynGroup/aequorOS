"""Metering the saved-dashboard and calculated-measure routes: one row, always.

The defect these tests exist to keep closed: ``bi_query_log`` rows ARE the read
budget (``query_log.budget_for`` counts them), and ``manage_bi_content`` wrote one
on exactly one path — the successful dashboard read. Every refusal returned
without a row, so an authenticated principal inside a tenant could ask for
dashboard and measure ids without limit and at no cost, and the answers are
distinguishable: 404 for a document they may not open, 403 for one they may open
but do not own, 409 for a state clash. That is an enumeration oracle whose only
bound was a rate limit that never counted a probe.

What is pinned here:

* every route of the module has a case, and the case list is compared against the
  router's OWN route table, so a route added later cannot escape the sweep;
* a refused request leaves exactly one ``denied`` row — not zero, and not two;
* the loop is no longer free: N refusals move the budget by N, and the N+1st
  request is refused by the limit rather than by the object. That test fails on
  the pre-fix code, which is the point — a metering test that passes without the
  fix proves nothing;
* the 429 itself is NOT charged, so an over-budget principal cannot deepen their
  own deficit;
* the row survives the raise in a really committed transaction, read back through
  a session this test opens for itself, and a write staged before the refusal does
  NOT survive with it;
* the digest tells two questions apart, hashes one question alike, and no column
  of the row carries the requested id in a readable form;
* audit A9-09: a dashboard served with every widget refused is still an
  ``allowed`` row. That decision is argued in the module docstring; this test is
  what makes changing it deliberate.

The institution, the identities and the grants come from
``tests/api/test_bi_content_routes.py`` — its ``plane`` fixture is the narrow
reader this surface is attacked with, and re-declaring it here would be a second
copy to keep in step. The fixtures are requested by NAME through
``usefixtures`` rather than as parameters, so nothing in this module shadows the
import that makes them resolvable.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.session import get_sessionmaker
from app.features import manage_bi_content
from app.features.read_bi import BiReadAccess
from app.models import Bank, User
from app.models.bi import BiQueryLog
from app.models.bi_content import BiMeasure
from app.services.bi import content, query_log
from tests.api.test_bi_content_routes import (
    AS_OF,
    BANK_ID,
    BASE,
    DEPOSITS,
    LOANS,
    MIXED_SPEC,
    OBLIGOR,
    VIEWER,
    bi_on,  # noqa: F401 - imported so pytest can resolve it by name
    plane,  # noqa: F401 - imported so pytest can resolve it by name
)
from tests.support.helpers import ORG_1, USER_1, headers

#: Every test here needs the institution and the deployment flag.
pytestmark = pytest.mark.usefixtures("plane", "bi_on")

#: An id of the right shape that names nothing. Every "probe" below asks for it,
#: which is exactly what an enumeration loop does.
MISSING = UUID("11111111-1111-4111-8111-111111111111")

#: A second one, so the digest can be shown to tell two questions apart.
OTHER_MISSING = UUID("22222222-2222-4222-8222-222222222222")

SPEC_BODY: dict[str, Any] = {"title": "Probe", "visibility": "private", "spec": MIXED_SPEC}
OWN_FORMULA = f"[m:{LOANS}] * 2"
REFUSED_FORMULA = f"[m:{DEPOSITS}] * 2"


@dataclass(frozen=True)
class Case:
    """One request, and the single row it must leave behind.

    ``path`` is the router's own template rather than the URL, because that is
    what :func:`test_every_route_of_this_module_is_covered` compares against the
    live route table: a case list keyed on URLs could drift from the routes it
    claims to cover without anything noticing.
    """

    method: str
    path: str
    url: str
    status: int
    decision: str
    body: dict[str, Any] | None = None
    actor: UUID = USER_1


DASHBOARDS = "/banks/{bank_id}/bi/dashboards"
DASHBOARD = "/banks/{bank_id}/bi/dashboards/{dashboard_id}"
MEASURES = "/banks/{bank_id}/bi/measures"
MEASURE = "/banks/{bank_id}/bi/measures/{measure_id}"

#: The three routes with no reachable refusal: a list is served to everyone the
#: dependencies admit, and the validation route answers even a hostile formula
#: with a 200 VERDICT rather than a refusal. Their case asserts the ``allowed``
#: row instead. If one of them grows a refusal path, its case is where to say so.
NO_REFUSAL: tuple[tuple[str, str], ...] = (
    ("GET", DASHBOARDS),
    ("GET", MEASURES),
    ("POST", f"{MEASURES}/validation"),
)

CASES: tuple[Case, ...] = (
    Case("GET", DASHBOARDS, "/dashboards", 200, query_log.DECISION_ALLOWED),
    Case(
        "POST",
        DASHBOARDS,
        "/dashboards",
        404,
        query_log.DECISION_DENIED,
        body={"title": "Probe", "visibility": "private", "from_pack": "no_such_pack"},
    ),
    Case("GET", DASHBOARD, f"/dashboards/{MISSING}?as_of={AS_OF}", 404, query_log.DECISION_DENIED),
    Case(
        "PUT", DASHBOARD, f"/dashboards/{MISSING}", 404, query_log.DECISION_DENIED, body=SPEC_BODY
    ),
    Case("DELETE", DASHBOARD, f"/dashboards/{MISSING}", 404, query_log.DECISION_DENIED),
    Case(
        "GET",
        f"{DASHBOARD}/versions",
        f"/dashboards/{MISSING}/versions",
        404,
        query_log.DECISION_DENIED,
    ),
    Case(
        "GET",
        f"{DASHBOARD}/shares",
        f"/dashboards/{MISSING}/shares",
        404,
        query_log.DECISION_DENIED,
    ),
    Case(
        "PUT",
        f"{DASHBOARD}/shares",
        f"/dashboards/{MISSING}/shares",
        404,
        query_log.DECISION_DENIED,
        body={"user_ids": []},
    ),
    Case(
        "POST",
        f"{MEASURES}/validation",
        "/measures/validation",
        200,
        query_log.DECISION_ALLOWED,
        body={"expression": OWN_FORMULA},
    ),
    Case("GET", MEASURES, "/measures", 200, query_log.DECISION_ALLOWED),
    Case(
        "POST",
        MEASURES,
        "/measures",
        403,
        query_log.DECISION_DENIED,
        body={
            "measure_key": "custom.probe",
            "label": "Probe",
            "expression": REFUSED_FORMULA,
            "value_type": "amount",
        },
        actor=VIEWER,
    ),
    Case("GET", MEASURE, f"/measures/{MISSING}", 404, query_log.DECISION_DENIED),
    Case(
        "PUT",
        MEASURE,
        f"/measures/{MISSING}",
        404,
        query_log.DECISION_DENIED,
        body={"label": "Probe", "expression": OWN_FORMULA, "value_type": "amount"},
    ),
    Case("DELETE", MEASURE, f"/measures/{MISSING}", 404, query_log.DECISION_DENIED),
    Case(
        "POST",
        f"{MEASURE}/proposal",
        f"/measures/{MISSING}/proposal",
        404,
        query_log.DECISION_DENIED,
        body={"reason": "Probe."},
    ),
    Case(
        "POST",
        f"{MEASURE}/decision",
        f"/measures/{MISSING}/decision",
        404,
        query_log.DECISION_DENIED,
        body={"decision": "approve", "reason": "Probe.", "expression_digest": "0" * 64},
    ),
)


def _module_routes() -> frozenset[tuple[str, str]]:
    """Every (method, path) this module's router carries, from the router itself."""

    return frozenset(
        (method, route.path)
        for route in manage_bi_content.router.routes
        if isinstance(route, APIRoute)
        for method in route.methods - {"HEAD", "OPTIONS"}
    )


def _authv(db: Session, user_id: UUID) -> int:
    user = db.get(User, user_id)
    assert user is not None
    db.refresh(user)
    return user.authorization_version


def _headers(db: Session, user_id: UUID = USER_1) -> dict[str, str]:
    return {
        **headers(org_id=ORG_1, user_id=user_id, authorization_version=_authv(db, user_id)),
        "Content-Type": "application/json",
    }


def _rows(db: Session, *, principal: UUID | None = None) -> list[BiQueryLog]:
    db.expire_all()
    statement = select(BiQueryLog).where(BiQueryLog.organization_id == ORG_1)
    if principal is not None:
        statement = statement.where(BiQueryLog.principal_user_id == principal)
    return list(db.scalars(statement.order_by(BiQueryLog.queried_at, BiQueryLog.query_hash)))


def _send(client: TestClient, db: Session, case: Case) -> Any:
    return client.request(
        case.method,
        f"{BASE}{case.url}",
        json=case.body,
        headers=_headers(db, case.actor),
    )


def _create(client: TestClient, db: Session, spec: dict[str, Any], *, visibility: str) -> str:
    response = client.post(
        f"{BASE}/dashboards",
        json={"title": "Funding and lending", "visibility": visibility, "spec": spec},
        headers=_headers(db),
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


# --- the sweep ---------------------------------------------------------------------------


def test_every_route_of_this_module_is_covered() -> None:
    """The case list is checked against the live route table, never a count alone.

    A route added to ``manage_bi_content`` with no metering case fails HERE, which
    is the only way a sweep of this kind stays true after the commit that wrote it.
    """

    covered = frozenset((case.method, case.path) for case in CASES)
    routes = _module_routes()
    assert covered == routes, {
        "uncovered": sorted(routes - covered),
        "stale": sorted(covered - routes),
    }
    assert len(CASES) == len(routes) == 16


def test_the_routes_with_no_reachable_refusal_are_exactly_the_three_named() -> None:
    """A tripwire, not a licence: if one of these learns to refuse, say so above."""

    served_cases = frozenset(
        (case.method, case.path) for case in CASES if case.decision == query_log.DECISION_ALLOWED
    )
    assert served_cases == frozenset(NO_REFUSAL)


@pytest.mark.parametrize("case", CASES, ids=lambda case: f"{case.method} {case.url}")
def test_every_request_leaves_exactly_one_row_with_the_decision_it_earned(
    db_client: TestClient, db_session: Session, case: Case
) -> None:
    """One row per request, whatever the outcome, read from a separate session.

    ``db_session`` is a different ``Session`` from the one the request ran on, so
    a row still sitting unflushed in the request's identity map is not visible
    here.
    """

    response = _send(db_client, db_session, case)
    assert response.status_code == case.status, response.text

    rows = _rows(db_session, principal=case.actor)
    assert len(rows) == 1, [(row.surface, row.decision) for row in rows]
    (row,) = rows
    assert row.decision == case.decision
    assert row.surface == content.DASHBOARD_SURFACE
    assert row.bank_id == BANK_ID
    assert row.organization_id == ORG_1
    assert row.principal_user_id == case.actor
    assert len(row.query_hash) == 64
    assert row.row_count is None


# --- the anti-vacuity control ------------------------------------------------------------


def test_a_probe_loop_of_refusals_moves_the_budget_by_one_per_probe(
    db_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The test the fix exists to pass, and the one the old code fails.

    Before the refusals were metered, all six requests below answered 404 and the
    budget stayed at zero forever. Now each of the first five is counted and the
    sixth is refused by the LIMIT rather than by the object — so the enumeration
    loop has a price, and the price is the one D-010 already set.
    """

    probes = 5
    monkeypatch.setattr(query_log, "RATE_LIMIT_MAX_QUERIES", probes)
    url = f"{BASE}/dashboards/{MISSING}?as_of={AS_OF}"

    for attempt in range(probes):
        response = db_client.get(url, headers=_headers(db_session))
        assert response.status_code == 404, response.text
        budget = query_log.budget_for(db_session, organization_id=ORG_1, principal_user_id=USER_1)
        assert budget.used == attempt + 1, f"probe {attempt + 1} was free"

    assert len(_rows(db_session, principal=USER_1)) == probes
    over = db_client.get(url, headers=_headers(db_session))
    assert over.status_code == 429, over.text
    assert over.json()["error"]["details"]["error_code"] == "bi_rate_limited"


def test_the_control_above_fails_when_the_refusals_stop_being_recorded(
    db_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The anti-vacuity control: the defect, reconstructed, and shown to fail.

    A metering test that would pass on the pre-fix code proves nothing, and
    reverting a file to prove it is not something a suite can keep. So the OLD
    behaviour is put back for one test — a block that checks the budget and writes
    no row, which is exactly what ``_budget`` alone did — and the same probe loop is
    run against it. Zero rows and an unmoved budget is what the defect looked like:
    if :func:`test_a_probe_loop_of_refusals_moves_the_budget_by_one_per_probe` ever
    passes for a reason other than the recording, this test passing beside it is the
    contradiction that says so.
    """

    @contextmanager
    def unmetered(
        db: Session, access: BiReadAccess, route: str, *question: object
    ) -> Iterator[manage_bi_content._Meter]:  # noqa: SLF001 - the shape being replaced
        manage_bi_content._budget(db, access)  # noqa: SLF001 - the pre-fix body, verbatim
        yield manage_bi_content._Meter(query_hash="0" * 64)  # noqa: SLF001

    monkeypatch.setattr(manage_bi_content, "_metered", unmetered)
    monkeypatch.setattr(query_log, "RATE_LIMIT_MAX_QUERIES", 3)
    url = f"{BASE}/dashboards/{MISSING}?as_of={AS_OF}"

    for _attempt in range(4):
        assert db_client.get(url, headers=_headers(db_session)).status_code == 404

    assert _rows(db_session, principal=USER_1) == []
    budget = query_log.budget_for(db_session, organization_id=ORG_1, principal_user_id=USER_1)
    assert budget.used == 0, "the defect: four probes, no cost, and the limit never bites"


def test_the_over_budget_refusal_is_not_itself_charged(
    db_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one outcome that writes no row, and the reason it must not.

    The budget IS the row count, so charging the refusal-for-being-over-budget
    would let a principal push their own recovery out without limit: six 429s
    would cost six more rows and the window would never drain. ``read_bi`` checks
    the budget before every ``_append`` for the same reason; here it is the order
    inside ``_metered``.
    """

    monkeypatch.setattr(query_log, "RATE_LIMIT_MAX_QUERIES", 1)
    url = f"{BASE}/dashboards/{MISSING}?as_of={AS_OF}"
    assert db_client.get(url, headers=_headers(db_session)).status_code == 404

    for _attempt in range(3):
        assert db_client.get(url, headers=_headers(db_session)).status_code == 429
        assert db_client.get(f"{BASE}/dashboards", headers=_headers(db_session)).status_code == 429

    assert len(_rows(db_session, principal=USER_1)) == 1


# --- what the row says -------------------------------------------------------------------


def test_the_digest_tells_two_questions_apart_and_hashes_one_alike(
    db_client: TestClient, db_session: Session
) -> None:
    """``query_hash`` means "which question", and the raw id is in no column."""

    first = f"{BASE}/dashboards/{MISSING}?as_of={AS_OF}"
    second = f"{BASE}/dashboards/{OTHER_MISSING}?as_of={AS_OF}"
    for url in (first, first, second):
        assert db_client.get(url, headers=_headers(db_session)).status_code == 404

    rows = _rows(db_session, principal=USER_1)
    assert len(rows) == 3
    digests = [row.query_hash for row in rows]
    assert len([digest for digest in digests if digests.count(digest) == 2]) == 2, (
        "the same question asked twice must hash alike"
    )
    assert len(set(digests)) == 2, "two different ids must not share a digest"

    for row in rows:
        stored = (row.query_hash, *row.member_ids, *row.denied_members)
        assert not any(str(MISSING) in value for value in stored)
        assert not any(str(OTHER_MISSING) in value for value in stored)


def test_two_routes_asking_about_one_id_do_not_share_a_digest(
    db_client: TestClient, db_session: Session
) -> None:
    """The route label is material: a bare id would collide across questions."""

    for path in (f"/dashboards/{MISSING}/versions", f"/dashboards/{MISSING}/shares"):
        assert db_client.get(f"{BASE}{path}", headers=_headers(db_session)).status_code == 404

    rows = _rows(db_session, principal=USER_1)
    assert len(rows) == 2
    assert len({row.query_hash for row in rows}) == 2


def test_a_refusal_that_named_members_puts_them_in_the_row(
    db_client: TestClient, db_session: Session
) -> None:
    """The row and the answer agree: the members the caller was already told."""

    response = db_client.post(
        f"{BASE}/measures",
        json={
            "measure_key": "custom.probe",
            "label": "Probe",
            "expression": REFUSED_FORMULA,
            "value_type": "amount",
        },
        headers=_headers(db_session, VIEWER),
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["details"]["denied_members"] == [DEPOSITS]

    (row,) = _rows(db_session, principal=VIEWER)
    assert row.decision == query_log.DECISION_DENIED
    assert row.denied_members == [DEPOSITS]
    assert row.member_ids == []


def test_a_validation_verdict_is_an_allowed_row_carrying_the_refused_figures(
    db_client: TestClient, db_session: Session
) -> None:
    """A 200 answer is a SERVED read; the figures it could not use ride along."""

    response = db_client.post(
        f"{BASE}/measures/validation",
        json={"expression": REFUSED_FORMULA},
        headers=_headers(db_session, VIEWER),
    )
    assert response.status_code == 200, response.text
    assert response.json()["valid"] is False

    (row,) = _rows(db_session, principal=VIEWER)
    assert row.decision == query_log.DECISION_ALLOWED
    assert row.denied_members == [DEPOSITS]


def test_a_served_dashboard_read_leaves_one_row_and_not_two(
    db_client: TestClient, db_session: Session
) -> None:
    """The route that already recorded must not have gained a second row."""

    dashboard_id = _create(db_client, db_session, MIXED_SPEC, visibility="org")
    assert len(_rows(db_session, principal=USER_1)) == 1, "the save is metered once"

    response = db_client.get(
        f"{BASE}/dashboards/{dashboard_id}?as_of={AS_OF}", headers=_headers(db_session)
    )
    assert response.status_code == 200, response.text

    rows = _rows(db_session, principal=USER_1)
    assert len(rows) == 2, [row.decision for row in rows]
    read = rows[-1]
    assert read.decision == query_log.DECISION_ALLOWED
    assert LOANS in read.member_ids


def test_a_dashboard_served_with_every_widget_refused_is_still_an_allowed_row(
    db_client: TestClient, db_session: Session
) -> None:
    """Audit A9-09, decided and pinned: ``allowed`` with nothing served.

    ``decision`` is the decision about the REQUEST, and the reader WAS served the
    dashboard: its canvas, its geometry, its count and its message. The certified
    packs record the identical situation as ``allowed``
    (``read_bi._pack_record``), and the discriminator an operator needs is already
    in the row — ``member_ids`` empty with ``denied_members`` non-empty. Flipping
    it would make ``denied`` mean two different things, so it has to be a decision
    rather than a patch.
    """

    secret_only = {
        "widgets": [
            {
                "id": "funding",
                "kind": "kpi",
                "title": "Deposit funding by obligor",
                "query": {"measures": [DEPOSITS], "dimensions": [OBLIGOR], "window": "as_of"},
            }
        ],
        "layout": [{"i": "funding", "x": 0, "y": 0, "w": 4, "h": 4}],
    }
    dashboard_id = _create(db_client, db_session, secret_only, visibility="org")

    response = db_client.get(
        f"{BASE}/dashboards/{dashboard_id}?as_of={AS_OF}", headers=_headers(db_session, VIEWER)
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["access"] == "restricted"
    assert payload["restricted_widgets"] == 1

    (row,) = _rows(db_session, principal=VIEWER)
    assert row.decision == query_log.DECISION_ALLOWED
    assert row.member_ids == [], "nothing was served, and the row says so"
    # Both halves of the refused widget: the figure and the breakdown it asked for.
    assert row.denied_members == [DEPOSITS, OBLIGOR]


# --- commit discipline -------------------------------------------------------------------


@pytest.mark.committing_db
def test_a_refusals_row_really_commits_and_is_read_from_another_session(
    db_client: TestClient, db_session: Session
) -> None:
    """A row the raise rolled back would meter nothing.

    ``committing_db`` means the request's ``commit()`` is a real commit against a
    schema of its own, and the row is read back through a session this test opens
    for itself rather than through the one that wrote it.
    """

    response = db_client.get(
        f"{BASE}/dashboards/{MISSING}?as_of={AS_OF}", headers=_headers(db_session)
    )
    assert response.status_code == 404, response.text

    session = get_sessionmaker()()
    try:
        rows = list(
            session.scalars(
                select(BiQueryLog)
                .where(BiQueryLog.organization_id == ORG_1)
                .order_by(BiQueryLog.queried_at)
            )
        )
    finally:
        session.close()
    assert len(rows) == 1
    assert rows[0].decision == query_log.DECISION_DENIED


@pytest.mark.committing_db
def test_the_refusals_commit_does_not_carry_a_half_applied_write(db_session: Session) -> None:
    """The rollback inside ``_metered``, proven rather than assumed.

    A refusal can be raised with a mutation already staged, so the commit that
    lands the meter row must not be the thing that lands the mutation. This drives
    ``_metered`` directly — a row is added inside the block and the block then
    raises — and reads the outcome both ways from a session of its own: the log row
    is there, and the staged write is not.
    """

    bank = db_session.get(Bank, BANK_ID)
    assert bank is not None
    access = BiReadAccess(
        ctx=TenantContext(organization_id=ORG_1, actor_user_id=USER_1),
        bank=bank,
        principal_user_id=USER_1,
        authorization_version=_authv(db_session, USER_1),
    )
    staged_key = "custom.never_committed"
    metered = manage_bi_content._metered  # noqa: SLF001 - the unit under test

    with pytest.raises(RuntimeError), metered(db_session, access, "measures.create", "probe"):
        db_session.add(
            BiMeasure(
                organization_id=ORG_1,
                bank_id=bank.id,
                owner_user_id=USER_1,
                measure_key=staged_key,
                label="Never committed",
                description="",
                expression=OWN_FORMULA,
                expression_digest="0" * 64,
                referenced_members=[LOANS],
                value_type="amount",
                favourable_direction="neutral",
                state="personal",
            )
        )
        db_session.flush()
        raise RuntimeError("the refusal")

    session = get_sessionmaker()()
    try:
        logged = list(session.scalars(select(BiQueryLog)))
        measures = list(
            session.scalars(select(BiMeasure).where(BiMeasure.measure_key == staged_key))
        )
    finally:
        session.close()
    assert len(logged) == 1
    assert logged[0].decision == query_log.DECISION_DENIED
    assert measures == [], "the metering commit must not land a staged write"
