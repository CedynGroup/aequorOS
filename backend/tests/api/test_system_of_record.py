"""System-of-record register + canonical withdrawal, over HTTP.

The service tests prove the rules; these prove the rules are actually reachable
and actually enforced at the boundary — the role split (analyst proposes and
requests, approver approves, revokes and reverses), the four-eyes refusal, and
the fact that a single-source bank's assessment comes back clean without anyone
having declared anything.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.models import (
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CurrentFinancialFact,
    IngestionBatch,
    Job,
    LineageRecord,
    User,
)
from tests.api.helpers import ORG_1, USER_1, headers
from tests.api.test_ingestion import seed_bank
from tests.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture

REGISTER_URL = "/api/v1/banks/{bank_id}/system-of-record"
ASSESSMENT_URL = "/api/v1/banks/{bank_id}/system-of-record-assessment"
WITHDRAWALS_URL = "/api/v1/banks/{bank_id}/canonical-withdrawals"
SCOPE_URL = "/api/v1/banks/{bank_id}/canonical-withdrawal-scope"

SECOND_SOURCE = "API_PUSH"
FIXTURE_SOURCE = "EXCEL_CSV"

#: A SECOND active user of the same tenant. Four-eyes is a claim about two
#: PEOPLE, so the checker must be a real, active user of the org — the seeded
#: ``USER_2`` belongs to the other tenant and is rejected at the boundary.
APPROVER_USER = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")


def _message(response) -> str:  # noqa: ANN001 - httpx.Response
    """The refusal text out of the platform's error envelope."""
    body = response.json()
    return body.get("error", {}).get("message", body.get("detail", ""))


def _seed(client: TestClient, *, duplicate: bool) -> str:
    bank_id = seed_bank(client)
    session = get_sessionmaker()()
    try:
        seed_canonical_fixture(session, organization_id=ORG_1, bank_id=bank_id)
        if session.get(User, APPROVER_USER) is None:
            session.add(
                User(
                    id=APPROVER_USER,
                    organization_id=ORG_1,
                    email="cro@bank.test",
                    display_name="Chief Risk Officer",
                    is_active=True,
                )
            )
            session.flush()
        if duplicate:
            batch = IngestionBatch(
                organization_id=ORG_1,
                bank_id=bank_id,
                source_system=SECOND_SOURCE,
                adapter_version="1.0",
                extraction_mode="full",
                status="accepted",
                as_of_date=FIXTURE_AS_OF,
            )
            session.add(batch)
            session.flush()
            lineage = LineageRecord(
                organization_id=ORG_1,
                ingestion_batch_id=batch.id,
                operation_type="ADAPTER_TRANSLATE",
                operation_ref="second-source-api-fixture",
                input_lineage_ids=[],
            )
            session.add(lineage)
            session.flush()
            common = {
                "organization_id": ORG_1,
                "bank_id": bank_id,
                "as_of_date": FIXTURE_AS_OF,
                "source_system": SECOND_SOURCE,
                "ingestion_batch_id": batch.id,
                "lineage_id": lineage.id,
                "validation_status": "accepted",
            }
            for index in range(3):
                reference = f"DUP-LOAN/{index}"
                position = CanonicalPosition(
                    **common,
                    source_reference=reference,
                    position_type="LOAN",
                    currency="GHS",
                )
                session.add(position)
                session.flush()
                session.add(
                    CanonicalPositionSnapshot(
                        **common,
                        source_reference=reference,
                        position_id=position.id,
                        balance=Decimal("100"),
                        attributes={"balance_ghs": "100"},
                    )
                )
        session.commit()
    finally:
        session.close()
    return bank_id


def test_a_single_source_bank_assesses_clean_with_an_empty_register(
    db_client: TestClient,
) -> None:
    bank_id = _seed(db_client, duplicate=False)

    assessment = db_client.get(
        ASSESSMENT_URL.format(bank_id=bank_id),
        headers=headers(),
        params={"as_of": FIXTURE_AS_OF.isoformat()},
    )
    assert assessment.status_code == 200, assessment.text
    body = assessment.json()
    assert body["clean"] is True
    assert body["findings"] == []
    assert body["message"] is None

    register = db_client.get(REGISTER_URL.format(bank_id=bank_id), headers=headers())
    assert register.status_code == 200, register.text
    assert register.json()["declarations"] == []


def test_the_full_governed_path_from_declaration_to_withdrawal(  # noqa: PLR0915
    db_client: TestClient,
) -> None:
    bank_id = _seed(db_client, duplicate=True)

    # Undeclared: the platform can size the duplication but cannot attribute it.
    undeclared = db_client.get(
        ASSESSMENT_URL.format(bank_id=bank_id),
        headers=headers(),
        params={"as_of": FIXTURE_AS_OF.isoformat()},
    ).json()
    assert undeclared["clean"] is False
    assert [f["finding"] for f in undeclared["findings"]] == ["undeclared"]

    proposed = db_client.post(
        REGISTER_URL.format(bank_id=bank_id),
        headers=headers(user_id=USER_1, roles=("analyst",)),
        json={
            "position_type": "LOAN",
            "source_system": FIXTURE_SOURCE,
            "effective_from": "2026-01-01",
            "source_citation": "IT sign-off ITSO-2026-014",
            "rationale": "Core banking is the book of record for lending.",
            "proposed_by": "analyst@bank.test",
        },
    )
    assert proposed.status_code == 201, proposed.text
    declaration_id = proposed.json()["id"]
    assert proposed.json()["status"] == "draft"

    # An analyst cannot approve; nor can the proposer, whatever role they hold.
    forbidden = db_client.post(
        f"{REGISTER_URL.format(bank_id=bank_id)}/{declaration_id}/approve",
        headers=headers(user_id=USER_1, roles=("analyst",)),
        json={"approved_by": "cro@bank.test"},
    )
    assert forbidden.status_code == 403, forbidden.text
    self_approved = db_client.post(
        f"{REGISTER_URL.format(bank_id=bank_id)}/{declaration_id}/approve",
        headers=headers(user_id=USER_1, roles=("approver",)),
        json={"approved_by": "cro@bank.test"},
    )
    assert self_approved.status_code == 422, self_approved.text
    assert "second approver" in _message(self_approved)

    approved = db_client.post(
        f"{REGISTER_URL.format(bank_id=bank_id)}/{declaration_id}/approve",
        headers=headers(user_id=APPROVER_USER, roles=("approver",)),
        json={"approved_by": "cro@bank.test"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

    # Now the same duplication is a named rule violation.
    violated = db_client.get(
        ASSESSMENT_URL.format(bank_id=bank_id),
        headers=headers(),
        params={"as_of": FIXTURE_AS_OF.isoformat()},
    ).json()
    finding = violated["findings"][0]
    assert finding["finding"] == "violated"
    assert finding["declared_source_system"] == FIXTURE_SOURCE
    assert finding["offending_rows"] == 3

    # The remedy: see the scope, request it, have a second officer approve it.
    scope = db_client.get(
        SCOPE_URL.format(bank_id=bank_id),
        headers=headers(),
        params={
            "entity": "position",
            "source_system": SECOND_SOURCE,
            "as_of": FIXTURE_AS_OF.isoformat(),
            "position_type": "LOAN",
        },
    )
    assert scope.status_code == 200, scope.text
    assert scope.json()["rows_in_scope"] == 3

    requested = db_client.post(
        WITHDRAWALS_URL.format(bank_id=bank_id),
        headers=headers(user_id=USER_1, roles=("analyst",)),
        json={
            "entity": "position",
            "source_system": SECOND_SOURCE,
            "as_of_date": FIXTURE_AS_OF.isoformat(),
            "reason": "Duplicate of the declared LOAN book of record.",
            "requested_by": "analyst@bank.test",
            "position_type": "LOAN",
            "declaration_id": declaration_id,
        },
    )
    assert requested.status_code == 201, requested.text
    withdrawal_id = requested.json()["id"]
    assert requested.json()["status"] == "pending"
    assert requested.json()["rows_withdrawn"] == 0

    blank_reason = db_client.post(
        WITHDRAWALS_URL.format(bank_id=bank_id),
        headers=headers(user_id=USER_1, roles=("analyst",)),
        json={
            "entity": "position",
            "source_system": SECOND_SOURCE,
            "as_of_date": FIXTURE_AS_OF.isoformat(),
            "reason": "",
            "requested_by": "analyst@bank.test",
        },
    )
    assert blank_reason.status_code == 422, blank_reason.text

    analyst_approve = db_client.post(
        f"{WITHDRAWALS_URL.format(bank_id=bank_id)}/{withdrawal_id}/approve",
        headers=headers(user_id=APPROVER_USER, roles=("analyst",)),
        json={"approved_by": "cro@bank.test"},
    )
    assert analyst_approve.status_code == 403, analyst_approve.text

    applied = db_client.post(
        f"{WITHDRAWALS_URL.format(bank_id=bank_id)}/{withdrawal_id}/approve",
        headers=headers(user_id=APPROVER_USER, roles=("approver",)),
        json={"approved_by": "cro@bank.test"},
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["status"] == "applied"
    assert applied.json()["rows_withdrawn"] == 3

    # The book is gone from the assessment, and nothing was deleted.
    clean = db_client.get(
        ASSESSMENT_URL.format(bank_id=bank_id),
        headers=headers(),
        params={"as_of": FIXTURE_AS_OF.isoformat()},
    ).json()
    assert clean["clean"] is True

    session = get_sessionmaker()()
    try:
        rows = session.scalars(
            select(CanonicalPositionSnapshot).where(
                CanonicalPositionSnapshot.organization_id == ORG_1,
                CanonicalPositionSnapshot.withdrawn_at.is_not(None),
            )
        ).all()
        assert len(rows) == 3
        assert all(row.superseded_by is None for row in rows)
    finally:
        session.close()

    listed = db_client.get(WITHDRAWALS_URL.format(bank_id=bank_id), headers=headers())
    assert listed.status_code == 200, listed.text
    assert [row["status"] for row in listed.json()["withdrawals"]] == ["applied"]

    reversed_response = db_client.post(
        f"{WITHDRAWALS_URL.format(bank_id=bank_id)}/{withdrawal_id}/reverse",
        headers=headers(user_id=APPROVER_USER, roles=("approver",)),
        json={
            "reversed_by": "cro@bank.test",
            "reason": "The sign-off named the wrong system.",
        },
    )
    assert reversed_response.status_code == 200, reversed_response.text
    assert reversed_response.json()["status"] == "reversed"
    assert reversed_response.json()["rows_restored"] == 3


#: The live plane's business date — deliberately LATER than the withdrawn book's
#: date, so the test can tell "refresh the live date" from "refresh the
#: withdrawn date" (the latter would roll the live plane back a month).
LIVE_AS_OF = date(2026, 7, 31)


def _stand_in_for_the_live_plane(bank_id: str) -> None:
    session = get_sessionmaker()()
    try:
        session.add(
            CurrentFinancialFact(
                organization_id=ORG_1,
                bank_id=bank_id,
                source_as_of_date=LIVE_AS_OF,
                source_generation=1,
                fact_group="balance_sheet",
                category="cash_vault",
                amount=Decimal("1"),
                currency="GHS",
            )
        )
        session.commit()
    finally:
        session.close()


def _queued_refreshes(bank_id: str, *, job_type: str = "pipeline_refresh") -> list[Job]:
    session = get_sessionmaker()()
    try:
        return list(
            session.scalars(
                select(Job)
                .where(
                    Job.organization_id == ORG_1,
                    Job.bank_id == bank_id,
                    Job.job_type == job_type,
                    Job.status == "queued",
                )
                .order_by(Job.queued_at)
            )
        )
    finally:
        session.close()


def test_approving_and_reversing_a_withdrawal_refresh_the_live_plane_on_its_own_date(
    db_client: TestClient,
) -> None:
    """A withdrawal changes the book the live plane was derived from, so both
    governed acts enqueue one coalesced ``pipeline_refresh`` — keyed on the
    bank's LIVE date, never on the withdrawal's own ``as_of_date``."""
    bank_id = _seed(db_client, duplicate=True)
    _stand_in_for_the_live_plane(bank_id)
    assert _queued_refreshes(bank_id) == []

    requested = db_client.post(
        WITHDRAWALS_URL.format(bank_id=bank_id),
        headers=headers(user_id=USER_1, roles=("analyst",)),
        json={
            "entity": "position",
            "source_system": SECOND_SOURCE,
            "as_of_date": FIXTURE_AS_OF.isoformat(),
            "reason": "Duplicate of the LOAN book of record.",
            "requested_by": "analyst@bank.test",
            "position_type": "LOAN",
        },
    )
    assert requested.status_code == 201, requested.text
    withdrawal_id = requested.json()["id"]
    # Requesting stamps nothing and so refreshes nothing.
    assert _queued_refreshes(bank_id) == []

    applied = db_client.post(
        f"{WITHDRAWALS_URL.format(bank_id=bank_id)}/{withdrawal_id}/approve",
        headers=headers(user_id=APPROVER_USER, roles=("approver",)),
        json={"approved_by": "cro@bank.test"},
    )
    assert applied.status_code == 200, applied.text
    (job,) = _queued_refreshes(bank_id)
    assert job.coalesce_key == f"refresh:{bank_id}:{LIVE_AS_OF.isoformat()}"
    assert job.payload["as_of_date"] == LIVE_AS_OF.isoformat()
    assert job.payload["as_of_date"] != FIXTURE_AS_OF.isoformat()
    assert job.payload["reason"] == "withdrawal_applied"

    reversed_response = db_client.post(
        f"{WITHDRAWALS_URL.format(bank_id=bank_id)}/{withdrawal_id}/reverse",
        headers=headers(user_id=APPROVER_USER, roles=("approver",)),
        json={"reversed_by": "cro@bank.test", "reason": "Wrong system named."},
    )
    assert reversed_response.status_code == 200, reversed_response.text
    # Still one un-started job: the reversal coalesced into it on the same key.
    (job,) = _queued_refreshes(bank_id)
    assert job.coalesce_key == f"refresh:{bank_id}:{LIVE_AS_OF.isoformat()}"
    assert job.payload["as_of_date"] == LIVE_AS_OF.isoformat()
    assert job.payload["reason"] == "withdrawal_reversed"
    # BI_MART_ENQUEUE_ENABLED is pinned off: no mart build was asked for.
    assert _queued_refreshes(bank_id, job_type="bi_mart_refresh") == []


def test_withdrawal_acts_ask_the_bi_lane_for_the_withdrawn_date_and_the_live_date(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mart is per-date slices, so D-025's rollback hazard does not apply
    to it: the WITHDRAWN date's slice is what changed and is rebuilt on its
    own date, while the live-plane trigger separately asks for the LIVE
    date's slice (its engine metrics move). Two dates, two coalesced jobs;
    the reversal merges into the same two."""
    monkeypatch.setenv("BI_MART_ENQUEUE_ENABLED", "1")
    get_settings.cache_clear()
    try:
        bank_id = _seed(db_client, duplicate=True)
        _stand_in_for_the_live_plane(bank_id)
        assert _queued_refreshes(bank_id, job_type="bi_mart_refresh") == []

        requested = db_client.post(
            WITHDRAWALS_URL.format(bank_id=bank_id),
            headers=headers(user_id=USER_1, roles=("analyst",)),
            json={
                "entity": "position",
                "source_system": SECOND_SOURCE,
                "as_of_date": FIXTURE_AS_OF.isoformat(),
                "reason": "Duplicate of the LOAN book of record.",
                "requested_by": "analyst@bank.test",
                "position_type": "LOAN",
            },
        )
        assert requested.status_code == 201, requested.text
        withdrawal_id = requested.json()["id"]
        assert _queued_refreshes(bank_id, job_type="bi_mart_refresh") == []

        applied = db_client.post(
            f"{WITHDRAWALS_URL.format(bank_id=bank_id)}/{withdrawal_id}/approve",
            headers=headers(user_id=APPROVER_USER, roles=("approver",)),
            json={"approved_by": "cro@bank.test"},
        )
        assert applied.status_code == 200, applied.text
        by_key = {
            job.coalesce_key: job for job in _queued_refreshes(bank_id, job_type="bi_mart_refresh")
        }
        assert set(by_key) == {
            f"bi:{bank_id}:{LIVE_AS_OF.isoformat()}",
            f"bi:{bank_id}:{FIXTURE_AS_OF.isoformat()}",
        }
        for job in by_key.values():
            assert job.payload["reason"] == "withdrawal_applied"
            assert job.payload["bank_id"] == bank_id
            assert "builder_version" in job.payload

        reversed_response = db_client.post(
            f"{WITHDRAWALS_URL.format(bank_id=bank_id)}/{withdrawal_id}/reverse",
            headers=headers(user_id=APPROVER_USER, roles=("approver",)),
            json={"reversed_by": "cro@bank.test", "reason": "Wrong system named."},
        )
        assert reversed_response.status_code == 200, reversed_response.text
        after = {
            job.coalesce_key: job for job in _queued_refreshes(bank_id, job_type="bi_mart_refresh")
        }
        assert set(after) == set(by_key)  # still two: the reversal coalesced
        for key, job in after.items():
            assert job.id == by_key[key].id
            assert job.payload["reason"] == "withdrawal_reversed"
    finally:
        get_settings.cache_clear()


def test_a_withdrawal_scope_that_matches_nothing_is_refused(db_client: TestClient) -> None:
    bank_id = _seed(db_client, duplicate=False)
    response = db_client.post(
        WITHDRAWALS_URL.format(bank_id=bank_id),
        headers=headers(user_id=USER_1, roles=("analyst",)),
        json={
            "entity": "position",
            "source_system": SECOND_SOURCE,
            "as_of_date": date(2026, 6, 30).isoformat(),
            "reason": "Retire a book that is not there.",
            "requested_by": "analyst@bank.test",
        },
    )
    assert response.status_code == 422, response.text
    assert "nothing to withdraw" in _message(response)
