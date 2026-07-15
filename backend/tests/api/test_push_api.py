"""Push API journeys: JSON in over three calls, canonical state and lineage out.

The push flow is the same pipeline as file ingestion — these tests assert the
staging endpoints' contract (idempotency, page cap, envelope validation,
tenant isolation) and that committed pushes land in canonical state exactly
like an uploaded workbook would.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy import text as sql_text

from app.db.session import get_sessionmaker
from app.models import CanonicalGlAccount, CanonicalReferenceRow
from app.services.push_ingestion import IDENTITY_MAPPING_NAME
from tests.api.helpers import ORG_1, ORG_2, headers
from tests.api.test_ingestion import seed_bank

AS_OF = "2026-06-30"

GL_ACCOUNTS = [
    {
        "source_reference": "1000",
        "account_code": "1000",
        "name": "Cash and balances",
        "account_class": "ASSET",
        "currency": "GHS",
    },
    {
        "source_reference": "2000",
        "account_code": "2000",
        "name": "Customer deposits",
        "account_class": "LIABILITY",
        "currency": "GHS",
    },
]

PRODUCTS = [
    {
        "source_reference": "LN.CORP.5Y",
        "product_code": "LN.CORP.5Y",
        "name": "5y corporate loan",
        "regulatory_category": "CORPORATE_LOAN_UNRATED_100RW",
    },
    {
        "source_reference": "DP.CURRENT",
        "product_code": "DP.CURRENT",
        "name": "Current account",
    },
]

COUNTERPARTIES = [
    {
        "source_reference": "C-001",
        "name": "Volta Agro Ltd",
        "counterparty_type": "CORPORATE",
        "country_code": "GH",
        "external_identifiers": {"tin": "GHA-123"},
    },
]

POSITIONS = [
    {
        "source_reference": "LN-0001",
        "position_type": "LOAN",
        "currency": "GHS",
        "balance": 1500000.5,
        "counterparty_reference": "C-001",
        "product_code": "LN.CORP.5Y",
        "interest_rate": 0.245,
        "rate_type": "FIXED",
        "origination_date": "2026-03-15",
        "contractual_maturity": "2031-03-15",
    },
    {
        "source_reference": "FXH-0001",
        "position_type": "FX_HEDGE",
        "currency": "USD",
        "balance": "250000",
        "notional": "250000",
        "contractual_maturity": "2026-12-31",
        "attributes": {"currency_pair": "USDGHS", "contract_rate": "15.10"},
    },
]

YIELD_CURVE = [
    {"curve_name": "GHS_SOVEREIGN", "tenor_months": 3, "rate": 0.241, "quote_date": AS_OF},
    {"curve_name": "GHS_SOVEREIGN", "tenor_months": 12, "rate": 0.262, "quote_date": AS_OF},
]


def open_push(
    client: TestClient, bank_id: str, key: str, as_of: str = AS_OF, org: UUID = ORG_1
) -> Any:
    return client.post(
        f"/api/v1/banks/{bank_id}/push-batches",
        headers=headers(org),
        json={"as_of_date": as_of, "idempotency_key": key, "reason": "Nightly middleware push."},
    )


def stage(client: TestClient, bank_id: str, push_id: str, page: dict[str, Any]) -> Any:
    return client.post(
        f"/api/v1/banks/{bank_id}/push-batches/{push_id}/records",
        headers=headers(),
        json=page,
    )


def commit(client: TestClient, bank_id: str, push_id: str) -> Any:
    return client.post(f"/api/v1/banks/{bank_id}/push-batches/{push_id}/commit", headers=headers())


def push_everything(client: TestClient, bank_id: str, key: str) -> tuple[str, dict[str, Any]]:
    """Open → two record pages → commit; returns (push_id, commit body)."""
    opened = open_push(client, bank_id, key)
    assert opened.status_code == 201, opened.text
    push_id = opened.json()["push_batch_id"]

    first = stage(
        client, bank_id, push_id, {"entities": {"gl_account": GL_ACCOUNTS, "product": PRODUCTS}}
    )
    assert first.status_code == 200, first.text
    assert first.json()["records_staged"] == {"gl_account": 2, "product": 2}

    second = stage(
        client,
        bank_id,
        push_id,
        {
            "entities": {"counterparty": COUNTERPARTIES, "position": POSITIONS},
            "reference": {"yield_curve": YIELD_CURVE},
        },
    )
    assert second.status_code == 200, second.text
    assert second.json()["pages_staged"] == 2
    assert second.json()["total_records_staged"] == 9

    committed = commit(client, bank_id, push_id)
    assert committed.status_code == 201, committed.text
    return push_id, committed.json()


def _org_scoped_rows(model: Any, order_by: Any) -> list[Any]:
    session = get_sessionmaker()()
    try:
        if session.get_bind().dialect.name == "postgresql":
            session.execute(
                sql_text("SELECT set_config('app.organization_id', :org, true)"),
                {"org": str(ORG_1)},
            )
        return list(session.scalars(select(model).order_by(order_by)).all())
    finally:
        session.close()


class TestPushHappyPath:
    def test_three_call_flow_lands_canonical_state(self, db_client: TestClient) -> None:
        bank_id = seed_bank(db_client)
        _, started = push_everything(db_client, bank_id, "push-2026-06-30-001")

        batch = started["batch"]
        assert started["reused"] is False
        assert batch["source_system"] == "API_PUSH"
        assert batch["status"] == "accepted"
        assert batch["records_extracted"] == 9
        assert batch["records_translated"] == 9
        assert batch["records_accepted"] == 9
        assert batch["validation_report"]["summary"]["reference_rows"] == {"yield_curve": 2}
        assert batch["raw_artifact_path"] == f"api_push/{AS_OF}/{batch['id']}/source.json"

        # The per-table breakdown lists every pushed key, resolved.
        tables = {entry["source_table"]: entry for entry in batch["validation_report"]["tables"]}
        assert set(tables) == {"gl_account", "product", "counterparty", "position", "yield_curve"}
        assert tables["position"]["resolved_to"] == "position"
        assert tables["position"]["rows_extracted"] == 2
        assert tables["yield_curve"]["resolved_to"] == "reference:yield_curve"
        assert tables["yield_curve"]["rows_accepted"] == 2

        positions = db_client.get(
            f"/api/v1/banks/{bank_id}/canonical-positions", headers=headers()
        ).json()["positions"]
        by_reference = {position["source_reference"]: position for position in positions}
        assert set(by_reference) == {"LN-0001", "FXH-0001"}
        assert by_reference["LN-0001"]["source_system"] == "API_PUSH"
        assert by_reference["FXH-0001"]["position_type"] == "FX_HEDGE"
        assert by_reference["LN-0001"]["validation_status"] == "accepted"

        walk = db_client.get(
            f"/api/v1/lineage/{by_reference['LN-0001']['lineage_id']}", headers=headers()
        ).json()
        assert [node["operation_type"] for node in walk["nodes"]] == [
            "VALIDATION",
            "ADAPTER_TRANSLATE",
            "ADAPTER_EXTRACT",
        ]

    def test_reference_rows_land_in_the_canonical_reference_table(
        self, db_client: TestClient
    ) -> None:
        bank_id = seed_bank(db_client)
        _, started = push_everything(db_client, bank_id, "push-refs-001")

        rows = _org_scoped_rows(CanonicalReferenceRow, CanonicalReferenceRow.row_index)
        assert [row.row_index for row in rows] == [1, 2]
        assert {row.dataset_kind for row in rows} == {"yield_curve"}
        assert rows[0].payload["curve_name"] == "GHS_SOVEREIGN"
        assert rows[0].payload["tenor_months"] == "3"
        assert str(rows[0].ingestion_batch_id) == started["batch"]["id"]
        assert rows[0].source_reference.startswith("source.json#yield_curve!R")

    def test_identity_mapping_is_auto_provisioned(self, db_client: TestClient) -> None:
        bank_id = seed_bank(db_client)
        push_everything(db_client, bank_id, "push-identity-001")

        configs = db_client.get(
            f"/api/v1/banks/{bank_id}/mapping-configs", headers=headers()
        ).json()["configs"]
        api_push_configs = [c for c in configs if c["source_system"] == "API_PUSH"]
        assert len(api_push_configs) == 1
        assert api_push_configs[0]["name"] == IDENTITY_MAPPING_NAME
        assert api_push_configs[0]["status"] == "active"


class TestPushIdempotency:
    def test_recommitting_the_same_push_batch_returns_the_same_batch(
        self, db_client: TestClient
    ) -> None:
        bank_id = seed_bank(db_client)
        push_id, first = push_everything(db_client, bank_id, "push-idem-001")

        again = commit(db_client, bank_id, push_id)
        assert again.status_code == 201
        assert again.json()["reused"] is True
        assert again.json()["batch"]["id"] == first["batch"]["id"]

        status = db_client.get(
            f"/api/v1/banks/{bank_id}/push-batches/{push_id}", headers=headers()
        ).json()
        assert status["status"] == "committed"
        assert status["committed_batch_id"] == first["batch"]["id"]

    def test_reopening_the_same_idempotency_key_returns_the_same_push_batch(
        self, db_client: TestClient
    ) -> None:
        bank_id = seed_bank(db_client)
        first = open_push(db_client, bank_id, "push-key-001")
        second = open_push(db_client, bank_id, "push-key-001")
        assert second.status_code == 201
        assert second.json()["push_batch_id"] == first.json()["push_batch_id"]

        conflicting = open_push(db_client, bank_id, "push-key-001", as_of="2026-07-31")
        assert conflicting.status_code == 409

    def test_identical_content_under_a_new_key_reuses_the_accepted_batch(
        self, db_client: TestClient
    ) -> None:
        bank_id = seed_bank(db_client)
        _, first = push_everything(db_client, bank_id, "push-content-001")
        _, second = push_everything(db_client, bank_id, "push-content-002")
        assert second["reused"] is True
        assert second["batch"]["id"] == first["batch"]["id"]

    def test_staging_into_a_committed_push_batch_conflicts(self, db_client: TestClient) -> None:
        bank_id = seed_bank(db_client)
        push_id, _ = push_everything(db_client, bank_id, "push-locked-001")
        late = stage(db_client, bank_id, push_id, {"entities": {"gl_account": GL_ACCOUNTS}})
        assert late.status_code == 409


class TestPushMapping:
    def test_aliased_mapping_translates_foreign_field_names(self, db_client: TestClient) -> None:
        bank_id = seed_bank(db_client)
        response = db_client.post(
            f"/api/v1/banks/{bank_id}/mapping-configs",
            headers=headers(),
            json={
                "source_system": "API_PUSH",
                "name": "Middleware field aliases",
                "config": {
                    "field_mappings": {
                        "gl_account": {
                            "source_table": "gl_account",
                            "fields": {
                                "source_reference": "AcctCode",
                                "account_code": "AcctCode",
                                "name": "AcctName",
                                "account_class": "Side",
                            },
                        },
                    },
                    "enum_mappings": {"account_class": {"A": "ASSET"}},
                },
                "activate": True,
                "reason": "Bank middleware cannot rename its export fields.",
            },
        )
        assert response.status_code == 200, response.text

        opened = open_push(db_client, bank_id, "push-alias-001")
        push_id = opened.json()["push_batch_id"]
        staged = stage(
            db_client,
            bank_id,
            push_id,
            {
                "entities": {
                    "gl_account": [{"AcctCode": "9001", "AcctName": "Vault cash", "Side": "A"}]
                }
            },
        )
        assert staged.status_code == 200, staged.text
        started = commit(db_client, bank_id, push_id)
        assert started.status_code == 201, started.text
        batch = started.json()["batch"]
        assert batch["status"] == "accepted"
        assert batch["records_accepted"] == 1

        rows = _org_scoped_rows(CanonicalGlAccount, CanonicalGlAccount.account_code)
        assert [row.account_code for row in rows] == ["9001"]
        assert rows[0].account_class == "ASSET"
        assert rows[0].source_system == "API_PUSH"


class TestPushValidation:
    def test_page_over_the_record_cap_is_rejected(self, db_client: TestClient) -> None:
        bank_id = seed_bank(db_client)
        push_id = open_push(db_client, bank_id, "push-cap-001").json()["push_batch_id"]
        oversized = [
            {
                "source_reference": f"GL-{index}",
                "account_code": f"GL-{index}",
                "name": "Filler",
                "account_class": "ASSET",
            }
            for index in range(5_001)
        ]
        response = stage(db_client, bank_id, push_id, {"entities": {"gl_account": oversized}})
        assert response.status_code == 413
        assert "5000" in response.json()["error"]["message"]

    def test_malformed_record_is_rejected_with_a_pointer(self, db_client: TestClient) -> None:
        bank_id = seed_bank(db_client)
        push_id = open_push(db_client, bank_id, "push-shape-001").json()["push_batch_id"]

        response = stage(db_client, bank_id, push_id, {"entities": {"gl_account": [42]}})
        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "validation_error"
        locs = [detail["loc"] for detail in error["details"]]
        assert any(loc[-3:] == ["entities", "gl_account", 0] for loc in locs)

        unknown_key = stage(db_client, bank_id, push_id, {"entities": {"ledger": []}})
        assert unknown_key.status_code == 422

        empty = stage(db_client, bank_id, push_id, {"entities": {"gl_account": []}})
        assert empty.status_code == 422

    def test_commit_without_records_is_rejected(self, db_client: TestClient) -> None:
        bank_id = seed_bank(db_client)
        push_id = open_push(db_client, bank_id, "push-empty-001").json()["push_batch_id"]
        response = commit(db_client, bank_id, push_id)
        assert response.status_code == 422
        assert "No records staged" in response.json()["error"]["message"]

    def test_untranslatable_push_records_are_preserved_for_review(
        self, db_client: TestClient
    ) -> None:
        bank_id = seed_bank(db_client)
        push_id = open_push(db_client, bank_id, "push-dirty-001").json()["push_batch_id"]
        stage(
            db_client,
            bank_id,
            push_id,
            {
                "entities": {
                    "position": [
                        {
                            "source_reference": "LN-BAD",
                            "position_type": "LOAN",
                            "currency": "GHS",
                            "balance": "GHS 1,500,000.50",  # spreadsheet chaos: not allowed here
                            "contractual_maturity": "15/03/2031",  # not ISO
                        },
                        POSITIONS[0],
                    ]
                }
            },
        )
        started = commit(db_client, bank_id, push_id)
        batch = started.json()["batch"]
        assert batch["records_translated"] == 1

        failures = db_client.get(
            f"/api/v1/banks/{bank_id}/ingestion-batches/{batch['id']}/translation-failures",
            headers=headers(),
        ).json()["failures"]
        assert len(failures) == 1
        assert failures[0]["error_code"] == "coercion_error"
        assert "balance" in failures[0]["error_message"]
        assert "contractual_maturity" in failures[0]["error_message"]
        assert failures[0]["raw_record"]["source_reference"] == "LN-BAD"


class TestPushTenantIsolation:
    def test_other_tenants_see_nothing(self, db_client: TestClient) -> None:
        bank_id = seed_bank(db_client)
        push_id, started = push_everything(db_client, bank_id, "push-tenant-001")

        foreign = headers(ORG_2)
        assert open_push(db_client, bank_id, "push-tenant-002", org=ORG_2).status_code == 404
        assert (
            db_client.get(
                f"/api/v1/banks/{bank_id}/push-batches/{push_id}", headers=foreign
            ).status_code
            == 404
        )
        assert (
            db_client.post(
                f"/api/v1/banks/{bank_id}/push-batches/{push_id}/commit", headers=foreign
            ).status_code
            == 404
        )
        assert (
            db_client.get(
                f"/api/v1/banks/{bank_id}/ingestion-batches/{started['batch']['id']}",
                headers=foreign,
            ).status_code
            == 404
        )
