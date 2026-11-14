"""REG configure cutover: channel-config and reporting-settings need exact grants.

``PUT /banks/{bank_id}/regulatory-reporting/channel-configs/{channel}`` and
``PUT /banks/{bank_id}/regulatory-reporting/settings`` are configuration duties:
they require a complete active ``reg/configure/restricted`` exact-bank binding,
denied BEFORE any credential encryption, row mutation or audit row (VAL-CFG-007).
The matching GETs read the same restricted-surface configuration, so they demand
the same binding at ``view`` — coverage first (a principal with no effective
authority for the bank in any module gets the opaque 404), then the scoped
denial, and the channel's "not configured" 404 only once authorization has
passed. Both writes return write-only acknowledgements (D27/D34): a
configure-only caller never reads stored content — the credential fingerprint,
``has_credentials`` or the stored row timestamps — through a write response, and
configure alone confers no read.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.identity.public import DEFAULT_DENIAL_DETAIL
from app.identity.service import authorization
from app.models import (
    AuditEvent,
    Bank,
    RegulatoryChannelConfig,
    RegulatoryReportingSettings,
)
from app.reporting.schemas.packages import (
    ChannelConfigWriteAcknowledgement,
    ReportingSettingsWriteAcknowledgement,
)
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book
from tests.support.authorization_fixtures import (
    current_authorization_version,
    open_tenant_session,
    suspend_all_bindings,
)
from tests.support.helpers import ORG_1, USER_1, error_envelope, headers

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}"
SIBLING = "BK-REGSIB1"
CHANNEL_URL = f"{BASE}/regulatory-reporting/channel-configs/orass_sandbox"
SETTINGS_URL = f"{BASE}/regulatory-reporting/settings"

CHANNEL_ACK_FIELDS = {"bank_id", "channel", "credentials_rotated", "created"}
SETTINGS_ACK_FIELDS = {"bank_id", "deadline_overrides", "created"}

REG_DENIAL = "This action requires an active Regulatory Reporting binding."
MASTER_KEY = "reg-configure-test-master-key"


@pytest.fixture(autouse=True)
def book_without_authority(db_client: TestClient) -> None:
    _ = db_client
    with get_sessionmaker()() as db:
        materialize_canonical_test_book(db)
        db.add(
            Bank(
                id=SIBLING,
                organization_id=ORG_1,
                name="Reg configure sibling fixture",
                short_name="RegSibling",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal",
                institution_type="universal_bank",
            )
        )
        db.commit()


@pytest.fixture(autouse=True)
def vault_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("CREDENTIAL_VAULT_MASTER_KEY", MASTER_KEY)
    get_settings.cache_clear()
    yield
    monkeypatch.undo()
    get_settings.cache_clear()


def _grant(
    bundle: RoleBundle,
    *,
    module: ModuleScope = ModuleScope.REGULATORY,
    sensitivity: SensitivityScope = SensitivityScope.RESTRICTED,
    bank_id: str = SAMPLE_BANK_ID,
) -> None:
    with get_sessionmaker()() as db:
        authorization.create_role_binding(
            db,
            organization_id=ORG_1,
            principal_user_id=USER_1,
            principal_type=PrincipalType.HUMAN,
            role_bundle=bundle,
            scope=authorization.BindingScope(
                InstitutionScope.INSTITUTION, bank_id, module, sensitivity
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "reg-configure-test"),
            reason="REG configure-cutover regression authority",
        )
        db.commit()


def _current_version() -> int:
    with open_tenant_session(ORG_1) as db:
        return current_authorization_version(db, organization_id=ORG_1, user_id=USER_1)


def _auth() -> dict[str, str]:
    # Scalar roles ("admin", "approver") must grant nothing by themselves:
    # only the explicit scoped binding may authorize these routes.
    return headers(roles=("admin", "approver"), authorization_version=_current_version())


def _counts() -> tuple[int, int, int]:
    with get_sessionmaker()() as db:
        return (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.organization_id == ORG_1)
            )
            or 0,
            db.scalar(
                select(func.count())
                .select_from(RegulatoryChannelConfig)
                .where(RegulatoryChannelConfig.organization_id == ORG_1)
            )
            or 0,
            db.scalar(
                select(func.count())
                .select_from(RegulatoryReportingSettings)
                .where(RegulatoryReportingSettings.organization_id == ORG_1)
            )
            or 0,
        )


def test_channel_config_put_denied_without_configure(db_client: TestClient) -> None:
    """Zero grants and a wrong-module analyst binding both stay denied, with no
    config row and no audit row written."""
    _grant(RoleBundle.ANALYST, module=ModuleScope.LIQUIDITY)
    before = _counts()

    zero_grant = db_client.put(
        CHANNEL_URL,
        headers=headers(roles=("admin",), authorization_version=_current_version()),
        json={"config": {"institution_code": "X"}},
    )
    assert zero_grant.status_code == 403, zero_grant.text
    assert error_envelope(zero_grant)["message"] == REG_DENIAL

    wrong_module = db_client.put(CHANNEL_URL, headers=_auth(), json={"config": {"a": 1}})
    assert wrong_module.status_code == 403, wrong_module.text
    assert error_envelope(wrong_module)["message"] == REG_DENIAL
    assert _counts() == before


def test_settings_put_denied_without_configure(db_client: TestClient) -> None:
    _grant(RoleBundle.ANALYST)
    before = _counts()

    denied = db_client.put(SETTINGS_URL, headers=_auth(), json={"deadline_overrides": {"BSD2": 14}})
    assert denied.status_code == 403, denied.text
    assert error_envelope(denied)["message"] == REG_DENIAL
    assert _counts() == before


def test_wrong_bank_configurator_is_denied(db_client: TestClient) -> None:
    """A configurator for the sibling bank has no configure right here (the
    hermetic fixture's org-scope viewer keeps the bank itself visible)."""
    _grant(RoleBundle.CONFIGURATOR, bank_id=SIBLING)
    before = _counts()

    for url, payload in (
        (CHANNEL_URL, {"config": {"institution_code": "X"}}),
        (SETTINGS_URL, {"deadline_overrides": {"BSD2": 14}}),
    ):
        denied = db_client.put(url, headers=_auth(), json=payload)
        assert denied.status_code == 403, (url, denied.text)
        assert error_envelope(denied)["message"] == REG_DENIAL
    assert _counts() == before


def test_principal_without_any_coverage_is_hidden(db_client: TestClient) -> None:
    """With every binding suspended the principal has no coverage at all: the
    bank itself is hidden (opaque 404), the module's DATA/MARKETS split."""
    _suspend_all()
    before = _counts()

    for url, payload in (
        (CHANNEL_URL, {"config": {"institution_code": "X"}}),
        (SETTINGS_URL, {"deadline_overrides": {"BSD2": 14}}),
    ):
        hidden = db_client.put(url, headers=_auth(), json=payload)
        assert hidden.status_code == 404, (url, hidden.text)
        assert error_envelope(hidden)["message"] == "Bank not found."
    assert _counts() == before


def test_cross_tenant_configurator_is_hidden(db_client: TestClient) -> None:
    _grant(RoleBundle.CONFIGURATOR)
    other = headers(org_id="OR-1S000002", roles=("admin",))
    before = _counts()
    attempts: list[tuple[str, dict[str, Any]]] = [
        (CHANNEL_URL, {"config": {}}),
        (SETTINGS_URL, {"deadline_overrides": {}}),
    ]
    for url, payload in attempts:
        hidden = db_client.put(url, headers=other, json=payload)
        assert hidden.status_code == 404, (url, hidden.text)
        assert error_envelope(hidden)["message"] == "Bank not found."
    assert _counts() == before


def test_channel_config_put_succeeds_with_configurator_binding(db_client: TestClient) -> None:
    _grant(RoleBundle.CONFIGURATOR)
    stored = db_client.put(
        CHANNEL_URL,
        headers=_auth(),
        json={"config": {"institution_code": "SBL-001"}, "credentials": {"api_key": "secret"}},
    )
    assert stored.status_code == 200, stored.text
    # Write-only acknowledgement (D27/D34): only what this request wrote.
    assert "credential_fingerprint" not in stored.text
    assert "has_credentials" not in stored.text
    body = ChannelConfigWriteAcknowledgement.model_validate_json(stored.text)
    assert set(body.model_dump()) == CHANNEL_ACK_FIELDS
    assert body.channel == "orass_sandbox"
    assert body.credentials_rotated is True
    assert body.created is True

    # A no-rotation write discloses nothing about the stored credential.
    reconfigured = db_client.put(CHANNEL_URL, headers=_auth(), json={"config": {"a": 1}})
    assert reconfigured.status_code == 200, reconfigured.text
    ack = ChannelConfigWriteAcknowledgement.model_validate_json(reconfigured.text)
    assert set(ack.model_dump()) == CHANNEL_ACK_FIELDS
    assert ack.credentials_rotated is False
    assert ack.created is False

    # The stored read stays on the tenant-gated GET.
    read_back = db_client.get(CHANNEL_URL, headers=_auth())
    assert read_back.status_code == 200, read_back.text
    assert read_back.json()["config"] == {"a": 1}
    assert read_back.json()["has_credentials"] is True


def test_configure_only_authority_writes_and_reads_nothing(db_client: TestClient) -> None:
    """With the seeded viewer coverage suspended and ONLY the exact-bank
    configurator binding active, both configuration PUTs still succeed with
    their write-only acknowledgements — the write path never borrows view
    authority. (The channel-config read-back is a plain tenant read by design:
    it follows bank visibility, not a module view gate.)"""
    _suspend_all()
    _grant(RoleBundle.CONFIGURATOR)

    stored = db_client.put(
        CHANNEL_URL,
        headers=_auth(),
        json={"config": {"institution_code": "SBL-001"}, "credentials": {"api_key": "secret"}},
    )
    assert stored.status_code == 200, stored.text
    assert "credential_fingerprint" not in stored.text
    assert "has_credentials" not in stored.text
    body = ChannelConfigWriteAcknowledgement.model_validate_json(stored.text)
    assert set(body.model_dump()) == CHANNEL_ACK_FIELDS
    assert body.channel == "orass_sandbox"
    assert body.credentials_rotated is True
    assert body.created is True

    settings = db_client.put(
        SETTINGS_URL, headers=_auth(), json={"deadline_overrides": {"BSD2": 14}}
    )
    assert settings.status_code == 200, settings.text
    settings_ack = ReportingSettingsWriteAcknowledgement.model_validate_json(settings.text)
    assert set(settings_ack.model_dump()) == SETTINGS_ACK_FIELDS


def test_settings_put_succeeds_with_configurator_binding(db_client: TestClient) -> None:
    _grant(RoleBundle.CONFIGURATOR)
    stored = db_client.put(
        SETTINGS_URL, headers=_auth(), json={"deadline_overrides": {"BSD2": 14, "FX-NOP": 10}}
    )
    assert stored.status_code == 200, stored.text
    settings_ack = ReportingSettingsWriteAcknowledgement.model_validate_json(stored.text)
    assert set(settings_ack.model_dump()) == SETTINGS_ACK_FIELDS
    assert settings_ack.deadline_overrides == {"BSD2": 14, "FX-NOP": 10}
    assert settings_ack.created is True

    replaced = db_client.put(SETTINGS_URL, headers=_auth(), json={"deadline_overrides": {}})
    assert replaced.status_code == 200, replaced.text
    replaced_ack = ReportingSettingsWriteAcknowledgement.model_validate_json(replaced.text)
    assert replaced_ack.deadline_overrides == {}
    assert replaced_ack.created is False

    read_back = db_client.get(SETTINGS_URL, headers=_auth())
    assert read_back.status_code == 200, read_back.text
    assert read_back.json()["deadline_overrides"] == {}


def test_denial_leaves_existing_rows_untouched(db_client: TestClient) -> None:
    _grant(RoleBundle.CONFIGURATOR)
    assert (
        db_client.put(CHANNEL_URL, headers=_auth(), json={"config": {"kept": True}}).status_code
        == 200
    )
    counts = _counts()

    with get_sessionmaker()() as db:
        _suspend_all(db)

    # With every binding suspended the actor loses even bank visibility: the
    # refusal is the opaque 404, and nothing is written either way.
    denied = db_client.put(CHANNEL_URL, headers=_auth(), json={"config": {"kept": False}})
    assert denied.status_code == 404, denied.text
    assert _counts()[0] == counts[0]  # no audit row on deny
    with get_sessionmaker()() as db:
        row = db.scalar(
            select(RegulatoryChannelConfig).where(
                RegulatoryChannelConfig.organization_id == ORG_1,
                RegulatoryChannelConfig.bank_id == SAMPLE_BANK_ID,
            )
        )
        assert row is not None
        assert row.config == {"kept": True}


def _suspend_all(db: Session | None = None) -> None:
    """Suspend EVERY active binding for the fixture actor (the hermetic demo
    tenant seeds an organization-scope viewer binding that would otherwise
    keep the bank covered)."""
    session = db if db is not None else open_tenant_session(ORG_1)
    suspend_all_bindings(
        session,
        organization_id=ORG_1,
        user_id=USER_1,
        reason="suspend every active fixture binding",
    )
    if db is None:
        session.close()


# --- the read gates -----------------------------------------------------------
#
# The seeded hermetic viewer (organization-scope, all modules, all sensitivities)
# covers reg/restricted VIEW on its own, so every deny case here suspends it and
# rebuilds exactly the coverage the case is about.


def _coverage_without_reg_view() -> None:
    """Suspend everything, then grant one non-REG exact-bank read: the actor
    stays covered for the bank (it exists for them) but holds no REG sentence."""
    _suspend_all()
    _grant(
        RoleBundle.VIEWER,
        module=ModuleScope.LIQUIDITY,
        sensitivity=SensitivityScope.CONFIDENTIAL,
    )


def test_reads_require_exact_bank_restricted_view(db_client: TestClient) -> None:
    """Covered, scalar roles and all, but without reg/restricted VIEW: both
    configuration reads are refused with the shared denial detail, before any
    stored content is read."""
    _coverage_without_reg_view()
    for url in (CHANNEL_URL, SETTINGS_URL):
        denied = db_client.get(url, headers=_auth())
        assert denied.status_code == 403, (url, denied.text)
        assert error_envelope(denied)["message"] == DEFAULT_DENIAL_DETAIL


def test_reads_read_for_the_exact_bank_viewer(db_client: TestClient) -> None:
    """The exact reg/restricted VIEW sentence reads both stored rows."""
    _suspend_all()
    _grant(RoleBundle.CONFIGURATOR)
    stored = db_client.put(
        CHANNEL_URL,
        headers=_auth(),
        json={"config": {"institution_code": "SBL-001"}, "credentials": {"api_key": "secret"}},
    )
    assert stored.status_code == 200, stored.text
    settings = db_client.put(
        SETTINGS_URL, headers=_auth(), json={"deadline_overrides": {"BSD2": 14}}
    )
    assert settings.status_code == 200, settings.text
    _grant(RoleBundle.VIEWER)

    read = db_client.get(CHANNEL_URL, headers=_auth())
    assert read.status_code == 200, read.text
    body = read.json()
    assert body["config"] == {"institution_code": "SBL-001"}
    assert body["has_credentials"] is True
    assert body["credential_fingerprint"]

    settings_read = db_client.get(SETTINGS_URL, headers=_auth())
    assert settings_read.status_code == 200, settings_read.text
    assert settings_read.json()["deadline_overrides"] == {"BSD2": 14}


def test_unconfigured_channel_404_only_after_authorization(db_client: TestClient) -> None:
    """A channel the bank never configured answers 404 to an authorized reader
    and 403 — never the unconfigured 404 — to one without the read sentence."""
    _suspend_all()
    _grant(RoleBundle.VIEWER)
    unconfigured = db_client.get(CHANNEL_URL, headers=_auth())
    assert unconfigured.status_code == 404, unconfigured.text
    assert "No channel configuration exists" in error_envelope(unconfigured)["message"]

    _suspend_all()
    _coverage_without_reg_view()
    denied = db_client.get(CHANNEL_URL, headers=_auth())
    assert denied.status_code == 403, denied.text
    assert error_envelope(denied)["message"] == DEFAULT_DENIAL_DETAIL


def test_reads_denied_for_the_sibling_bank_viewer(db_client: TestClient) -> None:
    """The reg/restricted VIEW sentence granted on the sibling bank reads
    nothing here: coverage comes from a non-REG row, the REG sentence sits on
    the wrong bank."""
    _suspend_all()
    _grant(
        RoleBundle.VIEWER,
        module=ModuleScope.LIQUIDITY,
        sensitivity=SensitivityScope.CONFIDENTIAL,
    )
    _grant(RoleBundle.VIEWER, bank_id=SIBLING)
    for url in (CHANNEL_URL, SETTINGS_URL):
        denied = db_client.get(url, headers=_auth())
        assert denied.status_code == 403, (url, denied.text)
        assert error_envelope(denied)["message"] == DEFAULT_DENIAL_DETAIL


def test_reads_hidden_without_any_coverage(db_client: TestClient) -> None:
    """With every binding suspended the bank itself is hidden: the opaque 404,
    for both reads."""
    _suspend_all()
    for url in (CHANNEL_URL, SETTINGS_URL):
        hidden = db_client.get(url, headers=_auth())
        assert hidden.status_code == 404, (url, hidden.text)
        assert error_envelope(hidden)["message"] == "Bank not found."


def test_reads_hidden_from_another_organization(db_client: TestClient) -> None:
    _grant(RoleBundle.CONFIGURATOR)
    other = headers(org_id="OR-1S000002", roles=("admin",))
    for url in (CHANNEL_URL, SETTINGS_URL):
        hidden = db_client.get(url, headers=other)
        assert hidden.status_code == 404, (url, hidden.text)
        assert error_envelope(hidden)["message"] == "Bank not found."
