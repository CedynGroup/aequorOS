"""The four optional Data Engine fields, end to end through the push API.

The unit suite (``tests/domain/ingestion/test_optional_position_fields.py``) pins
the normalisation and the messages. This one pins the seam: that a real push
lands the normalised values in ``canonical_position_snapshots.attributes``, that a
malformed value is dropped and reported without losing the position, and that a
push carrying none of the four behaves exactly as it did before they existed.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.session import get_sessionmaker
from app.models import CanonicalPosition, CanonicalPositionSnapshot
from tests.api.helpers import ORG_1
from tests.api.test_ingestion import seed_bank
from tests.api.test_push_api import _human_headers, commit, open_push, stage

pytestmark = pytest.mark.committing_db

AS_OF = "2026-06-30"

GL_ACCOUNTS = [
    {
        "source_reference": "1000",
        "account_code": "1000",
        "name": "Loans and advances",
        "account_class": "ASSET",
        "currency": "GHS",
    }
]


def _push(client: TestClient, bank_id: str, key: str, positions: list[dict[str, Any]]) -> Any:
    opened = open_push(client, bank_id, key)
    assert opened.status_code == 201, opened.text
    push_id = opened.json()["push_batch_id"]
    staged = stage(
        client,
        bank_id,
        push_id,
        {"entities": {"gl_account": GL_ACCOUNTS, "position": positions}},
    )
    assert staged.status_code == 200, staged.text
    committed = commit(client, bank_id, push_id)
    assert committed.status_code == 201, committed.text
    return committed.json()["batch"]


def _attributes(bank_id: str) -> dict[str, dict[str, Any]]:
    """The current generation's ``attributes`` bag per position reference."""
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        rows = session.execute(
            select(CanonicalPosition.source_reference, CanonicalPositionSnapshot.attributes)
            .join(
                CanonicalPositionSnapshot,
                CanonicalPositionSnapshot.position_id == CanonicalPosition.id,
            )
            .where(CanonicalPosition.bank_id == bank_id)
        ).all()
        return {reference: dict(attributes or {}) for reference, attributes in rows}
    finally:
        session.close()


def _findings(batch: dict[str, Any], rule: str) -> list[dict[str, Any]]:
    return [
        failure for failure in batch["validation_report"]["failures"] if failure["rule"] == rule
    ]


class TestPushedOptionalFieldsLandNormalised:
    def test_all_four_fields_are_stored_under_one_spelling(self, db_client: TestClient) -> None:
        bank_id = seed_bank(db_client)
        batch = _push(
            db_client,
            bank_id,
            "optional-fields-001",
            [
                {
                    "source_reference": "LN-0001",
                    "position_type": "LOAN",
                    "currency": "GHS",
                    "balance": 250000,
                    "gl_account_code": "1000",
                    "attributes": {
                        "officer_id": "  RM-014 ",
                        "channel": "Mobile Money",
                        "account_status": "Dormant",
                        "arrears_amount": "12,500.75",
                        "branch_id": "BR-01",
                    },
                }
            ],
        )
        assert batch["status"] == "accepted"
        assert _findings(batch, "optional_position_attributes") == []
        assert _attributes(bank_id)["LN-0001"] == {
            "officer_id": "RM-014",
            "channel": "mobile_money",
            "account_status": "dormant",
            "arrears_amount": "12500.75",
            "branch_id": "BR-01",
        }

    def test_a_malformed_value_is_dropped_and_reported_without_losing_the_position(
        self, db_client: TestClient
    ) -> None:
        bank_id = seed_bank(db_client)
        batch = _push(
            db_client,
            bank_id,
            "optional-fields-002",
            [
                {
                    "source_reference": "LN-0002",
                    "position_type": "LOAN",
                    "currency": "GHS",
                    "balance": 100000,
                    "gl_account_code": "1000",
                    "attributes": {
                        "channel": "carrier pigeon",
                        "arrears_amount": "not a number",
                        "officer_id": "RM-014",
                    },
                }
            ],
        )
        assert batch["status"] == "accepted_with_warnings"
        assert batch["records_accepted"] == 1  # the gl_account
        assert batch["records_warning"] == 1  # the position
        details = [
            failure["detail"] for failure in _findings(batch, "optional_position_attributes")
        ]
        assert len(details) == 2
        assert any("'carrier pigeon'" in detail and "mobile_money" in detail for detail in details)
        assert any("'not a number'" in detail for detail in details)
        assert all(
            failure["source_reference"] == "LN-0002"
            for failure in _findings(batch, "optional_position_attributes")
        )

        # The facility still landed, and the unusable keys are simply absent —
        # "not stated", never a default and never free text.
        stored = _attributes(bank_id)["LN-0002"]
        assert stored == {"officer_id": "RM-014"}
        positions = db_client.get(
            f"/api/v1/banks/{bank_id}/canonical-positions",
            headers=_human_headers(),
        ).json()["positions"]
        by_reference = {row["source_reference"]: row for row in positions}
        assert by_reference["LN-0002"]["validation_status"] == "warning"

    def test_a_push_carrying_none_of_the_four_is_unaffected(self, db_client: TestClient) -> None:
        bank_id = seed_bank(db_client)
        batch = _push(
            db_client,
            bank_id,
            "optional-fields-003",
            [
                {
                    "source_reference": "LN-0003",
                    "position_type": "LOAN",
                    "currency": "GHS",
                    "balance": 5000,
                    "gl_account_code": "1000",
                    "attributes": {"sector": "agriculture", "branch_id": "BR-02"},
                }
            ],
        )
        assert batch["status"] == "accepted"
        assert batch["validation_report"]["failures"] == []
        assert _attributes(bank_id)["LN-0003"] == {"sector": "agriculture", "branch_id": "BR-02"}

    def test_a_readable_but_odd_figure_is_noted_without_changing_the_record(
        self, db_client: TestClient
    ) -> None:
        bank_id = seed_bank(db_client)
        batch = _push(
            db_client,
            bank_id,
            "optional-fields-004",
            [
                {
                    "source_reference": "LN-0004",
                    "position_type": "LOAN",
                    "currency": "GHS",
                    "balance": 1000,
                    "gl_account_code": "1000",
                    # A unit error: arrears reported in minor units.
                    "attributes": {"arrears_amount": "100000"},
                }
            ],
        )
        (finding,) = _findings(batch, "position_attribute_consistency")
        assert finding["severity"] == "INFO"
        assert "exceeds the outstanding balance" in finding["detail"]
        # INFO alone never downgrades the record, and the figure is stored as sent.
        assert batch["status"] == "accepted"
        assert _attributes(bank_id)["LN-0004"] == {"arrears_amount": "100000"}
