"""Brute-force defence on the signing step-up (audit finding P0-4).

The step-up endpoint is the last control between a stolen — or merely
over-privileged — session and a legally binding regulatory attestation. It used
to call ``verify_password`` directly and write nothing back, so an authenticated
analyst could guess an approver's password at it forever. The properties
asserted here are the ones that would be DEFECTS if they regressed:

* a wrong password is COUNTED, durably, in the same two ``users`` columns the
  sign-in path uses — so the two surfaces share one budget of guesses rather
  than granting ``max_failed`` each;
* the count survives the refusal (it is committed before the exception, not
  rolled back with it) and survives a process restart, because a
  ``--workers 4`` deployment behind more than one replica cannot defend itself
  with a counter that lives in one process's memory;
* crossing the threshold LOCKS, the lock is checked BEFORE the hash (so the
  right password does not open a locked account), and the lock is the same one
  sign-in writes and reads;
* nothing is minted after the lock — the control fails closed;
* an unknown membership and a wrong password are indistinguishable, in wording
  and in the Argon2 work they burn;
* an id_token with no ``auth_time`` is refused rather than treated as fresh, or
  the "prove presence now" check would be decorative;
* a signing authorisation is burned exactly once even when two readers both saw
  it unspent;
* and the legitimate preparer → approver ceremony still completes with the
  throttle in place.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core import security
from app.core.config import get_settings
from app.models import (
    AttestationSignature,
    SigningAuthorization,
    SsoConnection,
    User,
)
from app.schemas.attestation import CertifyRequest, StepUpRequest
from app.services import attestation_api, auth_throttle
from app.services.attestation import stepup
from app.services.attestation.identity import ensure_signer_identity
from app.services.attestation.keys import SignerKeyService
from tests.api.helpers import ORG_1, USER_1

PASSWORD = "correct-horse-battery-staple-0001"  # noqa: S105 - disposable fixture
WRONG = "not-the-password-0002"  # noqa: S105 - disposable fixture
CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1, roles=("approver",))

_ISSUER = "https://idp.example.test"
_CLIENT_ID = "aequoros-step-up-client"
_SSO_SUBJECT = "idp-subject-for-step-up"


# ---------------------------------------------------------------------------
# Lightweight fixtures: the throttle needs a user, not a whole bank.
# ---------------------------------------------------------------------------
@pytest.fixture
def signer(db_session: Session) -> User:
    """The demo tenant's user, given a real password hash."""
    user = db_session.scalar(select(User).where(User.id == USER_1))
    assert user is not None
    user.password_hash = security.hash_password(PASSWORD)
    user.auth_provider = "password"
    user.failed_login_attempts = 0
    user.locked_until = None
    db_session.commit()
    return user


def _detail(error: HTTPException) -> dict[str, Any]:
    """Every step-up refusal carries the typed ``{error_code, message}`` body
    the dashboard branches on — assert against that shape, not against a str."""
    detail = error.detail
    assert isinstance(detail, dict)
    return cast("dict[str, Any]", detail)


def _step_up(db: Session, **kwargs: Any) -> dict[str, Any]:
    return stepup.verify_step_up(db, CTX, USER_1, **kwargs)


def _mint(db: Session) -> tuple[str, SigningAuthorization]:
    return stepup.mint_authorization(
        db,
        CTX,
        user_id=USER_1,
        signer_id="SGN-TESTTESTTESTTES",
        package_id=uuid4(),
        signing_role="preparer",
        certification_digest="d" * 64,
        auth_evidence={"method": "password_reauth"},
    )


def _attempts(db: Session) -> int:
    db.expire_all()
    value = db.scalar(select(User.failed_login_attempts).where(User.id == USER_1))
    assert value is not None
    return value


# ---------------------------------------------------------------------------
# The success path still works
# ---------------------------------------------------------------------------
def test_correct_password_steps_up_and_mints_an_authorization(
    db_session: Session, signer: User
) -> None:
    evidence = _step_up(db_session, password=PASSWORD)
    assert evidence["method"] == "password_reauth"

    raw, row = _mint(db_session)
    assert raw
    assert row.consumed_at is None
    assert row.user_id == USER_1


# ---------------------------------------------------------------------------
# Failed-attempt tracking
# ---------------------------------------------------------------------------
def test_wrong_password_increments_the_durable_failure_counter(
    db_session: Session, signer: User
) -> None:
    with pytest.raises(stepup.StepUpFailed) as refused:
        _step_up(db_session, password=WRONG)
    assert refused.value.status_code == 403
    assert _attempts(db_session) == 1

    with pytest.raises(stepup.StepUpFailed):
        _step_up(db_session, password=WRONG)
    assert _attempts(db_session) == 2


def test_the_failure_count_is_committed_not_rolled_back_with_the_refusal(
    db_session: Session, signer: User
) -> None:
    """A count the refusal's rollback erases is a count that never happened —
    which is exactly how the endpoint went unthrottled. Prove it from a session
    that never saw the failing one's in-memory state."""
    with pytest.raises(stepup.StepUpFailed):
        _step_up(db_session, password=WRONG)
    db_session.rollback()

    fresh = Session(db_session.get_bind())
    try:
        persisted = fresh.scalar(select(User.failed_login_attempts).where(User.id == USER_1))
    finally:
        fresh.close()
    assert persisted == 1


def test_crossing_the_threshold_locks_the_account(db_session: Session, signer: User) -> None:
    limit = get_settings().auth.max_failed_logins
    for _ in range(limit - 1):
        with pytest.raises(stepup.StepUpFailed):
            _step_up(db_session, password=WRONG)

    with pytest.raises(stepup.StepUpLocked) as locked:
        _step_up(db_session, password=WRONG)
    assert locked.value.status_code == 423
    assert _detail(locked.value)["error_code"] == "step_up_locked"

    db_session.expire_all()
    user = db_session.scalar(select(User).where(User.id == USER_1))
    assert user is not None
    assert auth_throttle.is_locked(user)


def test_a_locked_account_cannot_step_up_with_the_correct_password(
    db_session: Session, signer: User
) -> None:
    limit = get_settings().auth.max_failed_logins
    for _ in range(limit):
        with pytest.raises((stepup.StepUpFailed, stepup.StepUpLocked)):
            _step_up(db_session, password=WRONG)

    with pytest.raises(stepup.StepUpLocked):
        _step_up(db_session, password=PASSWORD)


def test_a_locked_account_mints_no_authorization(db_session: Session, signer: User) -> None:
    """Fail closed: the point of the lock is that nothing is issued past it."""
    limit = get_settings().auth.max_failed_logins
    for _ in range(limit):
        with pytest.raises((stepup.StepUpFailed, stepup.StepUpLocked)):
            _step_up(db_session, password=WRONG)
    with pytest.raises(stepup.StepUpLocked):
        _step_up(db_session, password=PASSWORD)

    db_session.rollback()
    assert db_session.scalars(select(SigningAuthorization.id)).all() == []


def test_a_lockout_earned_at_sign_in_also_closes_signing(db_session: Session, signer: User) -> None:
    """One budget of guesses across both surfaces: the sign-in path writes these
    same two columns, so a lock earned there must close this door too."""
    signer.failed_login_attempts = get_settings().auth.max_failed_logins
    signer.locked_until = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=10)
    db_session.commit()

    with pytest.raises(stepup.StepUpLocked):
        _step_up(db_session, password=PASSWORD)


def test_an_expired_lock_lets_the_right_password_through(db_session: Session, signer: User) -> None:
    signer.failed_login_attempts = get_settings().auth.max_failed_logins
    signer.locked_until = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
    db_session.commit()

    evidence = _step_up(db_session, password=PASSWORD)
    assert evidence["method"] == "password_reauth"


def test_a_successful_step_up_clears_the_counter_and_the_lock(
    db_session: Session, signer: User
) -> None:
    for _ in range(2):
        with pytest.raises(stepup.StepUpFailed):
            _step_up(db_session, password=WRONG)
    assert _attempts(db_session) == 2

    _step_up(db_session, password=PASSWORD)

    db_session.expire_all()
    user = db_session.scalar(select(User).where(User.id == USER_1))
    assert user is not None
    assert user.failed_login_attempts == 0
    assert user.locked_until is None
    # A step-up is a signature ceremony, not a sign-in: the login history must
    # not claim the officer signed in.
    assert user.last_login_at is None


def test_each_lockout_is_longer_than_the_last(db_session: Session, signer: User) -> None:
    settings = get_settings().auth
    first = auth_throttle._backoff_seconds(  # noqa: SLF001 - the policy under test
        settings.max_failed_logins, settings
    )
    second = auth_throttle._backoff_seconds(  # noqa: SLF001
        settings.max_failed_logins + 1, settings
    )
    assert first == settings.lockout_seconds
    assert second > first
    # ...but bounded, so five wrong keystrokes can never cost a filing deadline.
    runaway = auth_throttle._backoff_seconds(  # noqa: SLF001
        settings.max_failed_logins + 50, settings
    )
    assert runaway == auth_throttle.MAX_LOCKOUT_SECONDS


# ---------------------------------------------------------------------------
# No enumeration leak
# ---------------------------------------------------------------------------
def test_unknown_membership_and_wrong_password_refuse_identically(
    db_session: Session, signer: User
) -> None:
    with pytest.raises(stepup.StepUpFailed) as wrong_password:
        _step_up(db_session, password=WRONG)

    with pytest.raises(stepup.StepUpFailed) as unknown_user:
        stepup.verify_step_up(db_session, CTX, uuid4(), password=WRONG)

    assert wrong_password.value.status_code == unknown_user.value.status_code
    assert wrong_password.value.detail == unknown_user.value.detail


def test_an_sso_only_account_refuses_exactly_like_a_wrong_password(
    db_session: Session, signer: User
) -> None:
    """No password hash must not be a cheaper, differently-worded refusal —
    that would make "does this officer have a password?" readable off the wire.
    It is counted too, so it is not a free oracle either."""
    with pytest.raises(stepup.StepUpFailed) as wrong_password:
        _step_up(db_session, password=WRONG)
    db_session.expire_all()

    signer_row = db_session.scalar(select(User).where(User.id == USER_1))
    assert signer_row is not None
    signer_row.password_hash = None
    signer_row.failed_login_attempts = 0
    db_session.commit()

    with pytest.raises(stepup.StepUpFailed) as no_password:
        _step_up(db_session, password=WRONG)

    assert no_password.value.detail == wrong_password.value.detail
    assert _attempts(db_session) == 1


def test_a_service_account_can_never_step_up(db_session: Session, signer: User) -> None:
    signer.auth_provider = "service"
    db_session.commit()
    with pytest.raises(stepup.StepUpFailed):
        _step_up(db_session, password=PASSWORD)


# ---------------------------------------------------------------------------
# Contract bounds
# ---------------------------------------------------------------------------
def test_the_step_up_contract_bounds_both_proofs() -> None:
    """Verifying a password costs ~64 MiB of Argon2. An unbounded field is a
    cheap way to make the signing endpoint expensive."""
    with pytest.raises(ValidationError):
        StepUpRequest(signing_role="preparer", password="x" * 1025)
    with pytest.raises(ValidationError):
        StepUpRequest(signing_role="preparer", password="")
    with pytest.raises(ValidationError):
        StepUpRequest(signing_role="preparer", id_token="x" * 8193)

    assert StepUpRequest(signing_role="preparer", password="x" * 1024).password
    assert StepUpRequest(signing_role="preparer", password=None).password is None


# ---------------------------------------------------------------------------
# The OIDC path
# ---------------------------------------------------------------------------
def _connection(db: Session) -> None:
    db.add(
        SsoConnection(
            organization_id=ORG_1,
            issuer=_ISSUER,
            client_id=_CLIENT_ID,
            allowed_email_domains=[],
            enabled=True,
        )
    )
    db.commit()


def _id_token(**overrides: object) -> str:
    claims: dict[str, object] = {
        "iss": _ISSUER,
        "aud": _CLIENT_ID,
        "sub": _SSO_SUBJECT,
        "iat": 1,
        "exp": 4102444800,
    }
    claims.update(overrides)
    return jwt.encode(claims, "not-the-real-idp-key-padded-to-32-bytes!", algorithm="HS256")


@pytest.fixture
def sso(db_session: Session, signer: User, monkeypatch: pytest.MonkeyPatch) -> None:
    _connection(db_session)
    signer.sso_subject = _SSO_SUBJECT
    db_session.commit()
    monkeypatch.setattr(
        "app.core.security.verify_oidc_id_token",
        lambda id_token, *, issuer, audience: jwt.decode(
            id_token, options={"verify_signature": False}
        ),
    )


def test_an_id_token_without_auth_time_is_refused(db_session: Session, sso: None) -> None:
    """The step-up redirect sends ``max_age=0``, which OIDC Core §3.1.2.1 makes
    ``auth_time`` mandatory in the response. Treating an absent claim as fresh
    made the freshness check decorative for any IdP that omitted it."""
    with pytest.raises(stepup.StepUpFailed) as refused:
        _step_up(db_session, id_token=_id_token())
    assert "did not report when you authenticated" in _detail(refused.value)["message"]


def test_a_stale_auth_time_is_refused(db_session: Session, sso: None) -> None:
    stale = dt.datetime.now(dt.UTC) - (stepup.MAX_AUTH_AGE + dt.timedelta(minutes=1))
    with pytest.raises(stepup.StepUpFailed):
        _step_up(db_session, id_token=_id_token(auth_time=int(stale.timestamp())))


def test_a_fresh_auth_time_steps_up(db_session: Session, sso: None) -> None:
    now = int(dt.datetime.now(dt.UTC).timestamp())
    evidence = _step_up(db_session, id_token=_id_token(auth_time=now, acr="urn:mfa"))
    assert evidence["method"] == "oidc_reauth"
    assert evidence["acr"] == "urn:mfa"
    assert evidence["auth_time"]


def test_the_password_lockout_does_not_close_the_idp_path(
    db_session: Session, signer: User, sso: None
) -> None:
    """An id_token is an unguessable signed assertion, so the throttle would buy
    nothing here — while letting someone else's failed guessing at the sign-in
    page deny an approver the ability to file."""
    signer.failed_login_attempts = get_settings().auth.max_failed_logins
    signer.locked_until = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=10)
    db_session.commit()

    now = int(dt.datetime.now(dt.UTC).timestamp())
    evidence = _step_up(db_session, id_token=_id_token(auth_time=now))
    assert evidence["method"] == "oidc_reauth"


# ---------------------------------------------------------------------------
# The authorisation stays single-use
# ---------------------------------------------------------------------------
def _consume(db: Session, token: str, row: SigningAuthorization) -> SigningAuthorization:
    return stepup.consume_authorization(
        db,
        CTX,
        token=token,
        user_id=row.user_id,
        package_id=row.package_id,
        signing_role=row.signing_role,
        certification_digest=row.certification_digest,
    )


def test_an_authorization_can_only_be_spent_once(db_session: Session, signer: User) -> None:
    raw, row = _mint(db_session)
    spent = _consume(db_session, raw, row)
    assert spent.consumed_at is not None
    db_session.commit()

    with pytest.raises(stepup.StepUpFailed) as refused:
        _consume(db_session, raw, row)
    assert "already been used" in _detail(refused.value)["message"]


def test_two_readers_that_both_saw_it_unspent_cannot_both_burn_it(
    db_session: Session, signer: User
) -> None:
    """The race the read-then-write had: two certifications read
    ``consumed_at IS NULL``, both write it, and one act of presence produces two
    signatures. Reproduced deterministically — the second session holds a view
    of the row taken BEFORE the first burned it, which is precisely what a
    concurrent reader has — and defeated by making the burn itself conditional.
    """
    raw, row = _mint(db_session)
    package_id, user_id = row.package_id, row.user_id
    digest, role = row.certification_digest, row.signing_role

    other = Session(db_session.get_bind())
    try:
        # The second reader's view, taken while the authorisation is unspent.
        stale = other.scalar(select(SigningAuthorization).where(SigningAuthorization.id == row.id))
        assert stale is not None
        assert stale.consumed_at is None

        _consume(db_session, raw, row)
        db_session.commit()

        # Its in-memory row still says unspent; the database disagrees, and the
        # database is what decides.
        assert stale.consumed_at is None
        with pytest.raises(stepup.StepUpFailed) as refused:
            stepup.consume_authorization(
                other,
                CTX,
                token=raw,
                user_id=user_id,
                package_id=package_id,
                signing_role=role,
                certification_digest=digest,
            )
        assert "already been used" in _detail(refused.value)["message"]
    finally:
        other.rollback()
        other.close()


def test_an_expired_authorization_is_refused(db_session: Session, signer: User) -> None:
    raw, row = _mint(db_session)
    row.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
    db_session.commit()
    with pytest.raises(stepup.StepUpFailed) as refused:
        _consume(db_session, raw, row)
    assert "expired" in _detail(refused.value)["message"]


# ---------------------------------------------------------------------------
# The real ceremony, end to end, through the throttled step-up
# ---------------------------------------------------------------------------
APPROVER_PASSWORD = "approver-password-not-production-03"  # noqa: S105 - fixture


@pytest.fixture
def signing_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("SIGNER_ID_PEPPER", "test-signer-pepper-not-for-production")
    monkeypatch.setenv("ATTESTATION_SIGNING_ENABLED", "1")
    monkeypatch.setenv("SIGNING_BACKEND", "software")
    monkeypatch.setenv(
        "CREDENTIAL_VAULT_MASTER_KEY", "test-vault-master-key-not-for-production-0004"
    )
    monkeypatch.setenv("SIGNING_SOFTWARE_KEY_DIR", str(tmp_path / "signing_keys"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _http_request() -> Any:
    from starlette.requests import Request  # noqa: PLC0415 - test-only plumbing

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 5555),
            "headers": [(b"user-agent", b"pytest")],
        }
    )


def _enrol(db: Session, ctx: TenantContext, password: str) -> None:
    """Give a user a password, a signer identity, and an active signing key."""
    assert ctx.actor_user_id is not None
    user = db.scalar(select(User).where(User.id == ctx.actor_user_id))
    assert user is not None
    user.password_hash = security.hash_password(password)
    user.auth_provider = "password"
    identity = ensure_signer_identity(db, ctx, ctx.actor_user_id)
    service = SignerKeyService(db, ctx)
    if service.active_key(identity.signer_id) is None:
        service.issue(
            signer_id=identity.signer_id,
            display_name=user.display_name or "Test Signer",
            organization_name="Sample Bank",
        )
    db.commit()


def _ceremony(  # noqa: PLR0913 - mirrors the two API calls' own inputs
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    package_id: UUID,
    *,
    role: str,
    password: str,
) -> None:
    granted = attestation_api.step_up(
        db,
        ctx,
        bank_id,
        package_id,
        StepUpRequest(signing_role=role, password=password),  # pyright: ignore[reportArgumentType]
        _http_request(),
    )
    assert granted.method == "password_reauth"
    attestation_api.certify(
        db,
        ctx,
        bank_id,
        package_id,
        CertifyRequest(
            signing_role=role,  # pyright: ignore[reportArgumentType]
            authorization_token=granted.authorization_token,
            expected_certification_digest=granted.certification_digest,
        ),
    )


@pytest.mark.usefixtures("signing_env")
def test_the_preparer_then_approver_ceremony_still_completes(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control must stop guessing without stopping filing."""
    from tests.services.test_attestation_workspace import (  # noqa: PLC0415
        APPROVER,
        MAKER,
        SAMPLE_BANK_ID,
        _seed,  # pyright: ignore[reportPrivateUsage]
    )
    from tests.storage.inmemory import InMemoryStorageClient  # noqa: PLC0415

    storage = InMemoryStorageClient()
    monkeypatch.setattr(
        "app.services.regulatory_reporting.exports.get_storage_client", lambda: storage
    )
    monkeypatch.setattr(
        "app.services.attestation.artifact_signing.get_storage_client", lambda: storage
    )

    package = _seed(db_session)
    maker = TenantContext(
        organization_id=MAKER.organization_id,
        actor_user_id=MAKER.actor_user_id,
        roles=("approver",),
    )
    checker = TenantContext(
        organization_id=APPROVER.organization_id,
        actor_user_id=APPROVER.actor_user_id,
        roles=("approver",),
    )
    _enrol(db_session, maker, PASSWORD)
    _enrol(db_session, checker, APPROVER_PASSWORD)

    _ceremony(
        db_session,
        maker,
        SAMPLE_BANK_ID,
        package.id,
        role="preparer",
        password=PASSWORD,
    )
    # A wrong guess in the middle of a real ceremony is counted, refused, and
    # leaves the approver's own correct password working afterwards.
    with pytest.raises(stepup.StepUpFailed):
        attestation_api.step_up(
            db_session,
            checker,
            SAMPLE_BANK_ID,
            package.id,
            StepUpRequest(signing_role="approver", password=WRONG),
            _http_request(),
        )
    _ceremony(
        db_session,
        checker,
        SAMPLE_BANK_ID,
        package.id,
        role="approver",
        password=APPROVER_PASSWORD,
    )

    roles = set(
        db_session.scalars(
            select(AttestationSignature.signing_role).where(
                AttestationSignature.package_id == package.id
            )
        )
    )
    assert roles == {"preparer", "approver"}
