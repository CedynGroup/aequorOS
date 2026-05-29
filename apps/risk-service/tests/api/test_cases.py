from __future__ import annotations

from fastapi.testclient import TestClient

from tests.api.factories import CaseFactory
from tests.api.helpers import ORG_2, headers


def test_cases_are_org_scoped_and_archivable(db_client: TestClient) -> None:
    cases = CaseFactory(db_client)
    case_id = str(cases.create()["id"])
    other_case_id = str(cases.create(org_id=ORG_2)["id"])

    response = db_client.get("/api/v1/cases", headers=headers())
    assert response.status_code == 200
    assert [case["id"] for case in response.json()] == [case_id]

    response = db_client.get(f"/api/v1/cases/{other_case_id}", headers=headers())
    assert response.status_code == 404

    response = db_client.post(f"/api/v1/cases/{case_id}/archive", headers=headers())
    assert response.status_code == 200
    assert response.json()["status"] == "archived"
