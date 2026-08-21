"""Modules open on a graceful empty state, not a 409 (founder call 2026-08-20).

"No computed data yet" is a valid state, not a conflict: the live-view dashboards
return HTTP 200 ``{available: false, reason}`` so a module page opens with a clean
onboarding panel instead of a red error + a console 4xx. A bank with no ingested
facts exercises the path.
"""

from __future__ import annotations

from app.db.session import get_sessionmaker
from app.models import Bank
from tests.api.helpers import ORG_1, headers
from tests.fixtures.canonical_bank_fixture import materialize_canonical_test_book


def _fresh_bank() -> str:
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)  # init app engine + reference rows
        bank = Bank(
            organization_id=ORG_1,
            name="Empty State Bank",
            short_name="ESB",
            currency="GHS",
            jurisdiction_code="GH",
            license_type="universal",
            institution_type="universal_bank",
        )
        session.add(bank)
        session.commit()
        return bank.id
    finally:
        session.close()


def test_dashboards_return_200_unavailable_when_no_data(db_client) -> None:  # noqa: ANN001
    bank_id = _fresh_bank()
    for module in ("capital", "liquidity", "irr", "fx", "ftp"):
        resp = db_client.get(
            f"/api/v1/banks/{bank_id}/{module}/dashboard", headers=headers()
        )
        assert resp.status_code == 200, f"{module}: {resp.status_code} {resp.text}"
        body = resp.json()
        assert body["available"] is False, f"{module}: {body}"
        assert body["error_code"] == "current_facts_missing"
        assert body["reason"]
