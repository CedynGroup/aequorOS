"""The data scope through the routes: one unremovable filter, on every surface.

``tests/services/bi/test_data_scope.py`` proves the resolution and the two
refusals against the compiler. This file proves the same properties where a
caller can actually reach them, because a filter that is right in the service and
missing from one handler is a leak on that handler:

* a contradictory client filter is INTERSECTED on ``query``, ``grid``, ``drill``,
  ``explain``, ``export`` and the ``insights`` strip;
* an institution-grain measure is 403 to a branch-scoped reader, 200 to an
  institution-wide one, and the refusal names the measure so an Org Owner can see
  which grant to widen;
* the catalogue and the certified packs advertise only what the query path will
  serve, so a scoped reader is not handed a dashboard of 403s;
* two readers with different scopes never share a representation: different
  ETags, and a 304 for one is a 200 for the other;
* the ``bi_query_log`` row records THAT the read was narrowed, and no branch code.

The mart is ``test_bi_routes``'s: B1 carries 100 + 300 loans, B2 carries 200 plus
a 50 deposit, so the institution's loans are 600 and a B1-scoped reader may see
exactly 400. Every expected number below is one of those three.
"""

from __future__ import annotations

import csv
import io
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.authorization import (
    DataScope,
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.config import get_settings
from app.models import AuthorizationBinding, Bank, User
from app.models.bi import BiDimBranch, BiQueryLog
from app.services import authorization
from app.services.bi.authorization import (
    REASON_BANK_WIDE_FIGURE,
    REASON_INSTITUTION_GRAIN,
)
from tests.api.helpers import ORG_1, USER_1, headers
from tests.api.test_bi_routes import AS_OF, BANK_ID, BASE, seed_bi_mart

PORTFOLIO_MEASURE = "loans.balance_rc"
INSTITUTION_MEASURE = "engine.car_pct.crd.official"
BANK_WIDE_MEASURE = "loans.balance_rc.budget.actual"

#: Every module and sensitivity, so the only thing under test below is the SLICE.
FULL_AUTHORITY = (ModuleScope.ALL, SensitivityScope.ALL)

B1_ONLY = 400.0
#: B2's own loans. Named so the intersection test can assert the CLIENT's
#: requested branch is not served either — the half a vacuous assertion missed.
B2_ONLY = 200.0
INSTITUTION_LOANS = 600.0


@pytest.fixture
def mart(db_session: Session) -> Bank:
    return seed_bi_mart(db_session)


@pytest.fixture
def bi_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BI_ENABLED", "1")
    get_settings.cache_clear()


def _grant(  # noqa: PLR0913 - one keyword per binding dimension
    db: Session,
    *,
    scope: DataScope = DataScope.ALL,
    values: tuple[str, ...] = (),
    bundle: RoleBundle = RoleBundle.VIEWER,
    principal: Any = USER_1,
    replace_existing: bool = True,
) -> int:
    """Give ``principal`` exactly one sentence with this data scope; return ``authv``."""

    module, sensitivity = FULL_AUTHORITY
    if replace_existing:
        db.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.principal_user_id == principal)
        )
        db.commit()
    authorization.create_role_binding(
        db,
        organization_id=ORG_1,
        principal_user_id=principal,
        principal_type=PrincipalType.HUMAN,
        role_bundle=bundle,
        scope=authorization.BindingScope(
            InstitutionScope.INSTITUTION, BANK_ID, module, sensitivity, scope, values
        ),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
        reason="Exercise the BI data scope end to end.",
    )
    db.commit()
    user = db.get(User, principal)
    assert user is not None
    db.refresh(user)
    return user.authorization_version


def _regions(db: Session, assignment: dict[str, str]) -> None:
    for code, region in assignment.items():
        db.execute(
            update(BiDimBranch)
            .where(
                BiDimBranch.organization_id == ORG_1,
                BiDimBranch.bank_id == BANK_ID,
                BiDimBranch.branch_code == code,
            )
            .values(region=region)
        )
    db.commit()


def _headers(version: int, extra: dict[str, str] | None = None) -> dict[str, str]:
    sent = headers(authorization_version=version)
    if extra:
        sent.update(extra)
    return sent


def _post(
    client: TestClient,
    suffix: str,
    body: dict[str, Any],
    version: int,
    *,
    extra: dict[str, str] | None = None,
) -> Any:
    return client.post(f"{BASE}{suffix}", json=body, headers=_headers(version, extra))


def _get(
    client: TestClient, suffix: str, version: int, *, extra: dict[str, str] | None = None
) -> Any:
    return client.get(f"{BASE}{suffix}", headers=_headers(version, extra))


def _query_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "measures": [PORTFOLIO_MEASURE],
        "time": {"as_of": AS_OF.isoformat()},
    }
    body.update(overrides)
    return body


#: The client's own filter on the SAME dimension the grant narrows, naming the
#: branch the grant excludes. Honouring it would serve B2; dropping the grant's
#: filter would serve 600; the intersection is the only right answer.
CONTRADICTORY = [{"member": "branch.code", "op": "in", "values": ["B2"]}]


def _figures(payload: Any) -> list[float | None]:
    """Every numeric cell of a query/grid/drill result payload."""

    rows = payload.get("rows") or []
    out: list[float | None] = []
    for row in rows:
        for value in row:
            if value is None:
                out.append(None)
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                out.append(float(value))
            elif isinstance(value, str):
                try:
                    out.append(float(value))
                except ValueError:
                    continue
    return out


# --- the intersection, on every surface that compiles ----------------------------------------


def test_a_scoped_query_serves_only_its_slice(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    scoped = _grant(db_session, scope=DataScope.BRANCH, values=("B1",))
    response = _post(db_client, "/query", _query_body(), scoped)
    assert response.status_code == 200, response.text
    assert _figures(response.json()) == [B1_ONLY]


def test_an_institution_wide_query_still_serves_the_whole_book(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """The control: without it, "the scoped answer is 400" proves nothing."""
    whole = _grant(db_session)
    response = _post(db_client, "/query", _query_body(), whole)
    assert response.status_code == 200, response.text
    assert _figures(response.json()) == [INSTITUTION_LOANS]


@pytest.mark.parametrize(
    ("suffix", "body"),
    [
        ("/query", _query_body(filters=CONTRADICTORY)),
        ("/grid", {"query": _query_body(filters=CONTRADICTORY)}),
        (
            "/drill",
            {"query": _query_body(filters=CONTRADICTORY, dimensions=["position.source_reference"])},
        ),
        (
            "/query",
            _query_body(
                filters=CONTRADICTORY,
                dimensions=["loan.sector"],
                top_n={"dimension": "loan.sector", "n": 1},
            ),
        ),
        (
            "/query",
            _query_body(
                filters=CONTRADICTORY,
                dimensions=["branch.code"],
                pivot={"dimension": "loan.sector"},
            ),
        ),
        (
            "/query",
            {
                "measures": [PORTFOLIO_MEASURE],
                "filters": CONTRADICTORY,
                "time": {"as_of": AS_OF.isoformat(), "compare_to": "2026-07-31"},
            },
        ),
    ],
    ids=["query", "grid", "drill", "top_n", "pivot", "comparison"],
)
def test_a_contradictory_client_filter_is_intersected_on_every_shape(  # noqa: PLR0913 - one fixture per guard
    db_client: TestClient,
    db_session: Session,
    mart: Bank,
    bi_on: None,
    suffix: str,
    body: dict[str, Any],
) -> None:
    """The grant says B1, the request says B2: the answer is neither branch.

    ``injected_filters`` reaches ``compile_query`` beside the ``BiQuery``, so the
    client's own predicates cannot reach it — and each of these six is a distinct
    code path in the compiler (the select, the paged grid, the record-level drill,
    the Top-N ranking subquery, the pivot's distinct-value probe and the
    comparison window), any one of which could drop a filter on its own.
    """
    scoped = _grant(db_session, scope=DataScope.BRANCH, values=("B1",))
    response = _post(db_client, suffix, body, scoped)
    assert response.status_code == 200, response.text
    raw = _figures(response.json())
    figures = [figure for figure in raw if figure is not None]

    # Audit A10-08: this was ``all(figure == 0.0 for figure in figures)`` over a
    # list that is EMPTY on all six shapes — vacuously true, and it would have
    # stayed true if the filter had been dropped in a way that returned no rows
    # for some other reason. What actually proves the intersection is the two
    # figures that must NOT appear: B2's 200, which honouring the client's filter
    # would serve, and the institution's 600, which dropping the grant's would.
    assert B2_ONLY not in figures, f"the client's excluded branch was served: {raw}"
    assert INSTITUTION_LOANS not in figures, (
        f"the grant's filter was dropped and the whole book served: {raw}"
    )
    assert B1_ONLY not in figures, (
        "B1's own total appeared, so the client's B2 filter was dropped rather "
        f"than intersected: {raw}"
    )
    # Whatever IS served must be nothing or zero — the intersection of B1 and B2
    # is empty, and an empty intersection is not a figure.
    assert all(figure == 0.0 for figure in figures), raw
    assert "B2" not in response.text, response.text


def test_explain_cannot_be_used_to_read_outside_the_slice(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """``explain`` discloses NO figure, so there is no figure to read outside the slice.

    This test originally asserted ``payload["value"] == B1_ONLY`` and failed with
    ``KeyError: 'value'``. The assertion was wrong, not the route: ``BiExplainRead``
    carries the measure's declaration, the mart table, the window, the engine rule
    and the reconciliation checks, and **no number anywhere** — not on the response
    and not on a component, which holds only ``role``, ``member_id`` and ``label``.
    The route compiles the query (which is how the source table and the aggregate
    decision are known) and deliberately never executes it, because the figure came
    from ``query``.

    So the property here is stronger than "explain shows the slice's number": the
    surface cannot leak a figure at all. Pinning it that way means the route cannot
    later start returning one without this test noticing, which is what an
    equality assertion on a value would have quietly permitted.

    The narrowing is still proved to reach this handler, two ways that do not
    depend on a figure: the compiled source table is recorded, and the query-log
    row carries the injected scope member. The refusal half lives in
    ``test_an_institution_grain_measure_is_403_to_a_branch_scoped_reader``.
    """

    scoped = _grant(db_session, scope=DataScope.BRANCH, values=("B1",))
    body = {"query": _query_body(), "measure": PORTFOLIO_MEASURE}
    response = _post(db_client, "/explain", body, scoped)
    assert response.status_code == 200, response.text
    payload = response.json()

    # The measure's own value is absent by design. What the response DID carry was
    # the reconciliation operands, and on R7 those are the institution's total
    # loans against the ledger — the exact figure a B1 grant excludes. That was a
    # real leak and is now withheld; the walk below is what caught it, so it stays.
    def _numbers(node: Any, path: str = "") -> list[str]:
        if isinstance(node, dict):
            return [n for key, value in node.items() for n in _numbers(value, f"{path}.{key}")]
        if isinstance(node, list):
            return [n for i, value in enumerate(node) for n in _numbers(value, f"{path}[{i}]")]
        if isinstance(node, bool) or node is None:
            return []
        if isinstance(node, int | float):
            return [f"{path}={node}"]
        return []

    # ``input_hash`` and the like are strings; a genuine measured value would be a
    # JSON number. Dates serialise as strings too, so this is the figure check.
    assert _numbers(payload) == [], (
        f"explain returned a numeric field, so it now discloses a figure: {_numbers(payload)}"
    )
    assert str(B1_ONLY) not in response.text
    assert str(INSTITUTION_LOANS) not in response.text
    # The verdict is KEPT — a scoped reader still needs to know whether the book
    # they can see reconciles — and every check says why its numbers are absent.
    assert payload["checks"], "the reconciliation verdicts were dropped, not just their operands"
    for check in payload["checks"]:
        assert check["status"], check
        assert check["lhs"] is None and check["rhs"] is None, check
        assert check["difference"] is None and check["tolerance"] is None, check
        assert check["detail"] == {"reason": "withheld_outside_data_scope"}, check

    # And the scope did reach this handler: the log row names the injected scope
    # member, and never a branch code.
    logged = db_session.scalars(
        select(BiQueryLog)
        .where(BiQueryLog.organization_id == ORG_1, BiQueryLog.surface == "explain")
        .order_by(BiQueryLog.queried_at.desc())
    ).first()
    assert logged is not None, "explain recorded no query-log row"
    assert any("branch" in member for member in logged.member_ids), logged.member_ids
    assert "B1" not in (logged.denied_members or []) and "B1" not in logged.member_ids


def test_an_institution_wide_reader_still_receives_the_reconciliation_operands(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """The control for the test above, on both surfaces that serve check evidence.

    Withholding the operands from a SCOPED reader is only correct if an
    institution-wide reader still gets them. Without this, the change that closed
    that leak could have removed the reconciliation evidence for everybody and the
    withholding test would still have passed — the failure mode of every
    "the field is absent" assertion written without its positive half.

    ``explain`` and ``trust`` both build their checks through ``_check_read``, so
    both are exercised: a regression in the shared helper has to break one of them.
    """

    whole = _grant(db_session)

    explained = _post(
        db_client, "/explain", {"query": _query_body(), "measure": PORTFOLIO_MEASURE}, whole
    )
    assert explained.status_code == 200, explained.text
    assessed = [
        check
        for check in explained.json()["checks"]
        if check["detail"].get("reason") != "not_assessed"
    ]
    assert assessed, "the seeded reconciliation check did not reach explain at all"
    assert any(check["lhs"] is not None for check in assessed), assessed
    assert all(
        check["detail"].get("reason") != "withheld_outside_data_scope" for check in assessed
    ), assessed

    trust = _get(db_client, f"/trust?as_of={AS_OF.isoformat()}", whole)
    assert trust.status_code == 200, trust.text
    trusted = [
        check for check in trust.json()["checks"] if check["detail"].get("reason") != "not_assessed"
    ]
    assert trusted, "the seeded reconciliation check did not reach trust at all"
    assert any(check["lhs"] is not None for check in trusted), trusted


def test_a_scoped_trust_read_is_REFUSED_rather_than_narrowed(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """``trust`` fails closed for a scoped reader, which is why only ``explain`` leaked.

    Written expecting the same withholding ``explain`` now does, and it is not what
    happens: the route answers 403 ``bi_data_scope_unsupported`` before it builds a
    single check. That is the stronger answer and it was already there, so the
    reconciliation-operand leak existed on ``explain`` alone — ``explain`` serves a
    scoped reader by design, because provenance is not a figure, and that is exactly
    what made the operands the one number on its response.

    Pinned because the two surfaces now differ deliberately: if ``trust`` ever stops
    refusing, it must start withholding, and this test is what says so.
    """

    scoped = _grant(db_session, scope=DataScope.BRANCH, values=("B1",))
    response = _get(db_client, f"/trust?as_of={AS_OF.isoformat()}", scoped)
    assert response.status_code == 403, response.text
    assert response.json()["error"]["details"]["error_code"] == "bi_data_scope_unsupported"
    # And it discloses nothing on the way out.
    assert str(INSTITUTION_LOANS) not in response.text
    assert "lhs" not in response.text
    assert "checks" not in response.text


def test_a_scoped_export_carries_its_slice_and_says_so_on_the_artifact(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """A spreadsheet of one branch's book must not read "Whole institution"."""
    scoped = _grant(db_session, scope=DataScope.BRANCH, values=("B1",))
    # ``BiExportRequest`` is closed over exactly {query, format}: there is
    # deliberately no reason field and no disclosure flag, because the class is
    # read from the members the query touches, not stated by the caller.
    body = {"query": _query_body(filters=CONTRADICTORY), "format": "csv"}
    response = _post(db_client, "/export", body, scoped)
    assert response.status_code == 200, response.text
    text = response.text
    assert "Branches: B1" in text
    assert "Whole institution" not in text
    # And the figures are the intersection, not B2's 200.
    cells = [cell for row in csv.reader(io.StringIO(text)) for cell in row]
    assert "200" not in cells
    assert "600" not in cells


def test_an_institution_wide_export_still_says_whole_institution(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    whole = _grant(db_session)
    body = {"query": _query_body(), "format": "csv"}
    response = _post(db_client, "/export", body, whole)
    assert response.status_code == 200, response.text
    assert "Whole institution" in response.text


def test_a_region_scope_serves_the_regions_branches_through_the_route(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    _regions(db_session, {"B1": "North", "B2": "South"})
    scoped = _grant(db_session, scope=DataScope.REGION, values=("North",))
    response = _post(db_client, "/query", _query_body(), scoped)
    assert response.status_code == 200, response.text
    assert _figures(response.json()) == [B1_ONLY]


def test_a_region_with_no_branches_serves_nothing_rather_than_everything(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    _regions(db_session, {"B1": "North", "B2": "South"})
    scoped = _grant(db_session, scope=DataScope.REGION, values=("Volta",))
    response = _post(db_client, "/query", _query_body(), scoped)
    assert response.status_code == 200, response.text
    assert _figures(response.json()) in ([None], [0.0])
    assert INSTITUTION_LOANS not in [f for f in _figures(response.json()) if f is not None]


def test_a_granted_branch_code_never_ingested_serves_nothing(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    scoped = _grant(db_session, scope=DataScope.BRANCH, values=("BR-NEVER-FED",))
    response = _post(db_client, "/query", _query_body(), scoped)
    assert response.status_code == 200, response.text
    assert INSTITUTION_LOANS not in [f for f in _figures(response.json()) if f is not None]
    assert B1_ONLY not in [f for f in _figures(response.json()) if f is not None]


# --- the two refusals, as a caller meets them ------------------------------------------------


def test_an_institution_grain_measure_is_403_to_a_branch_scoped_reader(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    scoped = _grant(db_session, scope=DataScope.BRANCH, values=("B1",))
    response = _post(db_client, "/query", _query_body(measures=[INSTITUTION_MEASURE]), scoped)
    assert response.status_code == 403, response.text
    detail = response.json()["error"]["details"]
    # The refusal NAMES the measure and its label, which is the existing contract
    # of every BI denial: what the reader needs is the grant they are missing, and
    # a member id is not a figure. The reason is the one the log and the telemetry
    # carry, so an operator reading either sees the same rule.
    assert detail["denied_members"] == [INSTITUTION_MEASURE]
    assert detail["reason"] == REASON_INSTITUTION_GRAIN
    assert detail["denied_member_labels"]
    # No figure anywhere: the institution's CAR is 14.25 and its loans are 600.
    assert "14.25" not in response.text
    assert str(int(INSTITUTION_LOANS)) not in response.text


def test_the_query_log_row_names_the_reason_and_the_denied_measure(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    scoped = _grant(db_session, scope=DataScope.BRANCH, values=("B1",))
    _post(db_client, "/query", _query_body(measures=[INSTITUTION_MEASURE]), scoped)
    rows = list(
        db_session.execute(
            BiQueryLog.__table__.select().where(BiQueryLog.bank_id == BANK_ID)
        ).mappings()
    )
    denied = [row for row in rows if row["decision"] == "denied"]
    assert denied, rows
    assert INSTITUTION_MEASURE in denied[-1]["denied_members"]


def test_the_same_measure_is_served_to_an_institution_wide_reader(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    whole = _grant(db_session)
    response = _post(db_client, "/query", _query_body(measures=[INSTITUTION_MEASURE]), whole)
    assert response.status_code == 200, response.text
    assert _figures(response.json()) == [14.25]


def test_a_bank_wide_target_figure_is_403_to_a_scoped_reader(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    scoped = _grant(db_session, scope=DataScope.BRANCH, values=("B1",))
    response = _post(db_client, "/query", _query_body(measures=[BANK_WIDE_MEASURE]), scoped)
    assert response.status_code == 403, response.text


def test_the_two_refusals_are_distinct_reasons_in_the_log(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """One string per rule, so one telemetry filter finds each."""
    assert REASON_INSTITUTION_GRAIN != REASON_BANK_WIDE_FIGURE
    assert REASON_INSTITUTION_GRAIN.endswith("requires_whole_institution")
    assert REASON_BANK_WIDE_FIGURE.endswith("requires_whole_institution")


# --- the narrowed surfaces: the catalogue and the certified packs -----------------------------


def test_the_catalogue_advertises_no_figure_a_scoped_reader_would_be_refused(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """Otherwise a branch manager opens Explore and every ratio 403s."""
    scoped = _grant(db_session, scope=DataScope.BRANCH, values=("B1",))
    response = _get(db_client, "/catalogue", scoped)
    assert response.status_code == 200, response.text
    advertised = {measure["id"] for measure in response.json()["measures"]}
    assert PORTFOLIO_MEASURE in advertised
    assert INSTITUTION_MEASURE not in advertised
    assert BANK_WIDE_MEASURE not in advertised
    # The branch dimensions stay: slicing is how a scoped reader reads at all.
    dimensions = {dimension["id"] for dimension in response.json()["dimensions"]}
    assert "branch.code" in dimensions


def test_the_catalogue_still_advertises_engine_figures_to_a_whole_institution_reader(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    whole = _grant(db_session)
    response = _get(db_client, "/catalogue", whole)
    assert response.status_code == 200, response.text
    advertised = {measure["id"] for measure in response.json()["measures"]}
    assert INSTITUTION_MEASURE in advertised
    assert PORTFOLIO_MEASURE in advertised


def test_a_scoped_reader_is_not_handed_a_certified_pack_of_refusals(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """The packs name institution ratios, so a scoped reader's widgets are refused.

    What matters is that the REFUSAL is stated on the widget rather than left for
    ``/bi/query`` to 403 after the page has already drawn a tile.
    """
    scoped = _grant(db_session, scope=DataScope.BRANCH, values=("B1",))
    response = _get(db_client, "/packs?as_of=" + AS_OF.isoformat(), scoped)
    assert response.status_code == 200, response.text
    packs = response.json()["packs"]
    assert packs
    for pack in packs:
        for widget in pack["widgets"]:
            if widget["access"] != "granted" or widget.get("query") is None:
                continue
            measures = widget["query"]["measures"]
            assert INSTITUTION_MEASURE not in measures
            assert BANK_WIDE_MEASURE not in measures


# --- no two scopes share a representation ----------------------------------------------------


def test_two_scopes_do_not_share_an_etag(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    whole = _grant(db_session)
    wide = _post(db_client, "/query", _query_body(), whole)
    assert wide.status_code == 200, wide.text
    wide_etag = wide.headers["ETag"]

    scoped = _grant(db_session, scope=DataScope.BRANCH, values=("B1",))
    narrow = _post(db_client, "/query", _query_body(), scoped)
    assert narrow.status_code == 200, narrow.text
    assert narrow.headers["ETag"] != wide_etag


def test_the_wider_etag_does_not_revalidate_the_narrower_answer(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """The highest-consequence cache bug: one principal's 304 serving another's rows."""
    whole = _grant(db_session)
    wide = _post(db_client, "/query", _query_body(), whole)
    wide_etag = wide.headers["ETag"]

    scoped = _grant(db_session, scope=DataScope.BRANCH, values=("B1",))
    replay = _post(db_client, "/query", _query_body(), scoped, extra={"If-None-Match": wide_etag})
    assert replay.status_code == 200, replay.text
    assert _figures(replay.json()) == [B1_ONLY]


def test_a_narrowed_grant_invalidates_the_readers_own_cache(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    whole = _grant(db_session)
    first = _post(db_client, "/query", _query_body(), whole)
    etag = first.headers["ETag"]
    assert (
        _post(db_client, "/query", _query_body(), whole, extra={"If-None-Match": etag}).status_code
        == 304
    )

    scoped = _grant(db_session, scope=DataScope.BRANCH, values=("B1",))
    after = _post(db_client, "/query", _query_body(), scoped, extra={"If-None-Match": etag})
    assert after.status_code == 200, after.text
    assert _figures(after.json()) == [B1_ONLY]


def test_a_branch_joining_a_granted_region_moves_the_etag(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """The resolution is part of the answer's identity, not only the bindings.

    A region grant's branch set changes with the book, and neither ``authv`` nor a
    binding id moves when it does — so the ETag has to carry the RESOLVED scope.
    """
    _regions(db_session, {"B1": "North", "B2": "South"})
    scoped = _grant(db_session, scope=DataScope.REGION, values=("North",))
    before = _post(db_client, "/query", _query_body(), scoped)
    assert before.status_code == 200, before.text
    assert _figures(before.json()) == [B1_ONLY]
    etag = before.headers["ETag"]

    _regions(db_session, {"B2": "North"})
    after = _post(db_client, "/query", _query_body(), scoped, extra={"If-None-Match": etag})
    assert after.status_code == 200, after.text
    assert after.headers["ETag"] != etag
    assert _figures(after.json()) == [INSTITUTION_LOANS]


# --- what the log records about a SERVED scoped read -------------------------------------------


def test_a_served_scoped_read_records_the_scope_member_and_no_branch_code(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """``bi_query_log`` is value-free by rule; what it gains is the MEMBER id."""
    scoped = _grant(db_session, scope=DataScope.BRANCH, values=("B1",))
    assert _post(db_client, "/query", _query_body(), scoped).status_code == 200
    rows = list(
        db_session.execute(
            BiQueryLog.__table__.select().where(BiQueryLog.bank_id == BANK_ID)
        ).mappings()
    )
    served = [row for row in rows if row["decision"] == "allowed"]
    assert served, rows
    assert "branch.code" in served[-1]["member_ids"]
    assert "B1" not in served[-1]["member_ids"]
    assert "B1" not in served[-1]["query_hash"]


def _grant_pair_scoped(  # noqa: PLR0913 - one keyword per binding dimension
    db: Session,
    *,
    module: ModuleScope,
    sensitivity: SensitivityScope,
    scope: DataScope,
    values: tuple[str, ...] = (),
    principal: Any = USER_1,
) -> int:
    """ADD one binding over an exact (module, sensitivity), keeping the others.

    ``_grant`` above always writes ``ModuleScope.ALL``/``SensitivityScope.ALL``
    and deletes what was there, so every scope test until now exercised a
    principal holding exactly ONE binding. That is why audit A10-01 survived: the
    bypass needs two bindings whose data scopes differ.
    """

    authorization.create_role_binding(
        db,
        organization_id=ORG_1,
        principal_user_id=principal,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.VIEWER,
        scope=authorization.BindingScope(
            InstitutionScope.INSTITUTION, BANK_ID, module, sensitivity, scope, values
        ),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
        reason="A second sentence over one module, for the cross-pair reduction.",
    )
    db.commit()
    user = db.get(User, principal)
    assert user is not None
    db.refresh(user)
    return user.authorization_version


def test_an_institution_wide_grant_on_one_MODULE_does_not_widen_another(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """Audit A10-01, the blocker this file's own helper was hiding.

    ``authorize_query`` evaluates each ``(module, sensitivity)`` pair separately,
    and used to UNION every pair's matching binding ids before reducing them once.
    ``reduce_data_scope``'s rule 2 — any binding of kind ``all`` wins — is sound
    only WITHIN one resource's matches; across resources, an ``all`` binding that
    authorized the ``risk``/``aggregated`` pair discarded the branch restriction
    that applied to the credit pair.

    The reader triggered it themselves, which is what made it serious: 32 of the
    catalogue's 66 dimensions are ``risk``/``aggregated``, so merely breaking a
    figure down BY DATE pulled in an institution-wide binding and widened the
    whole answer. No privileged action, no crafted request.

    Two bindings, both written through the same ``create_role_binding`` an Org
    Owner's composer calls: one narrowing every module to branch B1, one covering
    the risk module institution-wide.
    """

    _grant(db_session, scope=DataScope.BRANCH, values=("B1",))
    authv = _grant_pair_scoped(
        db_session,
        module=ModuleScope.RISK,
        sensitivity=SensitivityScope.ALL,
        scope=DataScope.ALL,
    )

    # Without a dimension, one pair matches and the slice was always right.
    plain = _post(db_client, "/query", _query_body(), authv)
    assert plain.status_code == 200, plain.text
    assert _figures(plain.json()) == [B1_ONLY]

    # WITH a risk dimension, a second pair matches. This is the bypass: it used to
    # return the institution's 600 (and, broken down by branch, B2's rows too).
    widened = _post(db_client, "/query", _query_body(dimensions=["branch.code"]), authv)
    assert widened.status_code == 200, widened.text
    figures = _figures(widened.json())
    assert INSTITUTION_LOANS not in figures, (
        "an institution-wide grant on one module widened another module's slice "
        f"(A10-01); served {figures}"
    )
    assert figures == [B1_ONLY], figures
    assert "B2" not in widened.text, widened.text


def test_an_institution_grain_measure_stays_refused_when_another_pair_is_wide(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """The other half of A10-01: the grain rule was bypassable the same way.

    A branch-scoped reader is refused an institution ratio. Adding a dimension
    from a module they hold institution-wide used to make the SAME measure return
    200, because the widened scope then satisfied the whole-institution test.
    """

    _grant(db_session, scope=DataScope.BRANCH, values=("B1",))
    authv = _grant_pair_scoped(
        db_session,
        module=ModuleScope.RISK,
        sensitivity=SensitivityScope.ALL,
        scope=DataScope.ALL,
    )

    bare = _post(db_client, "/query", _query_body(measures=[INSTITUTION_MEASURE]), authv)
    assert bare.status_code == 403, bare.text
    assert bare.json()["error"]["details"]["reason"] == REASON_INSTITUTION_GRAIN

    with_dimension = _post(
        db_client,
        "/query",
        _query_body(measures=[INSTITUTION_MEASURE], dimensions=["time.date"]),
        authv,
    )
    assert with_dimension.status_code == 403, (
        "adding a dimension from an institution-wide module turned a refused "
        f"institution ratio into a served one (A10-01): {with_dimension.text}"
    )
    assert with_dimension.json()["error"]["details"]["reason"] == REASON_INSTITUTION_GRAIN


def test_two_irreconcilable_narrow_scopes_are_REFUSED_not_intersected(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """Audit A10-04: ``data_scope_conflict`` was implemented three times and tested nowhere.

    Fixing A10-01 added a fourth site, so the rule now decides whether a query is
    served on every BI surface — and a refusal nothing exercises is a refusal
    nobody knows works. This is that exercise.

    The shape: one binding narrows the credit module to branch B1, another narrows
    the risk module to region North. Both pairs are authorized, and each admits a
    DIFFERENT slice. There is no honest answer: intersecting them would invent a
    slice neither sentence granted, and picking one would silently ignore the
    other. So the query is refused, and the reason names the conflict rather than
    a missing grant, because the reader is not missing anything — their two
    sentences cannot be answered together.
    """

    _regions(db_session, {"B1": "North", "B2": "South"})
    db_session.execute(
        delete(AuthorizationBinding).where(AuthorizationBinding.principal_user_id == USER_1)
    )
    db_session.commit()
    _grant_pair_scoped(
        db_session,
        module=ModuleScope.CREDIT,
        sensitivity=SensitivityScope.ALL,
        scope=DataScope.BRANCH,
        values=("B1",),
    )
    authv = _grant_pair_scoped(
        db_session,
        module=ModuleScope.RISK,
        sensitivity=SensitivityScope.ALL,
        scope=DataScope.REGION,
        values=("North",),
    )

    # One pair alone is answerable, so the refusal below is about the COMBINATION
    # and not about either sentence being unusable.
    alone = _post(db_client, "/query", _query_body(), authv)
    assert alone.status_code == 200, alone.text

    conflicted = _post(db_client, "/query", _query_body(dimensions=["branch.code"]), authv)
    assert conflicted.status_code == 403, conflicted.text
    details = conflicted.json()["error"]["details"]
    assert details["reason"] == "data_scope_conflict", details
    # And nothing of the book leaks in the refusal.
    assert str(B1_ONLY) not in conflicted.text
    assert str(INSTITUTION_LOANS) not in conflicted.text


def test_the_query_answer_STATES_the_slice_it_covers(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """Audit A10-12: a narrowed figure was indistinguishable from a smaller bank.

    `BiQueryResult` carried no scope, so a branch-scoped reader's 400 looked exactly
    like an institution whose whole book is 400. The browser could only hedge —
    "some of what is shown here may be narrowed, and this page cannot say which" —
    which is true and nearly useless. The answer now says what it covers.

    ``all`` is emitted too, deliberately: a client must never have to treat absent
    and whole-institution as the same thing, or a stale client that never learned
    about scopes reads a narrowed answer as the institution's.
    """

    scoped = _grant(db_session, scope=DataScope.BRANCH, values=("B1",))
    narrowed = _post(db_client, "/query", _query_body(), scoped)
    assert narrowed.status_code == 200, narrowed.text
    payload = narrowed.json()
    assert _figures(payload) == [B1_ONLY]
    assert payload["data_scope"] == {
        "kind": "branch",
        "branches": ["B1"],
        "regions": [],
        "unresolved_regions": [],
    }, payload["data_scope"]

    whole = _grant(db_session)
    institution_wide = _post(db_client, "/query", _query_body(), whole)
    assert institution_wide.status_code == 200, institution_wide.text
    assert _figures(institution_wide.json()) == [INSTITUTION_LOANS]
    assert institution_wide.json()["data_scope"] == {
        "kind": "all",
        "branches": [],
        "regions": [],
        "unresolved_regions": [],
    }


def test_a_region_that_names_no_branch_says_so_rather_than_reporting_an_empty_book(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """Zero rows because a GRANT matches nothing is a grant to correct, not a book.

    The only ``unresolved_regions`` case this layer can determine honestly: every
    declared region resolving to no branch at all. A partially resolved set is not
    derivable from the resolved scope, which keeps the union of codes rather than
    the per-region mapping, so it is left empty rather than guessed.
    """

    _regions(db_session, {"B1": "North", "B2": "South"})
    scoped = _grant(db_session, scope=DataScope.REGION, values=("Nowhere",))
    response = _post(db_client, "/query", _query_body(), scoped)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert _figures(payload) == [] or all(value is None for value in _figures(payload)), payload
    assert payload["data_scope"]["unresolved_regions"] == ["Nowhere"], payload["data_scope"]
    # And the institution's own figure is nowhere in the response.
    assert str(INSTITUTION_LOANS) not in response.text
