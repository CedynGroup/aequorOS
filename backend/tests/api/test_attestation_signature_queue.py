"""The signature queue is the caller's own, and reading it is not a write.

``GET /attestation/awaiting-my-signature`` lists the returns routed to the
signed-in officer and still unsigned. It sat behind the scalar write ladder
(``analyst`` or higher), so an officer whose authority is a binding rather than
a scalar role — the Validator, a Board member — opened the Signatures tab and
was told the page needs the analyst role, instead of being told nothing is
waiting on them. The query is filtered to the caller's own recipient rows, so
every authenticated officer may read it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.api.helpers import headers

QUEUE_URL = "/api/v1/attestation/awaiting-my-signature"


@pytest.mark.parametrize("role", ("viewer", "examiner", "analyst", "approver", "admin"))
def test_every_officer_reads_their_own_signature_queue(db_client: TestClient, role: str) -> None:
    response = db_client.get(QUEUE_URL, headers=headers(roles=(role,)))
    assert response.status_code == 200, response.text
    assert response.json() == {"items": []}
