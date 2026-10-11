from __future__ import annotations

import os
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import insert, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DatabaseError

from app.db.base import Base
from tests.db.test_governance_append_only import (
    connection as connection,  # noqa: PLC0414 - register shared pytest fixture
)
from tests.db.test_governance_append_only import (
    governance_schema as governance_schema,  # noqa: PLC0414 - register shared pytest fixture
)
from tests.support.helpers import ORG_1

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL required")

PARAMETERS: dict[str, dict[str, object]] = {
    "param_lcr_runoff_rate": {"flow_direction": "outflow", "category": "test", "rate_pct": 5},
    "param_nsfr_weight": {"side": "asf", "category": "test", "weight_pct": 5},
    "param_risk_weight": {"risk_weight_code": "test", "weight_pct": 5},
    "param_stress_shock": {
        "module": "capital",
        "scenario_code": "test",
        "shock_key": "test",
        "shock_value": 5,
    },
    "param_capital_threshold": {"threshold_code": "test", "value_pct": 5},
    "param_concentration_limit": {
        "dimension": "sector",
        "limit_kind": "share_of_book_pct",
        "value": 5,
    },
    "param_credit_threshold": {"threshold_code": "test", "value_pct": 5},
    "param_liquidity_threshold": {
        "institution_class": "bank",
        "threshold_code": "test",
        "threshold_pct": 5,
    },
    "param_liquidity_haircut": {"asset_class": "test", "haircut_pct": 5},
    "param_ecl_assumption": {"segment": "test", "stage": 1, "pd_pct": 5, "lgd_pct": 5},
    "param_crm_haircut": {"collateral_class": "test", "haircut_pct": 5},
}


def _reject(connection: Connection, sql: str, parameters: dict[str, object]) -> None:
    words = sql.split()
    table = words[2] if words[0] == "DELETE" else words[1]
    assert table in Base.metadata.tables
    connection.execute(text(f"GRANT UPDATE, DELETE, TRUNCATE ON {table} TO CURRENT_USER"))
    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match="sealed|retained evidence|append-only|write-once"):
        connection.execute(text(sql), parameters)
    savepoint.rollback()


@pytest.mark.parametrize("table", list(PARAMETERS))
def test_approved_parameter_value_and_evidence_are_retained(
    connection: Connection, table: str
) -> None:
    identifier, now = uuid4(), datetime.now(UTC)
    values: dict[str, object] = {
        "id": identifier,
        "organization_id": ORG_1,
        "jurisdiction_code": "GH",
        "effective_from": date(2026, 10, 1),
        "approved_by": "Synthetic Board",
        "approval_timestamp": now,
        "created_at": now,
        "updated_at": now,
        **PARAMETERS[table],
    }
    connection.execute(insert(Base.metadata.tables[table]).values(**values))
    value_column = next(key for key, value in PARAMETERS[table].items() if isinstance(value, int))
    if value_column == "stage":
        value_column = "pd_pct"
    for sql in (
        f"UPDATE {table} SET {value_column} = 9 WHERE id = :id",
        f"UPDATE {table} SET approved_by = 'Forged Board' WHERE id = :id",
        f"DELETE FROM {table} WHERE id = :id",
        f"TRUNCATE {table}",
    ):
        _reject(connection, sql, {"id": identifier})
    connection.execute(
        text(f"UPDATE {table} SET effective_to = DATE '2027-01-01' WHERE id = :id"),
        {"id": identifier},
    )


def test_assumption_approval_is_permanent_but_draft_stays_editable(connection: Connection) -> None:
    identifier, maker, checker = uuid4(), uuid4(), uuid4()
    connection.execute(
        text("""
        INSERT INTO forecast_assumption_versions
          (id, organization_id, bank_id, version_number, status, effective_from, presets,
           change_note, created_by, created_at, updated_at)
        VALUES (:id, :org, 'BK-SEAL001', 1, 'draft', DATE '2026-10-01', '{}',
                'Synthetic assumptions', :maker, now(), now())
    """),
        {"id": identifier, "org": ORG_1, "maker": maker},
    )
    connection.execute(
        text(
            "UPDATE forecast_assumption_versions SET status = 'approved', "
            "reviewed_by = :checker, reviewed_at = now() WHERE id = :id"
        ),
        {"id": identifier, "checker": checker},
    )
    _reject(
        connection,
        "UPDATE forecast_assumption_versions SET presets = '{\"growth\": 99}' WHERE id = :id",
        {"id": identifier},
    )
    _reject(
        connection, "DELETE FROM forecast_assumption_versions WHERE id = :id", {"id": identifier}
    )


def test_role_scope_and_revocation_are_permanent(connection: Connection) -> None:
    user, binding = uuid4(), uuid4()
    connection.execute(
        text("""
        INSERT INTO users (id, organization_id, email, display_name, is_active,
                           created_at, updated_at)
        VALUES (:id, :org, 'synthetic@example.test', 'Synthetic User', true, now(), now())
    """),
        {"id": user, "org": ORG_1},
    )
    connection.execute(
        text("""
        INSERT INTO authorization_bindings
          (id, organization_id, principal_user_id, principal_type, role_bundle,
           institution_scope, institution_id, module_scope, sensitivity_scope,
           granted_by_type, granted_by_id, grant_reason, granted_at, status,
           valid_from, created_at, updated_at)
        VALUES (:id, :org, :user, 'human', 'analyst', 'institution', 'BK-SEAL001',
                'credit', 'confidential', 'system', 'synthetic', 'Synthetic grant',
                now(), 'active', now(), now(), now())
    """),
        {"id": binding, "org": ORG_1, "user": user},
    )
    _reject(
        connection,
        "UPDATE authorization_bindings SET module_scope = 'all' WHERE id = :id",
        {"id": binding},
    )
    connection.execute(
        text(
            "UPDATE authorization_bindings SET status = 'revoked', revoked_at = now(), "
            "revoked_by_type = 'system', revoked_by_id = 'synthetic', "
            "revoked_reason = 'Synthetic revocation' WHERE id = :id"
        ),
        {"id": binding},
    )
    _reject(
        connection,
        "UPDATE authorization_bindings SET revoked_reason = 'Forged' WHERE id = :id",
        {"id": binding},
    )
    _reject(
        connection,
        "UPDATE authorization_bindings SET status = 'active' WHERE id = :id",
        {"id": binding},
    )
    _reject(connection, "DELETE FROM authorization_bindings WHERE id = :id", {"id": binding})


def test_legacy_assumption_history_cannot_be_rewritten(connection: Connection) -> None:
    identifier, case = uuid4(), uuid4()
    connection.execute(
        text("""
        INSERT INTO risk_cases (id, organization_id, title, case_type, status,
                               created_at, updated_at)
        VALUES (:id, :org, 'Synthetic case', 'test', 'draft', now(), now())
    """),
        {"id": case, "org": ORG_1},
    )
    connection.execute(
        text("""
        INSERT INTO scenario_assumption_history
          (id, organization_id, case_id, scenario_id, assumption_id, action, changed_fields,
           reason, changed_by, created_at)
        VALUES (:id, :org, :case, :scenario, :assumption, 'approved', '{}', 'Synthetic approval',
                :actor, now())
    """),
        {
            "id": identifier,
            "org": ORG_1,
            "case": case,
            "scenario": uuid4(),
            "assumption": uuid4(),
            "actor": uuid4(),
        },
    )
    _reject(
        connection,
        "UPDATE scenario_assumption_history SET reason = 'Forged' WHERE id = :id",
        {"id": identifier},
    )
    _reject(
        connection, "DELETE FROM scenario_assumption_history WHERE id = :id", {"id": identifier}
    )


def test_financial_edit_history_is_append_only(connection: Connection) -> None:
    identifier, case = uuid4(), uuid4()
    connection.execute(
        text("""
        INSERT INTO risk_cases (id, organization_id, title, case_type, status,
                               created_at, updated_at)
        VALUES (:id, :org, 'Synthetic case', 'test', 'draft', now(), now())
    """),
        {"id": case, "org": ORG_1},
    )
    connection.execute(
        insert(Base.metadata.tables["financial_manual_edit_history"]).values(
            id=identifier,
            organization_id=ORG_1,
            case_id=case,
            record_table="financial_balances",
            record_id=uuid4(),
            field_name="balance",
            previous_value=7,
            new_value=8,
            reason="Synthetic correction",
        )
    )
    _reject(
        connection,
        "UPDATE financial_manual_edit_history SET reason = 'Forged' WHERE id = :id",
        {"id": identifier},
    )
    _reject(
        connection, "DELETE FROM financial_manual_edit_history WHERE id = :id", {"id": identifier}
    )
