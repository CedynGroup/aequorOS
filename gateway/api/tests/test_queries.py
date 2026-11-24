# pyright: standard
"""Queries: supervisor and institution sides, status transitions and delivery."""

from __future__ import annotations

from typing import Any

from tests.harness import BSD2_LINES, Harness


def _filed(harness: Harness) -> tuple[Any, str]:
    harness.bootstrap(("BSD2",))
    bank = harness.enrol(supervisor="aboateng")
    receipt_id = harness.post_filing(bank, harness.filing(bank, BSD2_LINES)).json()["receipt"][
        "receipt_id"
    ]
    return bank, receipt_id


def test_query_round_trip(any_harness: Harness) -> None:
    harness = any_harness
    bank, receipt_id = _filed(harness)
    akosua = harness.staff("aboateng")
    opened = harness.client.post(
        "/portal/api/queries",
        headers=akosua,
        json={
            "receipt_id": receipt_id,
            "subject": "Basis for upgraded facilities",
            "body": "Please explain.",
            "kind": "query",
            "due_at": "2026-11-24T17:00:00Z",
        },
    )
    assert opened.status_code == 201, opened.text
    number = opened.json()["number"]
    assert number.startswith("QRY-2026-")
    detail = harness.client.get(f"/portal/api/submissions/{receipt_id}", headers=akosua).json()
    assert detail["row"]["status"] == "queried"
    bank_headers = harness.bank_headers(bank)
    listed = harness.client.get("/v1/queries", headers=bank_headers).json()["items"]
    assert [q["number"] for q in listed] == [number]
    assert listed[0]["status"] == "awaiting_institution"
    assert listed[0]["messages"][0]["author"] == "supervisor"
    assert (
        harness.client.post(
            f"/v1/queries/{number}/acknowledgements", headers=bank_headers
        ).status_code
        == 204
    )
    reply = harness.client.post(
        f"/v1/queries/{number}/responses",
        headers=bank_headers,
        json={"body": "Schedules attached.", "resubmission_receipt_id": None},
    )
    assert reply.status_code == 201
    assert reply.json()["author"] == "institution"
    portal = harness.client.get(f"/portal/api/queries/{number}", headers=akosua).json()
    assert portal["status"] == "awaiting_supervisor"
    assert portal["delivered_at"] is not None
    assert portal["acknowledged_at"] is not None
    assert [m["author"] for m in portal["messages"]] == ["supervisor", "institution"]
    assert portal["messages"][0]["author_role"] == "Supervisor"
    assert (
        harness.client.get(f"/portal/api/submissions/{receipt_id}", headers=akosua).json()["row"][
            "status"
        ]
        == "in_review"
    )
    follow = harness.client.post(
        f"/portal/api/queries/{number}/messages",
        headers=akosua,
        json={
            "body": "Please refile.",
            "require_resubmission": True,
            "due_at": "2026-11-26T17:00:00Z",
        },
    )
    assert follow.status_code == 201
    after = harness.client.get(f"/portal/api/queries/{number}", headers=akosua).json()
    assert after["kind"] == "resubmission"
    assert after["status"] == "awaiting_institution"
    assert (
        harness.client.post(f"/portal/api/queries/{number}/close", headers=akosua).status_code
        == 204
    )
    closed = harness.client.get("/portal/api/queries?status=closed", headers=akosua).json()
    assert closed["counts"]["closed"] == 1
    assert closed["items"][0]["status"] == "closed"
    late = harness.client.post(
        f"/v1/queries/{number}/responses",
        headers=bank_headers,
        json={"body": "x", "resubmission_receipt_id": None},
    )
    assert late.status_code == 409
    assert harness.client.get("/v1/queries?status=open", headers=bank_headers).json()["items"] == []
    assert (
        len(harness.client.get("/v1/queries?status=all", headers=bank_headers).json()["items"]) == 1
    )


def test_overdue_and_counts(harness: Harness) -> None:
    _, receipt_id = _filed(harness)
    kofi = harness.staff("kowusu")
    harness.client.post(
        "/portal/api/queries",
        headers=kofi,
        json={
            "receipt_id": receipt_id,
            "subject": "FX swaps",
            "body": "Explain.",
            "kind": "query",
            "due_at": "2026-11-19T17:00:00Z",
        },
    )
    listed = harness.client.get("/portal/api/queries", headers=kofi).json()
    assert listed["items"][0]["status"] == "overdue"
    assert listed["counts"] == {
        "open": 1,
        "awaiting_institution": 1,
        "awaiting_supervisor": 0,
        "closed": 0,
    }


def test_resubmission_request_and_corrected_filing(harness: Harness) -> None:
    bank, receipt_id = _filed(harness)
    kofi = harness.staff("kowusu")
    created = harness.client.post(
        f"/portal/api/submissions/{receipt_id}/resubmission-request",
        headers=kofi,
        json={"reason": "Correct line 1.3.", "due_at": "2026-11-25T17:00:00Z"},
    )
    assert created.status_code == 201
    number = created.json()["number"]
    amended = harness.post_filing(
        bank,
        harness.filing(bank, BSD2_LINES, supersedes=receipt_id, reason="Corrected 1.3"),
        amend=receipt_id,
    ).json()["receipt"]["receipt_id"]
    headers = harness.bank_headers(bank)
    wrong = harness.client.post(
        f"/v1/queries/{number}/responses",
        headers=headers,
        json={"body": "Corrected.", "resubmission_receipt_id": "SRG-26-0000999"},
    )
    assert wrong.status_code == 400
    ok = harness.client.post(
        f"/v1/queries/{number}/responses",
        headers=headers,
        json={"body": "Corrected.", "resubmission_receipt_id": amended},
    )
    assert ok.status_code == 201


def test_institutions_cannot_see_each_others_queries(harness: Harness) -> None:
    _, receipt_id = _filed(harness)
    other = harness.enrol("0117", "Palmgrove Bank")
    number = harness.client.post(
        "/portal/api/queries",
        headers=harness.staff("kowusu"),
        json={
            "receipt_id": receipt_id,
            "subject": "s",
            "body": "b",
            "kind": "query",
            "due_at": "2026-11-30T17:00:00Z",
        },
    ).json()["number"]
    headers = harness.bank_headers(other)
    assert harness.client.get("/v1/queries?status=all", headers=headers).json()["items"] == []
    assert (
        harness.client.post(f"/v1/queries/{number}/acknowledgements", headers=headers).status_code
        == 404
    )
