"""Egress guard on the Temenos connector (audit finding P0-6).

``endpoint`` is a destination an ``analyst`` chooses. Two layers are pinned
here: the payload screen (a clean 422 before anything is stored) and the
authoritative pre-connect check on the live ``/test`` path, which resolves the
stored endpoint and refuses a name whose DNS answer is internal.

Hermetic: DNS is the stubbed :func:`app.core.outbound.resolve_host` seam and the
T24 sign-on is the offline simulated provider, so nothing here touches a
network.

This module's other-suite cases live in ``test_temenos_connections_outbound.py``.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import ModuleScope, RoleBundle, SensitivityScope
from app.core.config import get_settings
from app.core.outbound import OutboundTargetBlocked
from app.data_engine.adapters.temenos_t24.credential_vault import TemenosCredentialVault
from app.data_engine.models.temenos import TemenosConnection
from app.data_engine.service import temenos_connections
from app.models import Bank
from tests.data_engine.service.test_temenos_connections_outbound import CREDS
from tests.support.factories.authorization import grant_institution_authority
from tests.support.factories.outbound import stub_dns, stub_public_dns
from tests.support.helpers import ORG_1, USER_1

MASTER_KEY = "temenos-outbound-test-key"


@pytest.fixture
def ctx() -> TenantContext:
    return TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1)


def _bank(db: Session) -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name="T24 Outbound Bank",
        short_name="t24-outbound",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="universal_bank",
    )
    db.add(bank)
    db.flush()
    grant_institution_authority(
        db,
        organization_id=ORG_1,
        bank_id=bank.id,
        user_id=USER_1,
        bundle=RoleBundle.ANALYST,
        module=ModuleScope.DATA,
        sensitivity=SensitivityScope.RESTRICTED,
    )
    return bank


def _connection(
    db: Session, bank: Bank, *, endpoint: str = "ofs://sample-bank"
) -> TemenosConnection:
    connection = TemenosConnection(
        organization_id=ORG_1,
        bank_id=bank.id,
        connection_mode="OFS",
        display_name="Core OFS",
        endpoint=endpoint,
        default_currency="GHS",
        status="ACTIVE",
        vault_path="",
        companies=["GH0010001"],
        domains=[],
        schedule={},
        catalog_overrides={},
        created_by=USER_1,
    )
    db.add(connection)
    db.flush()
    return connection


def _store_creds(db: Session, connection: TemenosConnection, mp: pytest.MonkeyPatch) -> None:
    mp.setenv("CREDENTIAL_VAULT_MASTER_KEY", MASTER_KEY)
    get_settings.cache_clear()
    TemenosCredentialVault(db, master_key=MASTER_KEY).store(connection, credentials=CREDS)


def test_guard_endpoint_refuses_an_endpoint_that_resolves_internally(
    db_session: Session, ctx: TenantContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = _bank(db_session)
    connection = _connection(db_session, bank)
    _store_creds(db_session, connection, monkeypatch)
    stub_dns(monkeypatch, {"sample-bank": ("169.254.169.254",)})

    with pytest.raises(OutboundTargetBlocked) as blocked:
        temenos_connections.guard_endpoint(connection.endpoint)
    assert "not a permitted destination" in blocked.value.message
    assert "169.254.169.254" not in blocked.value.message


def test_test_connection_reports_that_live_transport_is_unavailable(
    db_session: Session, ctx: TenantContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = _bank(db_session)
    connection = _connection(db_session, bank)
    _store_creds(db_session, connection, monkeypatch)
    stub_public_dns(monkeypatch, "sample-bank")

    result = temenos_connections.test_connection(db_session, ctx, bank.id, connection.id)
    assert result.success is False
    assert result.sample_values == {}
    assert result.error is not None
    assert "not available in this deployment" in result.error


def test_guard_endpoint_fails_closed_on_an_unresolvable_endpoint(
    db_session: Session, ctx: TenantContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NXDOMAIN now is no promise about the answer the transport will get."""
    bank = _bank(db_session)
    connection = _connection(db_session, bank)
    _store_creds(db_session, connection, monkeypatch)
    stub_dns(monkeypatch, {})

    with pytest.raises(OutboundTargetBlocked):
        temenos_connections.guard_endpoint(connection.endpoint)
