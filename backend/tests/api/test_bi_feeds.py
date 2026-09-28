"""``GET /banks/{id}/bi/feeds/{dataset}`` end to end: the credential and the cursor.

Two properties are worth more than everything else in this file and are asserted
in both directions:

* **the two machine bundles cannot substitute for one another.** A data-push key
  is refused the feed and an analytics-feed key is refused a push, and neither
  refusal is a name check — ``integration_writer`` carries ``INGEST`` and
  ``bi_reader`` carries ``VIEW``, so the two routes refuse each other's credential
  by asking for what they actually need.
* **a restatement is delivered.** The cursor is a position in BUILD time, so a
  reporting date that is rebuilt reappears in the next pull. A business-date
  cursor would pass it once and never return, and the report server would then
  disagree with the platform for as long as the model lived, silently.

The mart is the read surface's own fixture (``tests/api/test_bi_routes``) plus a
SECOND reporting date added here, because a cursor cannot be tested over one
date. Every figure below is derivable by reading the two.

**One piece of wiring this file supplies itself**, and it is named rather than
hidden: the route's mount (``app/api/router.py``) and its entry in
``deps.INTEGRATION_KEY_ROUTES`` belong to another track in this phase. The
``feed_wiring`` fixture applies exactly those two lines — the same tuple the
feature exports, the same dependencies the other BI routers are mounted on — and
is a no-op once they are in place. Until they are, the behaviour below is proven
but the route is not reachable in production.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterator
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import deps
from app.api.deps import BANK_ROUTE_DEPENDENCIES, TenantContext
from app.core.authorization import (
    BindingStatus,
    DataScope,
    PrincipalType,
    RoleBundle,
)
from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.features import read_bi_feeds
from app.features.read_bi import require_bi_enabled
from app.models import AuditEvent, AuthorizationBinding, Bank, IntegrationKey, User
from app.models.bi import (
    BiDimBranch,
    BiDimDate,
    BiFactPositionDaily,
    BiMartBuild,
    BiQueryLog,
)
from app.services import integration_keys
from app.services.bi.feeds import authorization as feed_authorization
from app.services.bi.feeds import cursor as feed_cursor
from app.services.bi.feeds import datasets, runner
from tests.api.helpers import ORG_1, USER_1, headers, integration_key_headers
from tests.api.test_bi_routes import (
    AS_OF,
    BANK_ID,
    BUILT_AT,
    SAME_TENANT_BANK_ID,
    SIBLING_BANK_ID,
    seed_bi_mart,
)

#: A SECOND reporting date, built BEFORE August, so the walk order is derivable.
PRIOR = dt.date(2026, 7, 31)
PRIOR_BUILT = dt.datetime(2026, 8, 1, 2, tzinfo=dt.UTC)
PRIOR_BALANCE = Decimal("500")
RESTATED_BALANCE = Decimal("555")
RESTATED_AT = dt.datetime(2026, 9, 5, 2, tzinfo=dt.UTC)

#: Figures the AUGUST fixture holds: B1 carries 100 + 300, B2 carries 200, and a
#: 50 deposit sits outside every loan measure.
B1_AUGUST = Decimal("400")
B2_AUGUST = Decimal("200")

LOAN_BOOK = f"/api/v1/banks/{BANK_ID}/bi/feeds/loan_book"
METRICS = f"/api/v1/banks/{BANK_ID}/bi/feeds/regulatory_metrics"


# --- wiring ---------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def feed_wiring(db_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mount the feed and admit its credential, exactly as production must.

    Both halves are idempotent: if ``app/api/router.py`` already mounts the
    router and ``deps.INTEGRATION_KEY_ROUTES`` already names the route, this
    fixture does nothing. The tuple added to the allow-list is the one the
    feature exports, so the entry a reviewer applies and the entry this test
    applies cannot be different things.
    """

    app = db_client.app
    assert isinstance(app, FastAPI)
    mounted = {route.path for route in app.routes if isinstance(route, APIRoute)}
    if read_bi_feeds.FEED_ROUTE_PATH not in mounted:
        app.include_router(
            read_bi_feeds.router,
            prefix="/api/v1",
            dependencies=(*BANK_ROUTE_DEPENDENCIES, Depends(require_bi_enabled)),
        )
        app.openapi_schema = None
    if read_bi_feeds.FEED_INTEGRATION_KEY_ROUTE not in deps.INTEGRATION_KEY_ROUTES:
        monkeypatch.setattr(
            deps,
            "INTEGRATION_KEY_ROUTES",
            frozenset(deps.INTEGRATION_KEY_ROUTES | {read_bi_feeds.FEED_INTEGRATION_KEY_ROUTE}),
        )


@pytest.fixture
def bi_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Turn the deployment flag on for one test, and forget it again afterwards.

    The teardown clear matters: ``get_settings`` is cached for the life of the
    process, so a fixture that only sets the variable leaves the flag ON for every
    later test in the same file — including the one asserting the surface is
    absent when it is off.
    """

    monkeypatch.setenv("BI_ENABLED", "1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --- the mart -------------------------------------------------------------------------------


def _prior_slice(db: Session, bank: Bank, *, balance: Decimal, finished_at: dt.datetime) -> None:
    """One loan on B1 at :data:`PRIOR`, with its own build bookkeeping."""

    db.add(
        BiFactPositionDaily(
            as_of_date=PRIOR,
            snapshot_id=uuid4(),
            position_id=uuid4(),
            organization_id=bank.organization_id,
            bank_id=bank.id,
            source_system="API_PUSH",
            source_reference="L-PRIOR",
            position_type="LOAN",
            currency=bank.currency,
            balance_native=balance,
            balance_rc=balance,
            fx_unconverted=False,
            classification_exposure_rc=balance,
            non_performing=False,
            branch_code="B1",
            product_code="P-RETAIL",
            product_family="retail_loans",
            sector="trade",
            dpd_band="current",
            grade="standard",
            ifrs9_stage=1,
            builder_version=1,
            built_at=finished_at,
        )
    )
    db.add(
        BiDimDate(
            organization_id=bank.organization_id,
            bank_id=bank.id,
            date=PRIOR,
            has_data=True,
            is_last_in_month=True,
            is_last_in_quarter=True,
            is_last_in_year=False,
            calendar_month=PRIOR.replace(day=1),
            calendar_quarter=dt.date(2026, 7, 1),
            calendar_year=2026,
            fiscal_year=2026,
            fiscal_quarter=3,
            builder_version=1,
            built_at=finished_at,
        )
    )
    for scope in ("positions", "events", "gl", "engine", "dims"):
        db.add(
            BiMartBuild(
                organization_id=bank.organization_id,
                bank_id=bank.id,
                as_of_date=PRIOR,
                scope=scope,
                fingerprint="c" * 64,
                status="succeeded",
                builder_version=1,
                started_at=finished_at,
                finished_at=finished_at,
                row_counts={},
            )
        )


@pytest.fixture
def mart(db_session: Session) -> Bank:
    bank = seed_bi_mart(db_session)
    _prior_slice(db_session, bank, balance=PRIOR_BALANCE, finished_at=PRIOR_BUILT)
    db_session.commit()
    return bank


def _restate_prior(db: Session) -> None:
    """Rebuild the July slice: new figures, new ``finished_at`` on every scope."""

    db.query(BiFactPositionDaily).filter(BiFactPositionDaily.as_of_date == PRIOR).delete()
    bank = db.get(Bank, BANK_ID)
    assert bank is not None
    db.query(BiMartBuild).filter(
        BiMartBuild.as_of_date == PRIOR, BiMartBuild.bank_id == BANK_ID
    ).delete()
    db.query(BiDimDate).filter(BiDimDate.date == PRIOR, BiDimDate.bank_id == BANK_ID).delete()
    _prior_slice(db, bank, balance=RESTATED_BALANCE, finished_at=RESTATED_AT)
    db.commit()


# --- credentials ----------------------------------------------------------------------------


def _reader(
    bank_id: str = BANK_ID,
    *,
    data_scope: DataScope = DataScope.ALL,
    values: tuple[str, ...] = (),
    label: str = "Report server",
) -> dict[str, str]:
    """Issue a real analytics-feed key through the real issuance path."""

    with get_sessionmaker()() as db:
        db.info["organization_id"] = ORG_1
        issued = integration_keys.issue_key(
            db,
            TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1),
            bank_id,
            label,
            purpose="reader",
            data_scope=data_scope,
            data_scope_values=values,
        )
    return {"Authorization": f"Bearer {issued.key}"}


def _pull(
    client: TestClient,
    headers_: dict[str, str],
    *,
    path: str = LOAN_BOOK,
    fmt: str = "ndjson",
    cursor: str | None = None,
) -> Any:
    params: dict[str, str] = {"format": fmt}
    if cursor is not None:
        params["cursor"] = cursor
    return client.get(path, headers=headers_, params=params)


def _rows(response: Any) -> list[dict[str, Any]]:
    """The NDJSON payload, with numbers kept EXACT.

    ``parse_float=Decimal`` is the point: the feed writes an amount as its own
    decimal literal, and reading it back through a float would hide exactly the
    rounding the format exists to avoid — and would make the CSV/NDJSON agreement
    assertion below pass for the wrong reason.
    """

    return [
        json.loads(line, parse_float=Decimal) for line in response.text.splitlines() if line.strip()
    ]


def _cell_text(value: object) -> str:
    """One parsed JSON value as the text the CSV of the same pull must carry."""

    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _log_rows(db: Session) -> list[BiQueryLog]:
    return list(
        db.scalars(
            select(BiQueryLog)
            .where(BiQueryLog.surface == feed_authorization.SURFACE_FEED)
            .order_by(BiQueryLog.queried_at)
        )
    )


# --- the happy path -------------------------------------------------------------------------


def test_a_reader_key_pulls_the_curated_loan_book(
    db_client: TestClient, mart: Bank, bi_on: None
) -> None:
    response = _pull(db_client, _reader())
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/x-ndjson")
    # BOTH directions, so the tuple cannot go stale: every declared header is
    # sent, and every header sent is declared. A response header nobody declared
    # is one no consumer was told about.
    sent = {name.lower() for name in response.headers if name.lower().startswith("x-bi-feed-")}
    assert sent == {name.lower() for name in runner.FEED_HEADERS}

    rows = _rows(response)
    # July's single loan, then August's two branches — oldest BUILD first.
    assert [row["time.date"] for row in rows] == [
        PRIOR.isoformat(),
        AS_OF.isoformat(),
        AS_OF.isoformat(),
    ]
    assert [row["branch.code"] for row in rows] == ["B1", "B1", "B2"]
    assert [Decimal(str(row["loans.balance_rc"])) for row in rows] == [
        PRIOR_BALANCE,
        B1_AUGUST,
        B2_AUGUST,
    ]
    assert [row["loans.count"] for row in rows] == [1, 2, 1]
    # A figure the bank never supplied is empty, never nought.
    assert all(row["loans.provision_held_rc"] is None for row in rows)
    assert response.headers["x-bi-feed-data-scope"] == "Whole institution"
    assert response.headers["x-bi-feed-reporting-dates"] == (
        f"{PRIOR.isoformat()},{AS_OF.isoformat()}"
    )
    assert response.headers["x-bi-feed-columns"].startswith("time.date,branch.code")
    assert response.headers["x-bi-feed-unit"] == "GHS"
    assert response.headers["cache-control"] == "private, no-store"


def test_csv_and_ndjson_carry_the_same_figures(
    db_client: TestClient, mart: Bank, bi_on: None
) -> None:
    reader = _reader()
    as_json = _rows(_pull(db_client, reader, fmt="ndjson"))
    csv_response = _pull(db_client, reader, fmt="csv")
    assert csv_response.headers["content-type"].startswith("text/csv")
    lines = csv_response.text.splitlines()
    header = lines[0].split(",")
    assert header == list(as_json[0])
    for row, line in zip(as_json, lines[1:], strict=True):
        cells = line.split(",")
        for column, cell in zip(header, cells, strict=True):
            value = row[column]
            assert cell == ("" if value is None else str(value)), column


def test_regulatory_metrics_serves_a_null_for_every_figure_the_bank_has_not_computed(
    db_client: TestClient, mart: Bank, bi_on: None
) -> None:
    """The fixture holds one metric copy. The other fifteen are absent, not zero.

    It also shows the header doing its job: the pull COVERS both reporting dates
    (both were built) while only August produces a row, because no engine figure
    was copied for July. The consumer replaces both dates, which correctly leaves
    July empty — a payload that listed only the dates it had rows for would leave
    a stale July in the report model for ever.
    """

    response = _pull(db_client, _reader(), path=METRICS)
    rows = _rows(response)
    assert response.headers["x-bi-feed-reporting-dates"] == (
        f"{PRIOR.isoformat()},{AS_OF.isoformat()}"
    )
    assert [row["time.date"] for row in rows] == [AS_OF.isoformat()]
    august = rows[0]
    assert Decimal(str(august["engine.car_pct.crd.official"])) == Decimal("14.25")
    assert august["engine.lcr_pct.crd.official"] is None
    assert august["engine.nsfr_pct.crd.live"] is None


# --- the two bundles are disjoint -----------------------------------------------------------


def test_a_data_push_key_is_refused_the_feed(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """Not because the route names its bundle: because it asks for ``VIEW``."""

    response = _pull(db_client, integration_key_headers(BANK_ID))
    assert response.status_code == 403, response.text
    details = response.json()["error"]["details"]
    assert details["error_code"] == read_bi_feeds.ERROR_AUTHORIZATION_DENIED
    assert details["denied_members"]
    assert str(B1_AUGUST) not in response.text
    logged = _log_rows(db_session)
    assert len(logged) == 1
    assert logged[0].decision == "denied"
    assert logged[0].row_count is None
    assert logged[0].principal_type == PrincipalType.MACHINE.value


def test_an_analytics_feed_key_is_refused_a_push(
    db_client: TestClient, mart: Bank, bi_on: None
) -> None:
    """The mirror image, and the reason the bundles must stay disjoint."""

    response = db_client.post(
        f"/api/v1/banks/{BANK_ID}/push-batches",
        headers=_reader(),
        json={
            "as_of_date": AS_OF.isoformat(),
            "idempotency_key": f"feed-key-{uuid4().hex}",
            "reason": "a reader key must not be able to write canonical data",
        },
    )
    assert response.status_code == 403, response.text
    assert "machine binding" in response.json()["error"]["message"]


def test_the_two_machine_bundles_share_no_permission() -> None:
    """The structural statement behind both refusals above."""

    from app.core.authorization import ROLE_PERMISSIONS  # noqa: PLC0415 - one assertion

    reader = ROLE_PERMISSIONS[RoleBundle.BI_READER]
    writer = ROLE_PERMISSIONS[RoleBundle.INTEGRATION_WRITER]
    assert reader & writer == frozenset()
    assert reader and writer


# --- refusals -------------------------------------------------------------------------------


def test_a_revoked_key_is_refused(db_client: TestClient, mart: Bank, bi_on: None) -> None:
    reader = _reader()
    assert _pull(db_client, reader).status_code == 200
    raw = reader["Authorization"].removeprefix("Bearer ")
    with get_sessionmaker()() as db:
        db.info["organization_id"] = ORG_1
        key = db.scalar(
            select(IntegrationKey).where(
                IntegrationKey.key_hash == integration_keys.hash_key(raw),
                IntegrationKey.organization_id == ORG_1,
            )
        )
        assert key is not None
        integration_keys.revoke_key(
            db,
            TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1),
            key.id,
            "rotate the report server credential",
        )
    assert _pull(db_client, reader).status_code == 401


def test_revocation_ends_the_reader_binding_and_its_identity(
    db_client: TestClient, db_session: Session, mart: Bank
) -> None:
    """The lifecycle is the writer's, unchanged, for the new purpose too."""

    reader = _reader()
    raw = reader["Authorization"].removeprefix("Bearer ")
    with get_sessionmaker()() as db:
        db.info["organization_id"] = ORG_1
        key = db.scalar(
            select(IntegrationKey).where(
                IntegrationKey.key_hash == integration_keys.hash_key(raw),
                IntegrationKey.organization_id == ORG_1,
            )
        )
        assert key is not None
        service_user_id = key.service_user_id
        integration_keys.revoke_key(
            db,
            TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1),
            key.id,
            "decommission the report server",
        )
    binding = db_session.scalar(
        select(AuthorizationBinding).where(
            AuthorizationBinding.principal_user_id == service_user_id,
            AuthorizationBinding.role_bundle == RoleBundle.BI_READER.value,
        )
    )
    assert binding is not None
    assert binding.status == BindingStatus.REVOKED.value
    service_user = db_session.get(User, service_user_id)
    assert service_user is not None
    assert service_user.is_active is False


def test_a_key_for_a_sibling_institution_of_the_same_tenant_is_404(
    db_client: TestClient, mart: Bank, bi_on: None
) -> None:
    """Which institutions a credential covers is not something it may enumerate."""

    response = _pull(db_client, _reader(SAME_TENANT_BANK_ID))
    assert response.status_code == 404, response.text
    assert response.json()["error"]["message"] == "Bank not found."


def test_another_tenants_institution_is_404(db_client: TestClient, mart: Bank, bi_on: None) -> None:
    response = _pull(
        db_client,
        _reader(),
        path=f"/api/v1/banks/{SIBLING_BANK_ID}/bi/feeds/loan_book",
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["message"] == "Bank not found."


def test_an_unknown_dataset_is_refused_without_disclosing_the_registry(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    response = _pull(
        db_client,
        _reader(),
        path=f"/api/v1/banks/{BANK_ID}/bi/feeds/everything",
    )
    assert response.status_code == 404, response.text
    for known in datasets.dataset_ids():
        assert known not in response.text
    logged = _log_rows(db_session)
    assert len(logged) == 1
    assert logged[0].decision == "denied"
    assert logged[0].member_ids == []


def test_a_human_token_is_refused_however_much_authority_it_holds(
    db_client: TestClient, mart: Bank, bi_on: None
) -> None:
    response = _pull(db_client, headers())
    assert response.status_code == 403, response.text
    assert response.json()["error"]["details"]["error_code"] == read_bi_feeds.ERROR_MACHINE_REQUIRED


def test_the_feed_is_absent_when_bi_is_off(db_client: TestClient, mart: Bank) -> None:
    """The hermetic default is off, which is the product default."""

    response = _pull(db_client, _reader())
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize("token", ["not-a-cursor", "aeqf1.abc.20260831", "aeqf0.1.20260831"])
def test_a_malformed_cursor_is_422_not_a_full_resynchronisation(
    db_client: TestClient, mart: Bank, bi_on: None, token: str
) -> None:
    response = _pull(db_client, _reader(), cursor=token)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["error_code"] == read_bi_feeds.ERROR_INVALID_CURSOR
    assert str(B1_AUGUST) not in response.text


# --- the cursor -----------------------------------------------------------------------------


def test_the_cursor_walks_forward_and_brings_a_restatement_back(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """The property a business-date cursor cannot have."""

    reader = _reader()
    first = _pull(db_client, reader)
    assert first.status_code == 200
    cursor = first.headers["x-bi-feed-next-cursor"]
    assert cursor != runner.NONE_SENTINEL
    assert len(_rows(first)) == 3

    caught_up = _pull(db_client, reader, cursor=cursor)
    assert caught_up.status_code == 200
    assert _rows(caught_up) == []
    assert caught_up.headers["x-bi-feed-reporting-dates"] == runner.NONE_SENTINEL
    assert caught_up.headers["x-bi-feed-next-cursor"] == runner.NONE_SENTINEL

    _restate_prior(db_session)

    after = _pull(db_client, reader, cursor=cursor)
    assert after.status_code == 200
    rows = _rows(after)
    assert [row["time.date"] for row in rows] == [PRIOR.isoformat()]
    assert Decimal(str(rows[0]["loans.balance_rc"])) == RESTATED_BALANCE
    assert after.headers["x-bi-feed-reporting-dates"] == PRIOR.isoformat()
    # And the cursor moved past the restatement, so a third pull is empty again.
    third = _pull(db_client, reader, cursor=after.headers["x-bi-feed-next-cursor"])
    assert _rows(third) == []


def test_the_cursor_is_opaque_to_the_caller_and_round_trips_through_the_route(
    db_client: TestClient, mart: Bank, bi_on: None
) -> None:
    response = _pull(db_client, _reader())
    token = response.headers["x-bi-feed-next-cursor"]
    decoded = feed_cursor.decode(token)
    assert decoded.as_of == AS_OF
    assert decoded.at == BUILT_AT


# --- the data scope -------------------------------------------------------------------------


def test_a_branch_scoped_credential_serves_only_its_branch(
    db_client: TestClient, mart: Bank, bi_on: None
) -> None:
    """The filter is injected by the server and the client sends no query at all."""

    response = _pull(db_client, _reader(data_scope=DataScope.BRANCH, values=("B2",)))
    assert response.status_code == 200, response.text
    rows = _rows(response)
    assert {row["branch.code"] for row in rows} == {"B2"}
    assert [Decimal(str(row["loans.balance_rc"])) for row in rows] == [B2_AUGUST]
    # July's loan is on B1, so the scoped credential is not told July exists as a
    # row — but it IS told which slice it covers, which is the point.
    assert str(B1_AUGUST) not in response.text
    assert str(PRIOR_BALANCE) not in response.text
    assert response.headers["x-bi-feed-data-scope"] == "Branches: B2"


def test_a_region_scope_resolves_through_the_branch_dimension(
    db_client: TestClient, mart: Bank, bi_on: None
) -> None:
    """A region is resolved TO branches, so a branch joining the region joins it."""

    response = _pull(
        db_client,
        _reader(data_scope=DataScope.REGION, values=("Unassigned region",)),
    )
    assert response.status_code == 200, response.text
    assert {row["branch.code"] for row in _rows(response)} == {"B1", "B2"}
    # The label is the platform's own (``services/bi/data_scope.py``), not a
    # second copy written for this surface: it names the GRANT (the region) and
    # how many branches it came to, so an institution-wide-looking payload cannot
    # be mistaken for one. The interpunct the shared label uses is replaced by
    # ``runner.header_text``; see the header-safety test below.
    assert response.headers["x-bi-feed-data-scope"] == (
        "Regions: Unassigned region 2 branches in scope"
    )


def test_a_header_carrying_bank_data_is_reduced_to_printable_ascii(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """A branch's REGION is bank data and it reaches a response header.

    It arrives through the Data Engine from a core banking system, so it can hold
    anything: a non-ASCII character (which Starlette writes as latin-1 and the
    consumer then has to guess at) or — worse — a CR/LF, which would inject a
    header. Both are reduced to printable ASCII before the value is sent.
    """

    nasty = "Volta\r\nX-Injected: yes\u00a0Region\u2014north"
    db_session.add(
        BiDimBranch(
            organization_id=ORG_1,
            bank_id=BANK_ID,
            branch_code="B3",
            name="Ho branch",
            region=nasty,
            mapped=True,
            builder_version=1,
            built_at=BUILT_AT,
        )
    )
    db_session.commit()

    response = _pull(db_client, _reader(data_scope=DataScope.REGION, values=(nasty,)))
    assert response.status_code == 200, response.text
    label = response.headers["x-bi-feed-data-scope"]
    assert label.isascii()
    assert "\r" not in label and "\n" not in label
    assert "x-injected" not in {name.lower() for name in response.headers}
    assert "B3" in response.headers["x-bi-feed-data-scope"] or "1 branch in scope" in label


def test_a_scope_that_matches_no_branch_serves_nothing_rather_than_everything(
    db_client: TestClient, mart: Bank, bi_on: None
) -> None:
    """The fail-open this phase exists to close."""

    response = _pull(
        db_client,
        _reader(data_scope=DataScope.REGION, values=("Northern Territories",)),
    )
    assert response.status_code == 403, response.text
    assert str(B1_AUGUST) not in response.text


def test_a_branch_scoped_credential_is_refused_the_institution_ratios(
    db_client: TestClient, mart: Bank, bi_on: None
) -> None:
    """A capital ratio over one branch is a wrong number with a right-looking name."""

    response = _pull(
        db_client,
        _reader(data_scope=DataScope.BRANCH, values=("B1",)),
        path=METRICS,
    )
    assert response.status_code == 403, response.text
    details = response.json()["error"]["details"]
    assert set(details["denied_members"]) == set(datasets.dataset("regulatory_metrics").measures)
    assert "14.25" not in response.text


# --- the record of the pull -----------------------------------------------------------------


def test_exactly_one_query_log_row_per_served_pull(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    reader = _reader()
    assert _pull(db_client, reader).status_code == 200
    assert _pull(db_client, reader, fmt="csv").status_code == 200

    logged = _log_rows(db_session)
    assert len(logged) == 2
    for row in logged:
        assert row.decision == "allowed"
        assert row.principal_type == PrincipalType.MACHINE.value
        assert row.bank_id == BANK_ID
        assert row.row_count == 3
        assert row.duration_ms is not None
        assert row.member_ids
    # Two different questions (two formats) hash differently; neither hash
    # contains anything readable.
    assert logged[0].query_hash != logged[1].query_hash
    assert "loan_book" not in logged[0].query_hash


def test_the_audit_event_names_the_dataset_and_both_cursors(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    response = _pull(db_client, _reader())
    assert response.status_code == 200
    events = list(
        db_session.scalars(
            select(AuditEvent)
            .where(AuditEvent.event_type == read_bi_feeds.EVENT_PULLED)
            .order_by(AuditEvent.created_at)
        )
    )
    assert len(events) == 1
    details = events[0].details
    assert details["dataset"] == "loan_book"
    assert details["cursor"] is None
    assert details["next_cursor"] == response.headers["x-bi-feed-next-cursor"]
    assert details["reporting_dates"] == [PRIOR.isoformat(), AS_OF.isoformat()]
    assert details["row_count"] == 3
    assert details["data_scope"] == "Whole institution"


def test_a_refused_pull_is_recorded_too(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    assert _pull(db_client, integration_key_headers(BANK_ID)).status_code == 403
    refusals = list(
        db_session.scalars(
            select(AuditEvent).where(AuditEvent.event_type == read_bi_feeds.EVENT_REFUSED)
        )
    )
    assert len(refusals) == 1
    assert refusals[0].details["dataset"] == "loan_book"
    assert db_session.scalar(
        select(func.count()).select_from(BiQueryLog).where(BiQueryLog.decision == "denied")
    )


# --- the credential's own lifecycle surface --------------------------------------------------


def test_the_purpose_is_derived_from_the_binding_and_not_stored_on_the_key(
    db_client: TestClient, db_session: Session, mart: Bank
) -> None:
    """There is no ``purpose`` column, deliberately: the binding IS the authority."""

    assert not hasattr(IntegrationKey, "purpose")
    _reader(label="Report server", data_scope=DataScope.BRANCH, values=("B1",))
    integration_key_headers(BANK_ID)

    listed = db_client.get("/api/v1/integration-keys", headers=_account_admin_headers(db_session))
    assert listed.status_code == 200, listed.text
    by_label = {item["label"]: item for item in listed.json()["keys"]}
    assert by_label["Report server"]["purpose"] == "reader"
    assert by_label["Report server"]["data_scope_kind"] == "branch"
    assert by_label["Report server"]["data_scope_values"] == ["B1"]
    assert by_label["Test push feed"]["purpose"] == "writer"
    assert by_label["Test push feed"]["data_scope_kind"] is None


def test_a_data_push_key_cannot_be_issued_with_a_branch_scope(
    db_client: TestClient, db_session: Session, mart: Bank
) -> None:
    response = db_client.post(
        "/api/v1/integration-keys",
        headers=_account_admin_headers(db_session),
        json={
            "bank_id": BANK_ID,
            "label": "Confused credential",
            "purpose": "writer",
            "data_scope_kind": "branch",
            "data_scope_values": ["B1"],
        },
    )
    assert response.status_code == 422, response.text


def test_a_reader_key_is_issued_through_the_route_under_account_administration(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """The issuance authority is unchanged by the second purpose."""

    admin = _account_admin_headers(db_session)
    response = db_client.post(
        "/api/v1/integration-keys",
        headers=admin,
        json={
            "bank_id": BANK_ID,
            "label": "Report server via route",
            "purpose": "reader",
            "data_scope_kind": "branch",
            "data_scope_values": ["B2"],
        },
    )
    assert response.status_code == 201, response.text
    issued = response.json()
    assert issued["record"]["purpose"] == "reader"
    pulled = _pull(db_client, {"Authorization": f"Bearer {issued['key']}"})
    assert pulled.status_code == 200, pulled.text
    assert {row["branch.code"] for row in _rows(pulled)} == {"B2"}


def _account_admin_headers(db: Session) -> dict[str, str]:
    """One organization-wide ACCOUNT/restricted ``administer`` binding for USER_1."""

    from app.core.authorization import (  # noqa: PLC0415 - local to this helper
        GrantorType,
        InstitutionScope,
        ModuleScope,
        SensitivityScope,
    )
    from app.services import authorization  # noqa: PLC0415

    authorization.create_role_binding(
        db,
        organization_id=ORG_1,
        principal_user_id=USER_1,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.ACCOUNT_ADMIN,
        scope=authorization.BindingScope(
            InstitutionScope.ORGANIZATION,
            None,
            ModuleScope.ACCOUNT,
            SensitivityScope.RESTRICTED,
        ),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
        reason="exercise analytics-feed credential administration",
        commit=False,
    )
    db.commit()
    user = db.get(User, USER_1)
    assert user is not None
    db.refresh(user)
    return headers(ORG_1, user_id=USER_1, authorization_version=user.authorization_version)


def test_the_route_the_allow_list_must_name_is_the_route_that_is_mounted(
    db_client: TestClient,
) -> None:
    """The allow-list entry and the mounted path cannot be different strings.

    ``deps.INTEGRATION_KEY_ROUTES`` refuses an integration key on any route it
    does not name, so the feature exports the exact tuple the entry must be, and
    this asserts it matches what the app actually mounts.
    """

    app = db_client.app
    assert isinstance(app, FastAPI)
    method, path = read_bi_feeds.FEED_INTEGRATION_KEY_ROUTE
    matching = [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path == path and method in route.methods
    ]
    assert matching, f"{method} {path} is not mounted"
    assert path == read_bi_feeds.FEED_ROUTE_PATH


def test_the_feed_route_carries_no_object_identifier(db_client: TestClient) -> None:
    """Why the IDOR census needs no entry: ``{dataset}`` is a registry token.

    The census keys on ``*_id`` path, query and body parameters. This route
    accepts none — the dataset is resolved against a closed declared registry and
    a value outside it is refused before anything is read.
    """

    app = db_client.app
    assert isinstance(app, FastAPI)
    route = next(
        candidate
        for candidate in app.routes
        if isinstance(candidate, APIRoute) and candidate.path == read_bi_feeds.FEED_ROUTE_PATH
    )
    names = [parameter.name for parameter in route.dependant.path_params] + [
        parameter.name for parameter in route.dependant.query_params
    ]
    assert [name for name in names if name.endswith("_id") and name != "bank_id"] == []
