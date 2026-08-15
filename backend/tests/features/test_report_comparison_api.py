"""Report-comparison API: the frozen JSON contract, both modes, and the boundary.

Drives ``GET /banks/{bank_id}/reports/comparison`` end to end. Seeds immutable
runs directly (like tests/features/test_market_data_sources_api.py), then asserts
the response shape the frontend depends on, the version/period resolution, and
that neither the tenant boundary nor a non-comparable request leaks.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.db.session import get_sessionmaker
from app.models import Bank, BankReportingPeriod, RegulatoryRun
from tests.api.helpers import ORG_1, ORG_2, USER_1, USER_2, headers

BASE = "/api/v1"


def _set_org(session: Session, org_id: str) -> None:
    if session.get_bind().dialect.name == "postgresql":
        session.execute(
            sql_text("SELECT set_config('app.organization_id', :org, false)"),
            {"org": org_id},
        )


def _bank(session: Session, org_id: str, name: str) -> str:
    bank = Bank(
        organization_id=org_id,
        name=name,
        short_name="CMPB",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
    )
    session.add(bank)
    session.flush()
    return bank.id


def _period(session: Session, org_id: str, bank_id: str, end: date, label: str) -> UUID:
    period = BankReportingPeriod(
        organization_id=org_id,
        bank_id=bank_id,
        period_start=date(end.year, end.month, 1),
        period_end=end,
        label=label,
        status="closed",
    )
    session.add(period)
    session.flush()
    return period.id


def _run(  # noqa: PLR0913 - a run needs its full scoping
    session: Session,
    org_id: str,
    bank_id: str,
    period_id: UUID,
    metrics: dict[str, object],
    created_at: datetime,
    *,
    module: str = "capital",
    scenario_code: str = "baseline",
    created_by: UUID = USER_1,
) -> UUID:
    run = RegulatoryRun(
        organization_id=org_id,
        bank_id=bank_id,
        reporting_period_id=period_id,
        module=module,
        scenario_code=scenario_code,
        status="succeeded",
        engine_version="regulatory-capital-v1.0.0",
        input_schema_version="bank-facts-v2",
        output_schema_version="v1",
        input_hash=f"hash-{created_at.isoformat()}",
        inputs={"source": "test"},
        metrics=metrics,
        created_at=created_at,
        started_at=created_at,
        completed_at=created_at,
        created_by=created_by,
    )
    session.add(run)
    session.flush()
    return run.id


def _line(payload: dict, key: str) -> dict:
    for group in payload["groups"]:
        for line in group["lines"]:
            if line["key"] == key:
                return line
    raise AssertionError(f"line {key} missing from response")


def test_version_mode_contract(db_client: TestClient) -> None:
    with get_sessionmaker()() as session:
        _set_org(session, ORG_1)
        bank_id = _bank(session, ORG_1, "Version Bank")
        period_id = _period(session, ORG_1, bank_id, date(2026, 3, 31), "2026-Q1")
        v1 = _run(
            session,
            ORG_1,
            bank_id,
            period_id,
            {"car_pct": "12.5", "total_rwa_ghs": "1000"},
            datetime(2026, 4, 1, 10, tzinfo=UTC),
        )
        v2 = _run(
            session,
            ORG_1,
            bank_id,
            period_id,
            {"car_pct": "13.0", "total_rwa_ghs": "1100"},
            datetime(2026, 4, 2, 10, tzinfo=UTC),
        )
        session.commit()

    response = db_client.get(
        f"{BASE}/banks/{bank_id}/reports/comparison",
        params={"mode": "version", "module": "capital", "left": str(v1), "right": str(v2)},
        headers=headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["mode"] == "version"
    assert body["module"] == "capital"
    assert body["left"]["run_id"] == str(v1)
    assert body["left"]["version"] == 1
    assert body["left"]["period_label"] == "2026-Q1"
    assert body["right"]["run_id"] == str(v2)
    assert body["right"]["version"] == 2

    car = _line(body, "car_pct")
    assert car == {
        "key": "car_pct",
        "label": "Capital adequacy ratio (CAR)",
        "unit": "pct",
        "left_value": "12.5",
        "right_value": "13.0",
        "delta_ccy": "0.5",
        "delta_pct": "4.00",
        "direction": "up",
        "favorability": "favorable",
        "new": False,
    }
    assert _line(body, "total_rwa_ghs")["favorability"] == "adverse"
    assert body["favorable_count"] == 1
    assert body["adverse_count"] == 1
    assert body["neutral_count"] == 0


def test_period_mode(db_client: TestClient) -> None:
    with get_sessionmaker()() as session:
        _set_org(session, ORG_1)
        bank_id = _bank(session, ORG_1, "Period Bank")
        mar = _period(session, ORG_1, bank_id, date(2026, 3, 31), "2026-Q1")
        jun = _period(session, ORG_1, bank_id, date(2026, 6, 30), "2026-Q2")
        _run(
            session, ORG_1, bank_id, mar, {"car_pct": "12.0"}, datetime(2026, 4, 1, tzinfo=UTC)
        )
        _run(
            session, ORG_1, bank_id, jun, {"car_pct": "12.8"}, datetime(2026, 7, 1, tzinfo=UTC)
        )
        session.commit()

    response = db_client.get(
        f"{BASE}/banks/{bank_id}/reports/comparison",
        params={"mode": "period", "module": "capital", "left": str(mar), "right": str(jun)},
        headers=headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "period"
    assert body["left"]["period_label"] == "2026-Q1"
    assert body["right"]["period_label"] == "2026-Q2"
    car = _line(body, "car_pct")
    assert car["direction"] == "up"
    assert car["favorability"] == "favorable"


def test_non_comparable_returns_422(db_client: TestClient) -> None:
    with get_sessionmaker()() as session:
        _set_org(session, ORG_1)
        bank_id = _bank(session, ORG_1, "Mixed Bank")
        period_id = _period(session, ORG_1, bank_id, date(2026, 3, 31), "2026-Q1")
        cap = _run(
            session,
            ORG_1,
            bank_id,
            period_id,
            {"car_pct": "12"},
            datetime(2026, 4, 1, 10, tzinfo=UTC),
            module="capital",
        )
        liq = _run(
            session,
            ORG_1,
            bank_id,
            period_id,
            {"lcr_pct": "150"},
            datetime(2026, 4, 1, 11, tzinfo=UTC),
            module="liquidity",
        )
        session.commit()

    response = db_client.get(
        f"{BASE}/banks/{bank_id}/reports/comparison",
        params={"mode": "version", "module": "capital", "left": str(cap), "right": str(liq)},
        headers=headers(),
    )
    assert response.status_code == 422, response.text


def test_missing_run_returns_404(db_client: TestClient) -> None:
    with get_sessionmaker()() as session:
        _set_org(session, ORG_1)
        bank_id = _bank(session, ORG_1, "Sparse Bank")
        period_id = _period(session, ORG_1, bank_id, date(2026, 3, 31), "2026-Q1")
        real = _run(
            session, ORG_1, bank_id, period_id, {"car_pct": "12"}, datetime(2026, 4, 1, tzinfo=UTC)
        )
        session.commit()

    response = db_client.get(
        f"{BASE}/banks/{bank_id}/reports/comparison",
        params={
            "mode": "version",
            "module": "capital",
            "left": str(real),
            "right": str(uuid4()),
        },
        headers=headers(),
    )
    assert response.status_code == 404, response.text


def test_invalid_query_returns_422(db_client: TestClient) -> None:
    with get_sessionmaker()() as session:
        _set_org(session, ORG_1)
        bank_id = _bank(session, ORG_1, "Bank")
        session.commit()

    # Unknown module value.
    bad_module = db_client.get(
        f"{BASE}/banks/{bank_id}/reports/comparison",
        params={
            "mode": "version",
            "module": "nonsense",
            "left": str(uuid4()),
            "right": str(uuid4()),
        },
        headers=headers(),
    )
    assert bad_module.status_code == 422
    # Missing required 'mode'.
    missing_mode = db_client.get(
        f"{BASE}/banks/{bank_id}/reports/comparison",
        params={"module": "capital", "left": str(uuid4()), "right": str(uuid4())},
        headers=headers(),
    )
    assert missing_mode.status_code == 422


def test_tenant_isolation(db_client: TestClient) -> None:
    with get_sessionmaker()() as session:
        _set_org(session, ORG_2)
        other_bank = _bank(session, ORG_2, "Neighbour Bank")
        other_period = _period(session, ORG_2, other_bank, date(2026, 3, 31), "2026-Q1")
        other_run = _run(
            session,
            ORG_2,
            other_bank,
            other_period,
            {"car_pct": "12"},
            datetime(2026, 4, 1, tzinfo=UTC),
            created_by=USER_2,
        )
        session.commit()

    params = {
        "mode": "version",
        "module": "capital",
        "left": str(other_run),
        "right": str(other_run),
    }
    # ORG_1 cannot see ORG_2's bank.
    denied = db_client.get(
        f"{BASE}/banks/{other_bank}/reports/comparison", params=params, headers=headers(ORG_1)
    )
    assert denied.status_code == 404, denied.text
    # Its owner can.
    allowed = db_client.get(
        f"{BASE}/banks/{other_bank}/reports/comparison", params=params, headers=headers(ORG_2)
    )
    assert allowed.status_code == 200, allowed.text
