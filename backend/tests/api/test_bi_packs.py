"""The certified dashboards, resolved for one reader and one reporting date.

``tests/domain/bi/test_packs.py`` proves the FILES: seven packs, every member
resolving, no date anywhere, no currency or regulator in a label. This file
proves what the ROUTE does with them, and each test is a property the product
depends on rather than a shape the code happens to produce:

* **the date is the caller's.** Every widget query in a response is byte-identical
  to ``BiPackQuery.for_period(as_of)``, and no date appears anywhere in the
  response that is not derivable from the ``as_of`` that was asked for. Nothing
  may bypass ``for_period``, or a certified dashboard becomes a pinned date.
* **a refused widget discloses nothing.** The refusal carries its id and its place
  on the canvas. Not the title, not the caption, not the measure, not the
  dimension, not the filter, not a figure — which is checked by asserting the
  response body does not CONTAIN any of them, not merely that a field is absent.
* **an all-refused dashboard does not read as an empty one.** It says so, in
  production copy, and the counts agree with the widgets.
* **``needs_data`` and ``pending_capability`` pass through unchanged.** Telling a
  bank it "needs data: positions" when it pushes positions every night is a false
  statement about its own book, so the platform-work case stays its own field.
* **an SDI is not served a bank's dashboard** (D-070).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authorization import InstitutionScope, ModuleScope, SensitivityScope
from app.core.config import get_settings
from app.domain.bi.catalogue import MeasureDef, catalogue
from app.domain.bi.packs import pack as certified_pack
from app.domain.bi.packs import pack_ids, packs
from app.models import Bank
from app.models.bi import BiQueryLog
from app.services.bi.authorization import query_members
from tests.api.helpers import ORG_1, USER_1, headers
from tests.api.test_bi_routes import AS_OF, BANK_ID, BASE, Grant, grant_only, seed_bi_mart

#: An institution of the same tenant on the s.29 regime. The seven packs are
#: certified against CRD authorities, so this is the institution D-070 says must
#: not be shown them.
SDI_BANK_ID = "BK-BIPACKSD"

#: Every module at every sensitivity: the broadest reader, so a refusal in these
#: tests is the pack's own decision and never a missing grant.
EVERYTHING: tuple[Grant, ...] = (
    Grant(ModuleScope.ALL, SensitivityScope.ALL, InstitutionScope.ORGANIZATION),
)
#: Enough for loan totals by branch and nothing else — no capital, no liquidity,
#: no obligor names. Most pack widgets are refused for this reader.
NARROW: tuple[Grant, ...] = (
    Grant(ModuleScope.CREDIT, SensitivityScope.AGGREGATED, InstitutionScope.ORGANIZATION),
)

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


@pytest.fixture
def mart(db_session: Session) -> Bank:
    bank = seed_bi_mart(db_session)
    db_session.add(
        Bank(
            id=SDI_BANK_ID,
            organization_id=ORG_1,
            name="BI pack savings and loans",
            short_name="BI pack S&L",
            currency=bank.currency,
            jurisdiction_code=bank.jurisdiction_code,
            license_type="savings_and_loans",
            institution_type="savings_and_loans",
        )
    )
    db_session.commit()
    return bank


@pytest.fixture
def bi_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("BI_ENABLED", "1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _get(
    client: TestClient,
    path: str,
    *,
    authv: int,
    base: str = BASE,
) -> Any:
    return client.get(f"{base}{path}", headers=headers(authorization_version=authv))


def _widget(payload: dict[str, Any], widget_id: str) -> dict[str, Any]:
    return next(widget for widget in payload["widgets"] if widget["id"] == widget_id)


def _allowed_dates(as_of: str) -> set[str]:
    """Every date a response may contain: the requested one and what it derives.

    Built from ``BiPackQuery.for_period`` itself, so a route that resolved a
    widget through anything else — a hard-coded window, a month end of its own
    choosing, a database lookup — produces a date this set does not hold.
    """
    from datetime import date  # noqa: PLC0415 - local to the helper

    requested = date.fromisoformat(as_of)
    dates = {as_of}
    for spec in packs():
        for widget in spec.widgets:
            if widget.query is None:
                continue
            time = widget.query.for_period(requested).time
            for value in (time.as_of, time.compare_to):
                if value is not None:
                    dates.add(value.isoformat())
            if time.range is not None:
                dates.add(time.range.start.isoformat())
                dates.add(time.range.end.isoformat())
    return dates


# --- the date is the caller's --------------------------------------------------------------


def test_every_widget_query_is_the_file_resolved_for_the_requested_date(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """Byte-identical to ``for_period``: nothing may resolve a window itself."""
    authv = grant_only(db_session, EVERYTHING)
    response = _get(db_client, f"/packs/board?as_of={AS_OF.isoformat()}", authv=authv)
    assert response.status_code == 200, response.text
    payload = response.json()
    spec = certified_pack("board")
    compared = 0
    for widget in spec.widgets:
        served = _widget(payload, widget.id)
        if widget.query is None:
            assert served["query"] is None
            continue
        assert served["access"] == "granted", widget.id
        expected = widget.query.for_period(AS_OF).model_dump(mode="json")
        assert served["query"] == expected, widget.id
        compared += 1
    assert compared, "the board pack served no query widget; this test is vacuous"


def test_a_pack_carries_no_date_the_caller_did_not_ask_for(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """Scanned over the whole body, for every pack, at two different dates."""
    authv = grant_only(db_session, EVERYTHING)
    for as_of in (AS_OF.isoformat(), "2026-04-30"):
        response = _get(db_client, f"/packs?as_of={as_of}", authv=authv)
        assert response.status_code == 200, response.text
        found = set(_ISO_DATE.findall(response.text))
        assert found, "no date at all in the response; this test is vacuous"
        assert found <= _allowed_dates(as_of), found - _allowed_dates(as_of)


def test_two_dates_produce_two_different_resolutions(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """A pinned date would make these identical, which is the whole hazard."""
    authv = grant_only(db_session, EVERYTHING)
    first = _get(db_client, f"/packs/board?as_of={AS_OF.isoformat()}", authv=authv).json()
    second = _get(db_client, "/packs/board?as_of=2026-04-30", authv=authv).json()
    assert first["as_of"] != second["as_of"]
    assert [w["query"] for w in first["widgets"]] != [w["query"] for w in second["widgets"]]


# --- a refused widget discloses nothing ----------------------------------------------------


def test_a_refused_widget_carries_only_its_geometry(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """Everything a refusal could leak is asserted ABSENT FROM THE BODY.

    Checking that the field is null is not enough: the title of a refused widget
    appearing anywhere — in another field, in a message, in a count keyed by name
    — is the same disclosure. The client's ``RestrictedWidget`` accepts a height
    and a class name, so anything beyond the geometry is something it was never
    asked to render.
    """
    authv = grant_only(db_session, NARROW)
    response = _get(db_client, f"/packs/alco?as_of={AS_OF.isoformat()}", authv=authv)
    assert response.status_code == 200, response.text
    payload = response.json()
    spec = certified_pack("alco")
    refused = [widget for widget in payload["widgets"] if widget["access"] == "restricted"]
    assert refused, "no widget was refused for the narrow reader; this test is vacuous"
    for widget in refused:
        assert set(widget) == {
            "id",
            "layout",
            "access",
            *(key for key in widget if widget[key] is None),
        }
        for key, value in widget.items():
            if key in {"id", "layout", "access"}:
                continue
            assert value is None, (widget["id"], key, value)
        declared = spec.widget(widget["id"])
        assert declared.title not in response.text
        if declared.caption:
            assert declared.caption not in response.text
        assert declared.query is not None
        for member in query_members(catalogue(), declared.query.for_period(AS_OF)):
            assert member.id not in response.text, (widget["id"], member.id)
            assert member.label not in response.text, (widget["id"], member.label)


def test_a_refusal_names_no_member_even_though_the_log_does(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """The operator writing the grant needs the member ids; the reader does not."""
    authv = grant_only(db_session, NARROW)
    response = _get(db_client, f"/packs/alco?as_of={AS_OF.isoformat()}", authv=authv)
    assert response.status_code == 200, response.text
    assert "denied" not in response.text
    row = db_session.scalars(
        select(BiQueryLog).where(BiQueryLog.surface == "packs").order_by(BiQueryLog.queried_at)
    ).all()[-1]
    assert row.denied_members, "the log recorded no refusal, so the reader's silence proves nothing"
    assert row.bank_id == BANK_ID
    assert row.principal_user_id == USER_1
    assert row.row_count is None


def test_a_widget_the_reader_holds_is_served_whole(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """The refusal path must not be the only path: a granted widget keeps its copy."""
    authv = grant_only(db_session, EVERYTHING)
    payload = _get(db_client, f"/packs/board?as_of={AS_OF.isoformat()}", authv=authv).json()
    spec = certified_pack("board")
    for widget in spec.widgets:
        served = _widget(payload, widget.id)
        assert served["access"] == "granted"
        assert served["title"] == widget.title
        assert served["caption"] == widget.caption
        assert served["kind"] == widget.kind


# --- an all-refused dashboard ---------------------------------------------------------------


def test_a_dashboard_whose_every_figure_is_refused_says_so(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """Not an empty dashboard and not a 404: a sentence the reader can act on.

    A canvas of lock tiles with no statement over it reads as "there is nothing to
    show for this date", which is a claim about the institution's book. The
    pack-level sentence is what makes it a claim about the reader's access.
    """
    authv = grant_only(db_session, ())
    response = _get(db_client, f"/packs/board?as_of={AS_OF.isoformat()}", authv=authv)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["access"] == "restricted"
    assert payload["readable_widgets"] > 0
    assert payload["restricted_widgets"] == payload["readable_widgets"]
    assert "does not cover any" in payload["message"]
    assert "organization owner" in payload["message"]
    assert all(
        widget["access"] == "restricted"
        for widget in payload["widgets"]
        if certified_pack("board").widget(widget["id"]).query is not None
    )


def test_a_partly_refused_dashboard_says_partly(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    authv = grant_only(db_session, NARROW)
    payload = _get(db_client, f"/packs/cro?as_of={AS_OF.isoformat()}", authv=authv).json()
    assert 0 < payload["restricted_widgets"] < payload["readable_widgets"]
    assert payload["access"] == "granted"
    assert "some of the figures" in payload["message"]


def test_a_fully_granted_dashboard_says_so(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    authv = grant_only(db_session, EVERYTHING)
    payload = _get(db_client, f"/packs/board?as_of={AS_OF.isoformat()}", authv=authv).json()
    assert payload["access"] == "granted"
    assert payload["restricted_widgets"] == 0
    assert payload["message"]


# --- the two kinds of gap -------------------------------------------------------------------


@pytest.mark.parametrize("pack_id", sorted(pack_ids()))
def test_needs_data_and_pending_capability_pass_through_unchanged(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None, pack_id: str
) -> None:
    """A missing DATASET and missing PLATFORM work are different statements.

    Collapsing the second into the first renders "Needs data: positions" on the
    dashboard of a bank that pushes positions nightly.
    """
    authv = grant_only(db_session, EVERYTHING)
    payload = _get(db_client, f"/packs/{pack_id}?as_of={AS_OF.isoformat()}", authv=authv).json()
    spec = certified_pack(pack_id)
    for widget in spec.widgets:
        served = _widget(payload, widget.id)
        assert served["needs_data"] == widget.needs_data, widget.id
        assert served["pending_capability"] == widget.pending_capability, widget.id


def test_the_pack_set_still_carries_both_kinds_of_gap() -> None:
    """The pass-through test above is vacuous if no pack names either."""
    datasets = {w.needs_data for spec in packs() for w in spec.widgets if w.needs_data}
    capabilities = {
        w.pending_capability for spec in packs() for w in spec.widgets if w.pending_capability
    }
    assert datasets and capabilities


def test_a_named_gap_carries_no_query_and_no_figure(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    authv = grant_only(db_session, EVERYTHING)
    payload = _get(db_client, f"/packs/finance?as_of={AS_OF.isoformat()}", authv=authv).json()
    gaps = [
        widget
        for widget in payload["widgets"]
        if certified_pack("finance").widget(widget["id"]).is_gap
    ]
    assert gaps, "the finance pack names no gap; this test is vacuous"
    for widget in gaps:
        assert widget["query"] is None
        assert widget["panel"] is None
        assert widget["needs_data"] or widget["pending_capability"]


# --- the licence class (D-070) --------------------------------------------------------------


def test_an_sdi_is_not_served_a_banks_dashboard(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """D-070. Refused at the surface, before any widget is resolved.

    The seven packs are built on CRD capital and liquidity authorities. An
    institution on the s.29 regime has no row for a single one of them, so every
    figure on them would read as no value — and a dashboard of dashes over a
    bank's headings is worse than no dashboard.
    """
    authv = grant_only(db_session, EVERYTHING)
    sdi_base = f"/api/v1/banks/{SDI_BANK_ID}/bi"
    listing = _get(db_client, f"/packs?as_of={AS_OF.isoformat()}", authv=authv, base=sdi_base)
    assert listing.status_code == 404, listing.text
    details = listing.json()["error"]["details"]
    assert details["error_code"] == "bi_packs_not_published_for_institution_class"
    assert "Savings & Loans" in details["message"]
    assert "prudential regime" in details["message"]
    one = _get(db_client, f"/packs/board?as_of={AS_OF.isoformat()}", authv=authv, base=sdi_base)
    assert one.status_code == 404, one.text
    assert (
        one.json()["error"]["details"]["error_code"]
        == "bi_packs_not_published_for_institution_class"
    )
    # Nothing of the pack leaks through the refusal.
    for text in (listing.text, one.text):
        assert "widgets" not in text
        assert certified_pack("board").title not in text


def test_a_bank_is_served_every_certified_pack(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """The negative half above must not pass because nobody is served anything."""
    authv = grant_only(db_session, EVERYTHING)
    payload = _get(db_client, f"/packs?as_of={AS_OF.isoformat()}", authv=authv).json()
    assert {pack["id"] for pack in payload["packs"]} == set(pack_ids())
    assert payload["as_of"] == AS_OF.isoformat()


def test_the_class_gate_is_the_registrys_own_resolution() -> None:
    """Not a new notion of "is this an SDI": the same resolution the mart used.

    Every pack that names an engine measure names one whose authority resolves
    under CRD, which is why the gate closes for s.29 today and opens by itself
    when a pack naming an SDI's own authorities ships.
    """
    from app.services.bi.insights import engine_measure_applies  # noqa: PLC0415

    cat = catalogue()
    named = [
        cat.member(member_id)
        for spec in packs()
        for widget in spec.widgets
        if widget.query is not None
        for member_id in widget.query.measures
    ]
    engine = [m for m in named if isinstance(m, MeasureDef) and m.engine_rule is not None]
    assert engine, "no pack names an engine measure; the gate would be vacuous"
    assert all(
        engine_measure_applies(m, institution_class="bank", capital_regime="crd") for m in engine
    )
    assert not any(
        engine_measure_applies(m, institution_class="sdi", capital_regime="s29") for m in engine
    )


def test_an_unknown_pack_is_not_found(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    authv = grant_only(db_session, EVERYTHING)
    response = _get(db_client, f"/packs/no_such_pack?as_of={AS_OF.isoformat()}", authv=authv)
    assert response.status_code == 404, response.text
    assert response.json()["error"]["details"]["error_code"] == "bi_pack_not_found"


# --- the log and the cache ------------------------------------------------------------------


def test_one_row_per_pack_read_under_the_packs_surface(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    authv = grant_only(db_session, EVERYTHING)
    before = len(db_session.scalars(select(BiQueryLog)).all())
    _get(db_client, f"/packs?as_of={AS_OF.isoformat()}", authv=authv)
    _get(db_client, f"/packs/board?as_of={AS_OF.isoformat()}", authv=authv)
    rows = db_session.scalars(select(BiQueryLog).order_by(BiQueryLog.queried_at)).all()
    assert len(rows) - before == 2
    for row in rows[-2:]:
        assert row.surface == "packs"
        assert row.decision == "allowed"
        assert row.member_ids
        assert row.row_count is None
        assert len(row.query_hash) == 64


def test_a_refused_licence_class_is_recorded_as_a_refusal_naming_no_member(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """The budget is counted over these rows, so a probe loop is not free."""
    authv = grant_only(db_session, EVERYTHING)
    _get(
        db_client,
        f"/packs?as_of={AS_OF.isoformat()}",
        authv=authv,
        base=f"/api/v1/banks/{SDI_BANK_ID}/bi",
    )
    row = db_session.scalars(
        select(BiQueryLog).where(BiQueryLog.surface == "packs").order_by(BiQueryLog.queried_at)
    ).all()[-1]
    assert row.decision == "denied"
    assert row.member_ids == []
    assert row.denied_members == []
    assert row.bank_id == SDI_BANK_ID


def test_a_revalidated_pack_read_is_free(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    authv = grant_only(db_session, EVERYTHING)
    first = _get(db_client, f"/packs/board?as_of={AS_OF.isoformat()}", authv=authv)
    etag = first.headers["ETag"]
    assert etag
    again = db_client.get(
        f"{BASE}/packs/board?as_of={AS_OF.isoformat()}",
        headers={**headers(authorization_version=authv), "If-None-Match": etag},
    )
    assert again.status_code == 304, again.text
    assert not again.content


def test_the_etag_moves_with_the_date_and_the_reader(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    authv = grant_only(db_session, EVERYTHING)
    board = _get(db_client, f"/packs/board?as_of={AS_OF.isoformat()}", authv=authv)
    other_date = _get(db_client, "/packs/board?as_of=2026-04-30", authv=authv)
    assert board.headers["ETag"] != other_date.headers["ETag"]
    narrower = grant_only(db_session, NARROW)
    narrowed = _get(db_client, f"/packs/board?as_of={AS_OF.isoformat()}", authv=narrower)
    assert narrowed.headers["ETag"] != board.headers["ETag"]


def test_a_pack_naming_a_member_the_catalogue_lost_is_withheld_not_500(  # noqa: PLR0913
    db_client: TestClient,
    db_session: Session,
    mart: Bank,
    bi_on: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fails closed. A certified dashboard that cannot be resolved is not shown.

    ``tests/domain/bi/test_packs.py`` resolves every member of every pack, so this
    cannot ship — but a catalogue member disappearing under a running deployment
    must withhold the dashboard, not raise in front of a board.
    """
    real = catalogue()

    class _Lossy:
        version = real.version

        def member(self, member_id: str) -> Any:
            if member_id.startswith("engine."):
                raise KeyError(member_id)
            return real.member(member_id)

        def __getattr__(self, name: str) -> Any:
            return getattr(real, name)

    lossy = _Lossy()

    def _catalogue() -> Any:
        return lossy

    monkeypatch.setattr("app.features.read_bi.catalogue", _catalogue)
    authv = grant_only(db_session, EVERYTHING)
    response = _get(db_client, f"/packs?as_of={AS_OF.isoformat()}", authv=authv)
    assert response.status_code == 404, response.text
    assert (
        response.json()["error"]["details"]["error_code"]
        == "bi_packs_not_published_for_institution_class"
    )
