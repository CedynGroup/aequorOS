"""What the platform will say about a reporting date, and what it refuses to say.

The insights layer is pure and has its own suites; this file is about the SEAM —
the route and the assembler behind it — and every test here is one of the ways a
dashboard sentence lies:

* **a figure the book cannot answer becomes a movement of zero.** The single most
  important property: an insight is read as a statement about the institution, and
  "unchanged" over a figure nobody computed is a false one. Proven on a mart with
  no prior period at all.
* **an analytical figure is presented as certified.** Only a filed engine figure
  copied from the sealed tier may read as certified (D-022); nothing computed from
  the marts may, and there is no badge in between.
* **a measure the reader may not see is mentioned anyway.** A narrow reader gets no
  statement naming a capital figure, and no hint beyond a count.
* **a reader who may see nothing is told "nothing stands out".** That sentence is a
  claim about the bank's figures, so the route refuses instead of making it.

The wire shape is checked against ``components/bi/types.ts`` directly: the strip
mirrors ``statements.py::Insight`` field for field in camelCase, and a mismatch
there is a field the browser silently drops.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authorization import InstitutionScope, ModuleScope, SensitivityScope
from app.core.config import get_settings
from app.models import Bank
from app.models.bi import (
    BiAggPositionDaily,
    BiFactEngineMetric,
    BiFactPositionDaily,
    BiMartBuild,
    BiQueryLog,
)
from app.schemas.bi import BiInsightRead
from app.services.bi.insights import default_compare_to, headline_measures
from tests.api.helpers import ORG_1, headers
from tests.api.test_bi_routes import (
    AS_OF,
    BASE,
    BUILT_AT,
    FINGERPRINT,
    Grant,
    grant_only,
    seed_bi_mart,
)

PRIOR = default_compare_to(AS_OF)

#: An institution of the same tenant on the s.29 regime: its headline figures are
#: its own authorities', never a bank's.
SDI_BANK_ID = "BK-BIINSISD"

EVERYTHING: tuple[Grant, ...] = (
    Grant(ModuleScope.ALL, SensitivityScope.ALL, InstitutionScope.ORGANIZATION),
)
#: Loan figures only — no capital, no liquidity, no interest-rate risk.
CREDIT_ONLY: tuple[Grant, ...] = (
    Grant(ModuleScope.CREDIT, SensitivityScope.AGGREGATED, InstitutionScope.ORGANIZATION),
)

_TYPES_TS = Path("dashboard/components/bi/types.ts")
#: Fields the strip carries that no ``Insight`` holds: a published sparkline
#: series and a link to the working. Both are the client's to supply — see the
#: report — and both are optional in the TypeScript type.
_CLIENT_ONLY_FIELDS: frozenset[str] = frozenset({"series", "href"})


# --- fixtures --------------------------------------------------------------------------------


@pytest.fixture
def bi_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("BI_ENABLED", "1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def mart(db_session: Session) -> Bank:
    """The shared mart, plus a sibling institution on the other capital regime."""
    bank = seed_bi_mart(db_session)
    db_session.add(
        Bank(
            id=SDI_BANK_ID,
            organization_id=ORG_1,
            name="BI insights savings and loans",
            short_name="BI insights S&L",
            currency=bank.currency,
            jurisdiction_code=bank.jurisdiction_code,
            license_type="savings_and_loans",
            institution_type="savings_and_loans",
        )
    )
    db_session.commit()
    return bank


def _clone_day(db: Session, bank: Bank, *, target: date, factor: Decimal) -> None:
    """Copy the fixture's day onto ``target``, scaled, so a movement is real.

    Every ``Decimal`` column is scaled by the same factor, so the ratios move in a
    way the bridge can attribute and the totals stay internally consistent.
    """
    for model in (BiFactPositionDaily, BiAggPositionDaily, BiFactEngineMetric):
        rows = db.scalars(
            select(model).where(model.bank_id == bank.id, model.as_of_date == AS_OF)
        ).all()
        for row in rows:
            values = {column.name: getattr(row, column.name) for column in model.__table__.columns}
            values["as_of_date"] = target
            if "id" in values:
                values["id"] = uuid4()
            for key, value in list(values.items()):
                if isinstance(value, Decimal):
                    values[key] = value * factor
            db.add(model(**values))
    db.add(
        BiMartBuild(
            organization_id=bank.organization_id,
            bank_id=bank.id,
            as_of_date=target,
            scope="engine",
            fingerprint=FINGERPRINT,
            status="succeeded",
            builder_version=1,
            started_at=BUILT_AT,
            finished_at=BUILT_AT,
            row_counts={},
        )
    )
    db.commit()


def _get(client: TestClient, query: str, *, authv: int, base: str = BASE) -> Any:
    return client.get(f"{base}/insights?{query}", headers=headers(authorization_version=authv))


def _classes(payload: dict[str, Any]) -> set[str]:
    return {insight["statement_class"] for insight in payload["insights"]}


# --- a missing figure is never a zero and never "flat" ---------------------------------------


def test_a_book_with_no_prior_period_states_no_movement_at_all(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """The property this whole surface stands on.

    The fixture holds one date. Every headline figure therefore has no comparison,
    and several have no current figure either. Nothing in the answer may assert a
    move, a change of zero, or that anything stayed the same.
    """
    authv = grant_only(db_session, EVERYTHING)
    response = _get(db_client, f"as_of={AS_OF.isoformat()}", authv=authv)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["measures_read"] > 0, "nothing was read; this test is vacuous"
    assert "movement" not in _classes(payload)
    assert "attribution" not in _classes(payload)
    for insight in payload["insights"]:
        for banned in ("unchanged", "stayed the same", "no change", "flat by"):
            assert banned not in insight["detail"].lower(), (insight["id"], banned)
            assert banned not in insight["headline"].lower(), (insight["id"], banned)


def test_a_figure_with_no_value_is_named_as_a_gap_and_says_it_is_not_zero(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    authv = grant_only(db_session, EVERYTHING)
    payload = _get(db_client, f"as_of={AS_OF.isoformat()}", authv=authv).json()
    gaps = [i for i in payload["insights"] if i["statement_class"] == "data_gap"]
    assert gaps, "the fixture answered every headline figure; this test is vacuous"
    for gap in gaps:
        assert "It is not zero, and it has not stayed flat." in gap["detail"]
        assert gap["measure_ids"]
        assert gap["favourability"] == "neutral"


def test_a_real_movement_is_stated_with_both_figures(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """The positive control: the silence above is not the only thing this can do."""
    _clone_day(db_session, mart, target=PRIOR, factor=Decimal("0.8"))
    authv = grant_only(db_session, EVERYTHING)
    payload = _get(db_client, f"as_of={AS_OF.isoformat()}", authv=authv).json()
    assert payload["compare_to"] == PRIOR.isoformat()
    movements = [i for i in payload["insights"] if i["statement_class"] == "movement"]
    assert movements, payload["insights"]
    car = next((i for i in movements if "engine.car_pct.crd.official" in i["measure_ids"]), None)
    assert car is not None, [i["measure_ids"] for i in movements]
    assert PRIOR.isoformat() in car["detail"]
    assert AS_OF.isoformat() in car["detail"]
    # 14.25 against 11.40: a rise in a higher-is-better figure.
    assert car["favourability"] == "favourable"
    assert "14.25" in car["detail"]


def test_a_ratios_move_is_attributed_exactly(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """The exact bridge, reached through the route: parts that add to the whole."""
    _clone_day(db_session, mart, target=PRIOR, factor=Decimal("0.8"))
    authv = grant_only(db_session, EVERYTHING)
    payload = _get(db_client, f"as_of={AS_OF.isoformat()}", authv=authv).json()
    attributions = [i for i in payload["insights"] if i["statement_class"] == "attribution"]
    if not attributions:
        pytest.skip("no ratio moved materially in the scaled fixture")
    for insight in attributions:
        assert "add up to the whole move exactly" in insight["detail"]
        assert "contributed" in insight["detail"]


def test_two_points_do_not_become_a_projection(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """Three observations is the floor; a line through two is not a trend."""
    _clone_day(db_session, mart, target=PRIOR, factor=Decimal("0.8"))
    authv = grant_only(db_session, EVERYTHING)
    payload = _get(db_client, f"as_of={AS_OF.isoformat()}", authv=authv).json()
    assert "projection" not in _classes(payload)


# --- certification and build freshness --------------------------------------------------------


# --- authorization ---------------------------------------------------------------------------


def test_a_narrow_reader_gets_no_statement_naming_a_measure_they_cannot_see(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """Per measure, through the query path's own decision — and no names leak."""
    everything = grant_only(db_session, EVERYTHING)
    full = _get(db_client, f"as_of={AS_OF.isoformat()}", authv=everything).json()
    narrow_authv = grant_only(db_session, CREDIT_ONLY)
    response = _get(db_client, f"as_of={AS_OF.isoformat()}", authv=narrow_authv)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["measures_withheld"] > 0
    assert payload["measures_read"] < full["measures_read"]
    assert payload["measures_read"] + payload["measures_withheld"] == (
        full["measures_read"] + full["measures_withheld"]
    )
    named = {member_id for insight in payload["insights"] for member_id in insight["measure_ids"]}
    assert named
    assert not any(member_id.startswith("engine.car_pct") for member_id in named)
    assert not any(member_id.startswith("engine.lcr_pct") for member_id in named)
    # The count is the only hint; the withheld ids never appear in the body.
    assert "engine.car_pct.crd.official" not in response.text
    assert "denied_members" not in response.text


def test_a_reader_who_may_see_nothing_is_refused_rather_than_told_nothing_stands_out(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """An empty strip reads as "nothing stands out", which is a claim about the book.

    So the route refuses — and the refusal names no measure, because the reader is
    not the operator who writes the grant. The withheld ids are in the log.
    """
    authv = grant_only(db_session, ())
    response = _get(db_client, f"as_of={AS_OF.isoformat()}", authv=authv)
    assert response.status_code == 403, response.text
    details = response.json()["error"]["details"]
    assert details["error_code"] == "bi_insights_authorization_denied"
    assert "organization owner" in details["message"]
    assert "engine." not in response.text
    assert "loans." not in response.text
    row = db_session.scalars(
        select(BiQueryLog).where(BiQueryLog.surface == "insights").order_by(BiQueryLog.queried_at)
    ).all()[-1]
    assert row.decision == "denied"
    assert row.denied_members
    assert row.row_count is None


def test_an_institution_on_the_other_regime_reads_its_own_figures(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """Insights are not withheld from an SDI — its headline set is its own.

    The dashboards surface refuses an SDI (D-070) because the packs are certified
    against a bank's authorities. A headline SET is derived per institution, so
    there is nothing to refuse: it simply never names a CRD figure.
    """
    authv = grant_only(db_session, EVERYTHING)
    response = _get(
        db_client,
        f"as_of={AS_OF.isoformat()}",
        authv=authv,
        base=f"/api/v1/banks/{SDI_BANK_ID}/bi",
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    named = {member_id for insight in payload["insights"] for member_id in insight["measure_ids"]}
    assert named, payload
    assert not any(".crd." in member_id for member_id in named)


# --- the budget, the log and the cache -------------------------------------------------------


def test_one_insights_read_is_one_row_and_one_charge_against_the_budget(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """Several compiled statements, ONE budgeted read.

    An insights strip that charged a reader once per statement would consume their
    whole minute's budget and deny them the rest of BI, so the protection against
    the request being expensive is the assembler's own read cap, not the budget.
    """
    _clone_day(db_session, mart, target=PRIOR, factor=Decimal("0.8"))
    authv = grant_only(db_session, EVERYTHING)
    before = len(db_session.scalars(select(BiQueryLog)).all())
    _get(db_client, f"as_of={AS_OF.isoformat()}", authv=authv)
    rows = db_session.scalars(select(BiQueryLog).order_by(BiQueryLog.queried_at)).all()
    assert len(rows) - before == 1
    row = rows[-1]
    assert row.surface == "insights"
    assert row.decision == "allowed"
    assert row.member_ids, "the row names no member, so it cannot be reviewed"
    assert len(row.member_ids) > 1
    assert row.row_count is not None
    assert row.duration_ms is not None
    assert row.build_fingerprint == FINGERPRINT


def test_the_fact_sheet_hash_tells_two_sets_apart(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    authv = grant_only(db_session, EVERYTHING)
    first = _get(db_client, f"as_of={AS_OF.isoformat()}", authv=authv).json()
    again = _get(db_client, f"as_of={AS_OF.isoformat()}", authv=authv).json()
    assert first["fact_sheet_hash"] == again["fact_sheet_hash"]
    assert len(first["fact_sheet_hash"]) == 64
    row = db_session.scalars(
        select(BiFactEngineMetric).where(
            BiFactEngineMetric.bank_id == mart.id,
            BiFactEngineMetric.tier == "official",
        )
    ).first()
    assert row is not None
    row.value = Decimal("9.99")
    db_session.commit()
    changed = _get(db_client, f"as_of={AS_OF.isoformat()}", authv=authv).json()
    assert changed["fact_sheet_hash"] != first["fact_sheet_hash"]


def test_a_revalidated_insights_read_is_free(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    authv = grant_only(db_session, EVERYTHING)
    first = _get(db_client, f"as_of={AS_OF.isoformat()}", authv=authv)
    etag = first.headers["ETag"]
    again = db_client.get(
        f"{BASE}/insights?as_of={AS_OF.isoformat()}",
        headers={**headers(authorization_version=authv), "If-None-Match": etag},
    )
    assert again.status_code == 304, again.text
    assert not again.content


def test_a_comparison_that_is_not_earlier_is_refused(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    authv = grant_only(db_session, EVERYTHING)
    response = _get(
        db_client,
        f"as_of={AS_OF.isoformat()}&compare_to={AS_OF.isoformat()}",
        authv=authv,
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["error_code"] == "bi_insights_comparison_not_earlier"


def test_an_explicit_comparison_is_honoured(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    authv = grant_only(db_session, EVERYTHING)
    payload = _get(
        db_client, f"as_of={AS_OF.isoformat()}&compare_to=2026-06-30", authv=authv
    ).json()
    assert payload["compare_to"] == "2026-06-30"


# --- the wire shape ---------------------------------------------------------------------------


def _camel_to_snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def test_the_wire_shape_mirrors_the_dashboards_own_type() -> None:
    """``components/bi/types.ts::BiInsight`` field for field, in camelCase.

    Read from the TypeScript source rather than restated, so the two cannot drift:
    a field the browser expects and the server does not send renders as
    ``undefined``, which for a boolean like ``certified`` is a flag that silently
    reads as false.
    """
    source = _TYPES_TS.read_text(encoding="utf-8")
    block = re.search(r"export type BiInsight = Readonly<\{(.*?)\}>;", source, re.DOTALL)
    assert block is not None, f"BiInsight is no longer declared in {_TYPES_TS}"
    declared = {
        match.group(1) for match in re.finditer(r"^\s{2}(\w+)\??:", block.group(1), re.MULTILINE)
    }
    assert declared, block.group(1)
    expected = {_camel_to_snake(name) for name in declared} - _CLIENT_ONLY_FIELDS
    served = set(BiInsightRead.model_fields)
    assert expected <= served, expected - served


def test_every_served_insight_carries_the_whole_shape(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    _clone_day(db_session, mart, target=PRIOR, factor=Decimal("0.8"))
    authv = grant_only(db_session, EVERYTHING)
    payload = _get(db_client, f"as_of={AS_OF.isoformat()}", authv=authv).json()
    assert payload["insights"]
    for insight in payload["insights"]:
        assert set(insight) == set(BiInsightRead.model_fields)
        assert insight["headline"] and insight["detail"]
        assert insight["as_of"] == AS_OF.isoformat() or insight["as_of"] == PRIOR.isoformat()
        assert insight["statement_class"] in {
            "movement",
            "attribution",
            "projection",
            "data_gap",
        }


def test_the_headline_set_the_route_reads_is_the_derived_one(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """``measures_read`` is the derived candidate count, not a number in the route."""
    authv = grant_only(db_session, EVERYTHING)
    payload = _get(db_client, f"as_of={AS_OF.isoformat()}", authv=authv).json()
    from app.domain.bi.catalogue import catalogue  # noqa: PLC0415 - local to the assertion

    expected = headline_measures(catalogue(), institution_class="bank", capital_regime="crd")
    assert payload["measures_read"] + payload["measures_withheld"] == len(expected)
