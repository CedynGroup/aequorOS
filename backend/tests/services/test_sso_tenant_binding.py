"""Hermetic two-tenant matrix for the OIDC exchange tenant boundary.

No real IdP, key, token, network, or tenant data is used here. The structurally
valid test JWT only supplies routing hints; cryptographic verification is
replaced with a deterministic local verifier so these tests isolate the
post-verification tenant-binding rules from the already-covered JWKS machinery.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import jwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core import security
from app.db.session import get_sessionmaker
from app.models import SsoConnection, User
from app.services import authentication
from app.services.attestation import stepup
from tests.api.helpers import ORG_1, ORG_2, USER_1, USER_2

_ISSUER = "https://idp.tenant-one.example"
_OTHER_ISSUER = "https://idp.tenant-two.example"
_CLIENT_ID = "tenant-one-client"
_OTHER_CLIENT_ID = "tenant-two-client"
_SUBJECT = "shared-opaque-subject"
_EMAIL = "shared.user@shared.example"


@pytest.fixture(autouse=True)
def verified_idp(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Stand in only for successful cryptographic verification, without I/O."""

    def _verified_claims(id_token: str, *, issuer: str, audience: str) -> dict[str, object]:
        assert issuer in {_ISSUER, _OTHER_ISSUER}
        assert audience in {_CLIENT_ID, _OTHER_CLIENT_ID}
        claims = jwt.decode(id_token, options={"verify_signature": False})
        security.validate_oidc_authorized_party(claims, audience=audience)
        return claims

    monkeypatch.setattr("app.core.security.verify_oidc_id_token", _verified_claims)
    yield


def _token(
    *,
    issuer: str = _ISSUER,
    audience: str | list[str] = _CLIENT_ID,
    subject: str = _SUBJECT,
    email: str = _EMAIL,
    additional_claims: dict[str, object] | None = None,
) -> str:
    claims: dict[str, object] = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "email": email,
        "email_verified": True,
        "name": "Hermetic SSO User",
        "iat": 1,
        "exp": 4_102_444_800,
    }
    claims.update(additional_claims or {})
    return jwt.encode(
        claims,
        "hermetic-routing-hint-only-key-000000",
        algorithm="HS256",
    )


def _connection(  # noqa: PLR0913 - explicit IdP tuple keeps matrix cases readable
    db: Session,
    *,
    organization_id: str = ORG_1,
    issuer: str = _ISSUER,
    client_id: str = _CLIENT_ID,
    allowed_domains: list[str] | None = None,
    jit_enabled: bool = False,
) -> SsoConnection:
    connection = SsoConnection(
        organization_id=organization_id,
        issuer=issuer,
        client_id=client_id,
        client_secret_ciphertext="sealed-hermetic-placeholder",
        allowed_email_domains=allowed_domains or [],
        enabled=True,
        jit_enabled=jit_enabled,
    )
    db.add(connection)
    return connection


def _seeded_user(  # noqa: PLR0913 - explicit identity state keeps matrix cases readable
    db: Session,
    *,
    organization_id: str,
    email: str = _EMAIL,
    auth_provider: str = "password",
    subject: str | None = None,
    active: bool = True,
) -> User:
    user_id = USER_1 if organization_id == ORG_1 else USER_2
    user = db.get(User, user_id)
    assert user is not None
    user.email = email
    user.auth_provider = auth_provider
    user.sso_subject = subject
    user.is_active = active
    user.password_hash = None
    user.last_login_at = None
    user.access_rejected_at = None
    return user


def _assert_issued_for(issued: authentication.IssuedTokens, organization_id: str) -> None:
    token_pairs: tuple[tuple[str, security.TokenType], ...] = (
        (issued.access_token, "access"),
        (issued.refresh_token, "refresh"),
    )
    for token, token_type in token_pairs:
        claims = security.decode_token(token, expected_type=token_type)
        assert claims["org"] == organization_id


def _assert_http_error(
    exc_info: pytest.ExceptionInfo[HTTPException], status_code: int, detail: str
) -> None:
    assert exc_info.value.status_code == status_code
    assert exc_info.value.detail == detail


def test_verified_connection_cannot_link_user_from_caller_selected_other_tenant(
    db_session: Session,
) -> None:
    """F-01 regression: a verified tenant-one IdP cannot bind tenant-two.

    Before the hotfix, ``organization_id=ORG_2`` replaced the organization on
    the verified connection. The exchange linked USER_2 by email and issued an
    access/refresh pair carrying ORG_2 authority.
    """
    tenant_two_user = _seeded_user(db_session, organization_id=ORG_2)
    _connection(db_session)
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        authentication.login_with_sso(
            db_session,
            id_token=_token(),
            organization_hint=ORG_2,
        )

    _assert_http_error(exc_info, 401, "Invalid SSO token.")
    db_session.refresh(tenant_two_user)
    assert tenant_two_user.auth_provider == "password"
    assert tenant_two_user.sso_subject is None


def test_prelinked_subject_and_email_collisions_resolve_only_verified_tenant(
    db_session: Session,
) -> None:
    tenant_one_user = _seeded_user(
        db_session,
        organization_id=ORG_1,
        auth_provider="oidc",
        subject=_SUBJECT,
    )
    tenant_two_user = _seeded_user(
        db_session,
        organization_id=ORG_2,
        auth_provider="oidc",
        subject=_SUBJECT,
    )
    _connection(db_session)
    db_session.commit()

    issued = authentication.login_with_sso(db_session, id_token=_token())

    _assert_issued_for(issued, ORG_1)
    db_session.refresh(tenant_one_user)
    db_session.refresh(tenant_two_user)
    assert tenant_one_user.last_login_at is not None
    assert tenant_two_user.last_login_at is None


def test_first_email_link_with_overlapping_domains_updates_only_verified_tenant(
    db_session: Session,
) -> None:
    tenant_one_user = _seeded_user(db_session, organization_id=ORG_1)
    tenant_two_user = _seeded_user(db_session, organization_id=ORG_2)
    _connection(db_session, allowed_domains=["shared.example"])
    _connection(
        db_session,
        organization_id=ORG_2,
        issuer=_OTHER_ISSUER,
        client_id=_OTHER_CLIENT_ID,
        allowed_domains=["shared.example"],
    )
    db_session.commit()

    issued = authentication.login_with_sso(db_session, id_token=_token())

    _assert_issued_for(issued, ORG_1)
    db_session.refresh(tenant_one_user)
    db_session.refresh(tenant_two_user)
    assert (tenant_one_user.auth_provider, tenant_one_user.sso_subject) == ("oidc", _SUBJECT)
    assert (tenant_two_user.auth_provider, tenant_two_user.sso_subject) == ("password", None)


def test_list_valued_audience_supports_login_and_step_up_when_client_is_not_first(
    db_session: Session,
) -> None:
    _seeded_user(db_session, organization_id=ORG_1)
    _connection(db_session)
    db_session.commit()

    issued = authentication.login_with_sso(
        db_session,
        id_token=_token(
            audience=["another-audience", _CLIENT_ID],
            additional_claims={"azp": _CLIENT_ID},
        ),
    )

    _assert_issued_for(issued, ORG_1)
    evidence = stepup.verify_step_up(
        db_session,
        TenantContext(
            organization_id=ORG_1,
            actor_user_id=USER_1,
            roles=("approver",),
        ),
        USER_1,
        id_token=_token(
            audience=["another-audience", _CLIENT_ID],
            additional_claims={
                "azp": _CLIENT_ID,
                "auth_time": int(datetime.now(UTC).timestamp()),
            },
        ),
    )
    assert evidence["method"] == "oidc_reauth"


@pytest.mark.parametrize(
    "extra_claims",
    [{}, {"azp": _OTHER_CLIENT_ID}],
    ids=["missing-azp", "wrong-azp"],
)
def test_multi_audience_rejects_invalid_authorized_party_before_account_lookup(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    extra_claims: dict[str, object],
) -> None:
    _seeded_user(db_session, organization_id=ORG_1)
    _connection(db_session)
    db_session.commit()
    monkeypatch.setattr(
        authentication,
        "_resolve_sso_user",
        lambda *args, **kwargs: pytest.fail("account lookup must not run"),
    )

    with pytest.raises(HTTPException) as exc_info:
        authentication.login_with_sso(
            db_session,
            id_token=_token(
                audience=["another-audience", _CLIENT_ID],
                additional_claims=extra_claims,
            ),
        )

    _assert_http_error(exc_info, 401, "Invalid SSO token.")


def test_single_audience_accepts_missing_authorized_party(db_session: Session) -> None:
    _seeded_user(db_session, organization_id=ORG_1)
    _connection(db_session)
    db_session.commit()

    issued = authentication.login_with_sso(db_session, id_token=_token())

    _assert_issued_for(issued, ORG_1)


@pytest.mark.parametrize("authorized_party", [_OTHER_CLIENT_ID, None], ids=["wrong", "null"])
def test_single_audience_rejects_mismatched_authorized_party_before_account_lookup(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    authorized_party: object,
) -> None:
    _seeded_user(db_session, organization_id=ORG_1)
    _connection(db_session)
    db_session.commit()
    monkeypatch.setattr(
        authentication,
        "_resolve_sso_user",
        lambda *args, **kwargs: pytest.fail("account lookup must not run"),
    )

    with pytest.raises(HTTPException) as exc_info:
        authentication.login_with_sso(
            db_session,
            id_token=_token(additional_claims={"azp": authorized_party}),
        )

    _assert_http_error(exc_info, 401, "Invalid SSO token.")


def test_oversized_audience_list_fails_generically_before_verification(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seeded_user(db_session, organization_id=ORG_1)
    _connection(db_session)
    db_session.commit()
    monkeypatch.setattr(
        "app.core.security.verify_oidc_id_token",
        lambda *args, **kwargs: pytest.fail("oversized routing must not attempt verification"),
    )

    with pytest.raises(HTTPException) as exc_info:
        authentication.login_with_sso(
            db_session,
            id_token=_token(audience=[*[f"audience-{index}" for index in range(8)], _CLIENT_ID]),
        )

    _assert_http_error(exc_info, 401, "Invalid SSO token.")


def test_ambiguous_enabled_connection_selection_fails_generically_before_verification(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_one_user = _seeded_user(db_session, organization_id=ORG_1)
    tenant_two_user = _seeded_user(db_session, organization_id=ORG_2)
    _connection(db_session)
    _connection(db_session, organization_id=ORG_2)
    db_session.commit()
    monkeypatch.setattr(
        "app.core.security.verify_oidc_id_token",
        lambda *args, **kwargs: pytest.fail("ambiguous routing must not attempt verification"),
    )

    with pytest.raises(HTTPException) as exc_info:
        authentication.login_with_sso(db_session, id_token=_token())

    _assert_http_error(exc_info, 401, "Invalid SSO token.")
    db_session.refresh(tenant_one_user)
    db_session.refresh(tenant_two_user)
    assert tenant_one_user.sso_subject is None
    assert tenant_two_user.sso_subject is None


@pytest.mark.parametrize("jit_enabled", [False, True], ids=["jit-disabled", "jit-enabled"])
def test_unknown_identity_with_cross_tenant_collisions_never_uses_other_tenant(
    db_session: Session,
    jit_enabled: bool,
) -> None:
    _seeded_user(
        db_session,
        organization_id=ORG_1,
        email="different@shared.example",
    )
    tenant_two_user = _seeded_user(
        db_session,
        organization_id=ORG_2,
        auth_provider="oidc",
        subject=_SUBJECT,
    )
    _connection(
        db_session,
        allowed_domains=["shared.example"],
        jit_enabled=jit_enabled,
    )
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        authentication.login_with_sso(db_session, id_token=_token())

    expected_detail = (
        "Your access request has been recorded. An administrator must approve "
        "your account before you can sign in."
        if jit_enabled
        else "No AequorOS account is provisioned for this identity."
    )
    _assert_http_error(exc_info, 403 if jit_enabled else 401, expected_detail)
    db_session.refresh(tenant_two_user)
    assert tenant_two_user.last_login_at is None
    stubs = db_session.scalars(
        select(User).where(User.organization_id == ORG_1, User.email == _EMAIL)
    ).all()
    assert len(stubs) == int(jit_enabled)
    if stubs:
        assert stubs[0].is_active is False
        assert stubs[0].auth_provider == "oidc"
        assert stubs[0].sso_subject == _SUBJECT


def test_inactive_user_and_existing_jit_stub_never_issue_cross_tenant_authority(
    db_session: Session,
) -> None:
    tenant_one_stub = _seeded_user(
        db_session,
        organization_id=ORG_1,
        auth_provider="oidc",
        subject=_SUBJECT,
        active=False,
    )
    tenant_two_user = _seeded_user(
        db_session,
        organization_id=ORG_2,
        auth_provider="oidc",
        subject=_SUBJECT,
    )
    _connection(
        db_session,
        allowed_domains=["shared.example"],
        jit_enabled=True,
    )
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        authentication.login_with_sso(db_session, id_token=_token())

    _assert_http_error(
        exc_info,
        403,
        "Your access request has been recorded. An administrator must approve "
        "your account before you can sign in.",
    )
    db_session.refresh(tenant_one_stub)
    db_session.refresh(tenant_two_user)
    assert tenant_one_stub.is_active is False
    assert tenant_one_stub.last_login_at is None
    assert tenant_two_user.last_login_at is None
    assert (
        db_session.scalar(select(User).where(User.organization_id == ORG_1, User.email == _EMAIL))
        == tenant_one_stub
    )


def test_inactive_prelinked_user_is_not_rescued_by_active_other_tenant(
    db_session: Session,
) -> None:
    tenant_one_user = _seeded_user(
        db_session,
        organization_id=ORG_1,
        auth_provider="oidc",
        subject=_SUBJECT,
        active=False,
    )
    tenant_two_user = _seeded_user(
        db_session,
        organization_id=ORG_2,
        auth_provider="oidc",
        subject=_SUBJECT,
    )
    _connection(db_session)
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        authentication.login_with_sso(db_session, id_token=_token())

    _assert_http_error(exc_info, 401, "No AequorOS account is provisioned for this identity.")
    db_session.refresh(tenant_one_user)
    db_session.refresh(tenant_two_user)
    assert tenant_one_user.last_login_at is None
    assert tenant_two_user.last_login_at is None


def test_public_exchange_contract_omits_tenant_authority_and_transition_is_fail_closed(
    db_client: TestClient,
) -> None:
    with get_sessionmaker()() as db:
        _seeded_user(db, organization_id=ORG_1)
        _seeded_user(db, organization_id=ORG_2)
        _connection(db)
        db.commit()

    schema = db_client.get("/openapi.json").json()["components"]["schemas"]["SsoLoginRequest"]
    assert schema["required"] == ["id_token"]
    assert set(schema["properties"]) == {"id_token"}

    mismatch = db_client.post(
        "/api/v1/auth/sso",
        json={"id_token": _token(), "organization_id": ORG_2},
    )
    assert mismatch.status_code == 401
    assert mismatch.json()["error"]["message"] == "Invalid SSO token."

    compatible = db_client.post(
        "/api/v1/auth/sso",
        json={"id_token": _token(), "organization_id": ORG_1},
    )
    assert compatible.status_code == 200, compatible.text
    token_pairs: tuple[tuple[str, security.TokenType], ...] = (
        (compatible.json()["access_token"], "access"),
        (compatible.json()["refresh_token"], "refresh"),
    )
    for token, token_type in token_pairs:
        assert security.decode_token(token, expected_type=token_type)["org"] == ORG_1
