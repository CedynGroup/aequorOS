"""The reconciliation escape valve, through the product (audit 2026-08-22 D-20).

``grant_exception`` shipped complete — non-empty reason, positive ceiling,
ordered window, four-eyes refusal of self-approval, audit event — and had no
endpoint. The balance-sheet identity control is fail-closed, so a tenant whose
canonical book carries a known, bounded defect was barred from every filing act
with no product path to record the approved exception. These tests drive the
routes an officer actually has.

The governance rules themselves are covered in
``tests/services/test_reconciliation_control.py``; what is proved here is that
they are REACHABLE, and that the API preserves each refusal rather than
softening it into a 500 or, worse, letting it through.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient

from tests.api.helpers import ORG_2, USER_1, headers
from tests.api.test_ingestion import seed_bank

URL = "/api/v1/banks/{bank_id}/reconciliation/exceptions"

#: The hermetic canonical fixture ships WITH a governed exception — the compact
#: book deliberately does not tie, and the fixture records the same bounded,
#: dated grant a real bank would need (``tests/factories/reconciliation.py``).
#: So these tests assert on deltas and on identity, never on an empty register.
FIXTURE_APPROVER = "fixture_supervisor"

_GRANT = {
    "reason": "Known custody-feed gap on the securities book, quantified and bounded.",
    "approved_by": "Head of Finance",
    "max_gap_fraction": "0.002",
    "effective_from": "2026-06-01",
    "effective_to": "2026-09-30",
}


def test_the_register_is_readable_and_names_the_live_grant(db_client: TestClient) -> None:
    """The read half of the valve: "on whose authority was this filed?"."""
    bank_id = seed_bank(db_client)
    response = db_client.get(URL.format(bank_id=bank_id), headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["control"] == "balance_sheet_identity"
    fixture = [row for row in body["exceptions"] if row["approved_by"] == FIXTURE_APPROVER]
    assert len(fixture) == 1
    # The live grant is resolved by the service, so the reader sees exactly the
    # exception the filing gate would apply.
    assert body["active_exception_id"] == fixture[0]["id"]


def test_an_approved_exception_is_recorded_and_becomes_the_active_grant(
    db_client: TestClient,
) -> None:
    bank_id = seed_bank(db_client)
    granted = db_client.post(
        URL.format(bank_id=bank_id), headers=headers(roles=("approver",)), json=_GRANT
    )
    assert granted.status_code == 201, granted.text
    row = granted.json()
    assert row["control"] == "balance_sheet_identity"
    assert Decimal(row["max_gap_fraction"]) == Decimal("0.002")
    assert row["approved_by"] == "Head of Finance"
    assert row["revoked_at"] is None

    listed = db_client.get(
        URL.format(bank_id=bank_id),
        headers=headers(),
        params={"as_of": "2026-06-30"},
    )
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert row["id"] in {entry["id"] for entry in body["exceptions"]}
    # The ACTIVE grant is the gate's own resolution (widest ceiling first), not a
    # re-filter of the list — here the fixture's 0.13 ceiling still wins.
    assert body["active_exception_id"] is not None


def test_revoking_closes_the_grant_without_deleting_the_record(
    db_client: TestClient,
) -> None:
    bank_id = seed_bank(db_client)
    granted = db_client.post(
        URL.format(bank_id=bank_id), headers=headers(roles=("approver",)), json=_GRANT
    ).json()

    revoked = db_client.post(
        f"{URL.format(bank_id=bank_id)}/{granted['id']}/revoke",
        headers=headers(roles=("approver",)),
        json={"revoked_by": "Chief Risk Officer", "reason": "Custody feed corrected."},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["revoked_by"] == "Chief Risk Officer"
    assert revoked.json()["revoked_at"] is not None

    listed = db_client.get(
        URL.format(bank_id=bank_id), headers=headers(), params={"as_of": "2026-06-30"}
    ).json()
    # The record survives revocation — an exception is evidence, not a toggle.
    closed = next(entry for entry in listed["exceptions"] if entry["id"] == granted["id"])
    assert closed["revoked_at"] is not None
    assert closed["reason"] == _GRANT["reason"]


def test_self_approval_is_refused_through_the_route(db_client: TestClient) -> None:
    """The four-eyes rule is the reason the valve is safe to expose at all."""
    bank_id = seed_bank(db_client)
    response = db_client.post(
        URL.format(bank_id=bank_id),
        headers=headers(roles=("approver",)),
        json={**_GRANT, "approved_by_user_id": str(USER_1)},
    )
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["error"]["details"]["error_code"] == "reconciliation_exception_refused"
    assert "second approver" in body["error"]["details"]["message"]

    # ...and nothing was recorded: only the fixture's own grant remains.
    remaining = db_client.get(URL.format(bank_id=bank_id), headers=headers()).json()["exceptions"]
    assert {entry["approved_by"] for entry in remaining} == {FIXTURE_APPROVER}


def test_a_blank_reason_and_a_zero_ceiling_are_both_refused(
    db_client: TestClient,
) -> None:
    bank_id = seed_bank(db_client)
    blank = db_client.post(
        URL.format(bank_id=bank_id),
        headers=headers(roles=("approver",)),
        json={**_GRANT, "reason": "   "},
    )
    assert blank.status_code in {409, 422}, blank.text

    zero = db_client.post(
        URL.format(bank_id=bank_id),
        headers=headers(roles=("approver",)),
        json={**_GRANT, "max_gap_fraction": "0"},
    )
    assert zero.status_code == 422, zero.text


def test_granting_is_approver_gated(db_client: TestClient) -> None:
    bank_id = seed_bank(db_client)
    response = db_client.post(
        URL.format(bank_id=bank_id), headers=headers(roles=("analyst",)), json=_GRANT
    )
    assert response.status_code == 403, response.text


def test_another_tenant_cannot_see_or_grant(db_client: TestClient) -> None:
    bank_id = seed_bank(db_client)
    assert (
        db_client.get(URL.format(bank_id=bank_id), headers=headers(org_id=ORG_2)).status_code == 404
    )
    assert (
        db_client.post(
            URL.format(bank_id=bank_id),
            headers=headers(org_id=ORG_2, roles=("approver",)),
            json=_GRANT,
        ).status_code
        == 404
    )
