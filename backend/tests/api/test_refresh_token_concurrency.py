"""Refresh-token revocation under CONCURRENCY (audit finding D-30).

``tests/api/test_refresh_tokens.py`` pins the lifecycle when one caller acts at a
time. This module pins what happens when two do, because that is where the
defect was: rotation INSERTs a successor while holding ``FOR UPDATE`` on its
ancestor, and revocation is one ``UPDATE … WHERE revoked_at IS NULL``. Under
READ COMMITTED the revoking statement's snapshot predates the successor, so it
blocked on the ancestor, revoked it, and never saw the child — leaving a fully
valid 14-day refresh token behind a password reset that was performed *because*
the credential was believed stolen.

Two levels, deliberately:

* :func:`test_no_refresh_token_is_written_without_first_locking_its_owner` is
  hermetic and runs everywhere. It pins the LOCK ORDER, which is the property
  that makes the race impossible and the one a refactor silently loses.
* :func:`test_a_revocation_racing_an_in_flight_rotation_leaves_no_live_token` and
  :func:`test_a_logout_racing_an_in_flight_rotation_ends_the_whole_session` run
  two real threads on two real Postgres connections and reproduce the exact
  interleaving from the finding — once through the revoker the finding names
  (``set_password``, which has no production caller yet) and once through the one
  that is a live endpoint (``POST /api/v1/auth/logout``). They need
  ``TEST_DATABASE_URL``: SQLite serializes whole write transactions, so the
  interleaving cannot be expressed there at all — a green run on SQLite would
  prove nothing, which is precisely why the original defect survived a 27-test
  suite.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import Engine, event, select
from sqlalchemy.orm import Session

from app.core import security
from app.models import RefreshToken, User
from app.services import authentication
from tests.api.helpers import ORG_1, USER_1

_PASSWORD = "S3cure-Passphrase!"

_POSTGRES_ONLY = pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="A real interleaving needs two Postgres connections (TEST_DATABASE_URL).",
)


def _password_user(db_session: Session) -> User:
    user = db_session.get(User, USER_1)
    assert user is not None
    user.password_hash = security.hash_password(_PASSWORD)
    user.auth_provider = "password"
    user.failed_login_attempts = 0
    user.locked_until = None
    db_session.commit()
    return user


def _live_token_count(session: Session, user: User) -> int:
    return len(
        list(
            session.scalars(
                select(RefreshToken).where(
                    RefreshToken.user_id == user.id,
                    RefreshToken.revoked_at.is_(None),
                )
            )
        )
    )


class _StatementLog:
    """Every statement the engine emits, in order, with the touched table."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def index_of_first(self, *fragments: str) -> int | None:
        for position, statement in enumerate(self.statements):
            if any(fragment in statement for fragment in fragments):
                return position
        return None


@pytest.fixture
def statement_log(db_session: Session) -> Iterator[_StatementLog]:
    log = _StatementLog()
    engine = db_session.get_bind()
    assert isinstance(engine, Engine)

    def record(
        _conn: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        log.statements.append(" ".join(statement.split()))

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield log
    finally:
        event.remove(engine, "before_cursor_execute", record)


def _assert_owner_locked_before_any_token_write(log: _StatementLog, dialect: str) -> None:
    """The invariant, stated as SQL order.

    No row may be written into ``refresh_tokens`` unless a statement against
    ``users`` came first, because that statement is the lock the revoking path
    also takes. Asserting the ORDER rather than the presence of a helper call is
    deliberate: the guarantee lives in the database, so the evidence for it has
    to be what reached the database.
    """
    first_owner = log.index_of_first("FROM users")
    first_token_write = log.index_of_first("INSERT INTO refresh_tokens", "UPDATE refresh_tokens")
    assert first_token_write is not None, "the path under test wrote no refresh_tokens row"
    assert first_owner is not None, "refresh_tokens was written without touching users at all"
    assert first_owner < first_token_write, (
        "a refresh_tokens write ran before its owner row was locked:\n"
        + "\n".join(log.statements)
    )
    if dialect == "postgresql":
        # SQLite's dialect drops the clause (its own write lock serializes the
        # transaction instead), so the strength is only assertable on Postgres.
        assert any(
            "FROM users" in statement and "FOR NO KEY UPDATE" in statement
            for statement in log.statements
        ), "the owner row was read but not locked:\n" + "\n".join(log.statements)


def test_no_refresh_token_is_written_without_first_locking_its_owner(
    db_session: Session,
    statement_log: _StatementLog,
) -> None:
    """Every one of the four paths that writes ``refresh_tokens``.

    Rotation is the one the finding names, but the phantom is symmetric: any
    unlocked INSERT can be missed by any concurrent revoke, and any unlocked
    revoke can miss any concurrent INSERT. So the lock belongs at both
    chokepoints — ``issue_tokens`` and ``_revoke_where`` — and every caller of
    either inherits it.
    """
    dialect = db_session.get_bind().dialect.name
    user = _password_user(db_session)

    # 1. login / SSO login -> issue_tokens (INSERT, no ancestor)
    statement_log.statements.clear()
    issued = authentication.issue_tokens(db_session, user)
    _assert_owner_locked_before_any_token_write(statement_log, dialect)

    # 2. refresh -> lock ancestor, then INSERT successor
    statement_log.statements.clear()
    rotated = authentication.refresh_tokens(db_session, refresh_token=issued.refresh_token)
    _assert_owner_locked_before_any_token_write(statement_log, dialect)

    # 3. logout -> revoke_refresh_family (UPDATE)
    statement_log.statements.clear()
    authentication.logout(db_session, refresh_token=rotated.refresh_token)
    _assert_owner_locked_before_any_token_write(statement_log, dialect)

    # 4. password change / deactivation -> revoke_user_refresh_tokens (UPDATE)
    statement_log.statements.clear()
    authentication.set_password(db_session, user, "Another-Passphrase!2")
    _assert_owner_locked_before_any_token_write(statement_log, dialect)


@_POSTGRES_ONLY
def test_a_revocation_racing_an_in_flight_rotation_leaves_no_live_token(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The finding's interleaving, reproduced rather than reasoned about.

    The rotation is held open after it has taken its locks; the revocation is
    released into that window. Before the fix the revoking UPDATE blocked on the
    ancestor, woke after the rotation committed, and revoked only what its
    original snapshot could see — the successor survived. It now blocks one step
    earlier, on the owner row, so its UPDATE starts *after* the rotation is
    committed and sees both rows.

    The assertion is the end state a user cares about, not the mechanism: after a
    password change, this user holds no usable refresh token. The successor is
    asserted to EXIST as well, so the test cannot pass vacuously by the rotation
    having been refused before the window opened.
    """
    _race_a_revocation_against_an_in_flight_rotation(
        db_session=db_session,
        monkeypatch=monkeypatch,
        revoke=lambda session, user_id, _token: authentication.revoke_user_refresh_tokens(
            session, user_id, reason="password_change"
        ),
    )


@_POSTGRES_ONLY
def test_a_logout_racing_an_in_flight_rotation_ends_the_whole_session(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same race, driven through the revoker that is actually an ENDPOINT.

    The finding names ``set_password`` and ``deactivate_user``. Neither has a
    production caller — the tenant API exposes no password-change and no
    user-deactivation route — so argued from those alone the race is unreachable.
    ``POST /api/v1/auth/logout`` is reachable, unauthenticated by design, and
    revokes through the same ``_revoke_where``. If a rotation in flight (a
    background tab, a retrying client) can slip a successor past it, then signing
    out reports success and leaves a valid 14-day refresh token behind — which is
    precisely what sign-out exists to prevent on a shared machine.

    Reuse-detection revokes through the same chokepoint with the same lock, so
    pinning ``logout`` pins that path's ordering too.
    """
    _race_a_revocation_against_an_in_flight_rotation(
        db_session=db_session,
        monkeypatch=monkeypatch,
        revoke=lambda session, _user_id, token: authentication.logout(session, refresh_token=token),
    )


def _race_a_revocation_against_an_in_flight_rotation(
    *,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    # ``object`` return, not ``None``: the family/user revokers hand back a row
    # count and ``logout`` hands back nothing. The helper ignores either.
    revoke: Callable[[Session, UUID, str], object],
) -> None:
    """Hold a rotation open after it has taken its locks, release ``revoke`` into
    that window, and assert the end state rather than the mechanism.

    Before the fix the revoking statement blocked on the ancestor the rotation
    held, woke after the rotation committed, and revoked only what its original
    snapshot could see — the successor survived. It now blocks one step earlier,
    on the owner row, so its UPDATE starts *after* the rotation is committed and
    sees both rows.

    The successor is asserted to EXIST as well, so no caller can pass vacuously
    by the rotation having been refused before the window ever opened.
    """
    engine = db_session.get_bind()
    assert isinstance(engine, Engine)
    user = _password_user(db_session)
    user_id = user.id
    issued = authentication.issue_tokens(db_session, user)
    db_session.expire_all()

    rotation_holds_its_locks = threading.Event()
    revocation_released = threading.Event()
    original_lock = authentication._lock_refresh_token  # noqa: SLF001 - the pause point

    def paused_lock(db: Session, token_id: Any) -> Any:
        record = original_lock(db, token_id)
        rotation_holds_its_locks.set()
        revocation_released.wait(timeout=15)
        # The event only says the revoking thread has been let go; this gives its
        # statement time to actually reach the server and block.
        time.sleep(0.5)
        return record

    monkeypatch.setattr(authentication, "_lock_refresh_token", paused_lock)

    outcome: dict[str, Any] = {}

    def rotate() -> None:
        with Session(engine) as session:
            try:
                outcome["rotated"] = authentication.refresh_tokens(
                    session, refresh_token=issued.refresh_token
                )
            except HTTPException as exc:  # the other ordering: revocation won
                outcome["refused"] = exc.status_code

    def revoke_in_its_own_session() -> None:
        assert rotation_holds_its_locks.wait(timeout=15)
        with Session(engine) as session:
            revocation_released.set()
            revoke(session, user_id, issued.refresh_token)

    rotating = threading.Thread(target=rotate, name="rotate")
    revoking = threading.Thread(target=revoke_in_its_own_session, name="revoke")
    rotating.start()
    revoking.start()
    for thread in (rotating, revoking):
        thread.join(timeout=30)
        assert not thread.is_alive(), f"{thread.name} never finished — the locks deadlocked"

    with Session(engine) as verifier:
        verifier.info["organization_id"] = ORG_1
        all_rows = list(
            verifier.scalars(select(RefreshToken).where(RefreshToken.user_id == user_id))
        )
        assert len(all_rows) == 2, "the successor was never written; the race was not exercised"
        assert _live_token_count(verifier, user) == 0

    # And the token the racing client walked away with is dead on presentation.
    rotated = outcome.get("rotated")
    if rotated is not None:
        with Session(engine) as session, pytest.raises(HTTPException) as refusal:
            authentication.refresh_tokens(session, refresh_token=rotated.refresh_token)
        assert refusal.value.status_code == 401
