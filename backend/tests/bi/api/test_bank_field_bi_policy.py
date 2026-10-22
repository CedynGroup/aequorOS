"""Bank-key refusals must render as capability decisions, preserving numeric BI."""

from decimal import Decimal
from typing import TypedDict, cast

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sqlalchemy.orm import Session

from app.bi.schemas.query import BiCatalogueRead, BiQueryResult
from app.core.authorization import ModuleScope, SensitivityScope
from app.core.key_management import registry
from app.core.key_management.backfill import backfill_bank
from app.core.key_management.local import LocalKeyProvider
from app.core.key_management.types import KeyReference
from app.identity.public import Bank
from tests.bi.api import test_bi_routes as routes
from tests.support.helpers import ORG_1, headers

mart = routes.mart
bi_on = routes.bi_on


class _Details(TypedDict):
    error_code: str
    message: str


class _Error(TypedDict):
    details: _Details


class _Failure(TypedDict):
    error: _Error


def test_connected_bank_refusal_explains_capability_and_keeps_numeric_concentration(
    db_client: TestClient,
    db_session: Session,
    mart: Bank,
    bi_on: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authv = routes.grant_only(db_session, (routes.Grant(ModuleScope.ALL, SensitivityScope.ALL),))
    request_headers = headers(authorization_version=authv)
    query = {
        "measures": ["loans.largest_single_name_share_pct"],
        "time": {"as_of": routes.AS_OF.isoformat()},
    }
    before = db_client.post(routes.BASE + "/query", json=query, headers=request_headers)
    assert before.status_code == 200, before.text
    expected = BiQueryResult.model_validate(cast(object, before.json()))
    assert TypeAdapter(Decimal).validate_python(expected.rows[0][0]) == 50
    old_catalogue = db_client.get(routes.BASE + "/catalogue", headers=request_headers)
    assert old_catalogue.status_code == 200
    provider = LocalKeyProvider()
    key = KeyReference("aws_kms", "synthetic-bi-route", "us-east-1", "111122223333")
    provider.add_key(key)

    def providers(_name: str) -> LocalKeyProvider:
        return provider

    monkeypatch.setattr(registry, "provider_for", providers)
    registry.register(
        db_session,
        organization_id=ORG_1,
        bank_id=mart.id,
        storage_slug="synthetic-bi-route",
        key=key,
        providers=providers,
    )
    db_session.commit()
    backfill_bank(db_session, organization_id=ORG_1, bank_id=mart.id)
    numeric = db_client.post(routes.BASE + "/query", json=query, headers=request_headers)
    assert numeric.status_code == 200, numeric.text
    assert BiQueryResult.model_validate(cast(object, numeric.json())).rows == expected.rows
    refused = db_client.post(
        routes.BASE + "/query", json=routes.OBLIGOR_QUERY, headers=request_headers
    )
    assert refused.status_code == 403, refused.text
    details = TypeAdapter(_Failure).validate_python(cast(object, refused.json()))["error"][
        "details"
    ]
    # This distinct code makes the dashboard render the server's own sentence,
    # instead of paraphrasing it as a grant an Org Owner can issue.
    assert details["error_code"] == "bank_key_identity_bi_unavailable"
    assert "bank-held encryption" in details["message"]
    assert "Numeric BI remains available" in details["message"]
    assert "Ada Traders" not in refused.text
    refreshed = db_client.get(
        routes.BASE + "/catalogue",
        headers={**request_headers, "If-None-Match": old_catalogue.headers["etag"]},
    )
    assert refreshed.status_code == 200, refreshed.text
    catalogue = BiCatalogueRead.model_validate(cast(object, refreshed.json()))
    assert "counterparty.name" not in {dimension.id for dimension in catalogue.dimensions}
    assert "counterparty" not in {hierarchy.id for hierarchy in catalogue.hierarchies}
    assert "loans.largest_single_name_share_pct" in {measure.id for measure in catalogue.measures}
