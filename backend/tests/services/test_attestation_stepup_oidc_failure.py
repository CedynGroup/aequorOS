"""An unacceptable id_token must REFUSE the step-up, never 500.

``_verify_oidc_proof`` calls two functions that raise
:class:`app.core.security.AuthError` — ``unverified_claims`` (routing) and
``verify_oidc_id_token`` (the real verification) — and caught neither. Every
ordinary failure therefore escaped the service as an unhandled exception:

* a malformed or truncated id_token — reachable today, with no attacker
  needed, by any signer whose IdP round-trip returned something unexpected;
* a token that fails signature/audience/expiry verification;
* and, since the OIDC SSRF fix, an issuer the egress guard refuses to reach,
  which :func:`app.core.security._guard_oidc_target` converts into a
  ``TokenInvalidError`` precisely so that "blocked" reads as an authentication
  failure rather than a platform fault.

The 500 body leaks nothing, so this was never a disclosure — it was the signer
being told the platform broke, mid-ceremony, with no route to the password
path that would have worked. Refusal is the typed ``step_up_failed`` 403 the
password proof already returns, worded identically so the IdP's business stays
the IdP's.
"""

from __future__ import annotations

from typing import Any, cast

import jwt
import pytest
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core import security
from app.models import SsoConnection, User
from app.services.attestation import stepup
from tests.api.helpers import ORG_1, USER_1

_ISSUER = "https://idp.example.test"
_CLIENT_ID = "aequoros-step-up-client"
_SSO_SUBJECT = "idp-subject-for-step-up"

CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1, roles=("approver",))


@pytest.fixture
def signer(db_session: Session) -> User:
    user = db_session.scalar(select(User).where(User.id == USER_1))
    assert user is not None
    user.auth_provider = "oidc"
    user.sso_subject = _SSO_SUBJECT
    db_session.add(
        SsoConnection(
            organization_id=ORG_1,
            issuer=_ISSUER,
            client_id=_CLIENT_ID,
            allowed_email_domains=[],
            enabled=True,
        )
    )
    db_session.commit()
    return user


def _id_token() -> str:
    return jwt.encode(
        {"iss": _ISSUER, "aud": _CLIENT_ID, "sub": _SSO_SUBJECT, "iat": 1, "exp": 4102444800},
        "not-the-real-idp-key-padded-to-32-bytes!",
        algorithm="HS256",
    )


def _detail(error: HTTPException) -> dict[str, Any]:
    detail = error.detail
    assert isinstance(detail, dict)
    return cast("dict[str, Any]", detail)


def _assert_refused(error: HTTPException) -> None:
    """A typed 4xx refusal, worded exactly like a failed password re-entry."""
    assert error.status_code == status.HTTP_403_FORBIDDEN
    body = _detail(error)
    assert body["error_code"] == "step_up_failed"
    assert body["message"] == stepup._REAUTH_FAILED  # noqa: SLF001 - pinning the shared wording
    # Nothing about the issuer, the token, or the destination reaches the body.
    for leak in ("issuer", "jwks", "signature", "http", "169.254"):
        assert leak not in body["message"].lower()


def test_a_malformed_id_token_refuses_instead_of_raising(
    db_session: Session, signer: User
) -> None:
    """Reachable with no attacker at all: ``unverified_claims`` raises before
    any connection lookup, so this was a 500 on every garbled round-trip."""
    with pytest.raises(stepup.StepUpFailed) as refused:
        stepup.verify_step_up(db_session, CTX, USER_1, id_token="not-a-jwt")
    _assert_refused(refused.value)


def test_a_token_that_fails_verification_refuses_instead_of_raising(
    db_session: Session, signer: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _reject(id_token: str, *, issuer: str, audience: str) -> dict[str, Any]:
        raise security.TokenInvalidError("OIDC id_token verification failed: signature")

    monkeypatch.setattr("app.core.security.verify_oidc_id_token", _reject)
    with pytest.raises(stepup.StepUpFailed) as refused:
        stepup.verify_step_up(db_session, CTX, USER_1, id_token=_id_token())
    _assert_refused(refused.value)


def test_an_issuer_blocked_by_the_egress_guard_refuses_instead_of_raising(
    db_session: Session, signer: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SSRF guard's own failure mode. ``_guard_oidc_target`` raises
    ``TokenInvalidError`` so a blocked destination is an auth failure; without
    the catch below that intent was undone one layer up."""

    def _blocked(id_token: str, *, issuer: str, audience: str) -> dict[str, Any]:
        raise security.TokenInvalidError(
            "The OIDC issuer is not a permitted destination for an outbound connection."
        )

    monkeypatch.setattr("app.core.security.verify_oidc_id_token", _blocked)
    with pytest.raises(stepup.StepUpFailed) as refused:
        stepup.verify_step_up(db_session, CTX, USER_1, id_token=_id_token())
    _assert_refused(refused.value)


def test_no_auth_error_of_any_kind_escapes_the_oidc_proof(
    db_session: Session, signer: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The catch is on the BASE class, so a future ``AuthError`` subclass — an
    unconfigured signing secret, say — refuses rather than 500s."""

    def _misconfigured(id_token: str, *, issuer: str, audience: str) -> dict[str, Any]:
        raise security.AuthConfigError("auth is not configured")

    monkeypatch.setattr("app.core.security.verify_oidc_id_token", _misconfigured)
    with pytest.raises(HTTPException) as refused:
        stepup.verify_step_up(db_session, CTX, USER_1, id_token=_id_token())
    assert isinstance(refused.value, stepup.StepUpFailed)
    _assert_refused(refused.value)


def test_a_genuine_mismatch_still_reports_its_own_reason(
    db_session: Session, signer: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The catch must not swallow the checks that FOLLOW verification — a token
    for a different subject still has to say so, not fall into the generic
    wording."""
    monkeypatch.setattr(
        "app.core.security.verify_oidc_id_token",
        lambda id_token, *, issuer, audience: jwt.decode(
            id_token, options={"verify_signature": False}
        ),
    )
    signer.sso_subject = "somebody-else"
    db_session.commit()
    with pytest.raises(stepup.StepUpFailed) as refused:
        stepup.verify_step_up(db_session, CTX, USER_1, id_token=_id_token())
    assert "does not match the signed-in user" in _detail(refused.value)["message"]


# --- auth_time parsing ------------------------------------------------------
#
# A token can pass signature/audience/expiry verification and STILL carry an
# auth_time this system cannot read. `int(auth_time)` and
# `datetime.fromtimestamp` raise TypeError / ValueError / OverflowError / OSError
# on those, and an unhandled raise at that point is a 500 — the signer is told
# the platform broke, mid-ceremony, for what is squarely a refusal. Same defect
# class as the AuthError cases above, one line further down.


def _verified_token_with(monkeypatch: pytest.MonkeyPatch, **extra: Any) -> str:
    """A token that PASSES verification, carrying the given extra claims."""
    monkeypatch.setattr(
        "app.core.security.verify_oidc_id_token",
        lambda id_token, *, issuer, audience: jwt.decode(
            id_token, options={"verify_signature": False}
        ),
    )
    return jwt.encode(
        {
            "iss": _ISSUER,
            "aud": _CLIENT_ID,
            "sub": _SSO_SUBJECT,
            "iat": 1,
            "exp": 4102444800,
            **extra,
        },
        "not-the-real-idp-key-padded-to-32-bytes!",
        algorithm="HS256",
    )


@pytest.mark.parametrize(
    ("auth_time", "why"),
    [
        ("yesterday", "a non-numeric string -> ValueError from int()"),
        ("", "an empty string -> ValueError from int()"),
        ({"at": 1}, "a JSON object -> TypeError from int()"),
        ([1755000000], "a JSON array -> TypeError from int()"),
        (10**20, "beyond the epoch range -> OverflowError/OSError/ValueError"),
        (-(10**20), "negative and out of range"),
    ],
)
def test_an_unreadable_auth_time_refuses_instead_of_raising(
    db_session: Session,
    signer: User,
    monkeypatch: pytest.MonkeyPatch,
    auth_time: object,
    why: str,
) -> None:
    id_token = _verified_token_with(monkeypatch, auth_time=auth_time)
    with pytest.raises(HTTPException) as refused:
        stepup.verify_step_up(db_session, CTX, USER_1, id_token=id_token)
    assert isinstance(refused.value, stepup.StepUpFailed), why
    assert refused.value.status_code == status.HTTP_403_FORBIDDEN
    message = _detail(refused.value)["message"]
    # Its OWN reason, not the generic re-auth wording: the signer needs to know
    # the password path is the way through, and the admin needs the claim named.
    assert "auth_time" in message
    assert "could not read" in message or "not read" in message
    # The offending value never reaches the body. (Skip the empty string, for
    # which the containment check is vacuously true rather than meaningful.)
    if str(auth_time):
        assert str(auth_time) not in message


def test_a_readable_auth_time_still_passes(
    db_session: Session, signer: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: the catch must not swallow a GOOD auth_time. Without
    this, a bare `except Exception` would look identical to a correct fix."""
    from datetime import UTC, datetime  # noqa: PLC0415 - local to this check

    now = int(datetime.now(UTC).timestamp())
    id_token = _verified_token_with(monkeypatch, auth_time=now)
    evidence = stepup.verify_step_up(db_session, CTX, USER_1, id_token=id_token)
    assert evidence is not None


def test_a_stale_auth_time_still_reports_staleness_not_unreadability(
    db_session: Session, signer: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A perfectly readable but OLD auth_time must keep its own message — the
    parse guard must not absorb the freshness check that follows it."""
    id_token = _verified_token_with(monkeypatch, auth_time=1)  # 1970
    with pytest.raises(stepup.StepUpFailed) as refused:
        stepup.verify_step_up(db_session, CTX, USER_1, id_token=id_token)
    message = _detail(refused.value)["message"]
    assert "older sign-in" in message
