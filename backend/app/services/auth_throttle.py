"""Shared brute-force throttle for every path that re-checks a password.

Password *login* has always been throttled (``app/services/authentication.py``).
Signing **step-up** was not: it called :func:`app.core.security.verify_password`
directly and wrote nothing back, so an authenticated analyst could guess an
approver's password without limit against the one endpoint that mints a signing
authorisation — the control standing between them and a legally binding
regulatory attestation (audit finding P0-4).

This module is the primitive every such path shares. It deliberately owns no
policy of its own: the thresholds are ``AuthSettings.max_failed_logins`` /
``AuthSettings.lockout_seconds``, and the state is the SAME two ``users``
columns login already uses. That is the point — a lockout earned by guessing at
the sign-in page also blocks signing, and one earned at the signing dialog also
blocks sign-in. Two counters would have meant an attacker gets ``max_failed``
guesses per surface.

**Not only tenant users (audit finding D-25).** The staff control plane guards
cross-tenant BYPASSRLS access and had a WEAKER control than this one: an
in-process dict keyed on ``(email, client-ip)``, which a rotating source
address resets, more than one worker multiplies, and a deploy erases. It now
runs on the same durable primitive, over ``operator_users``' own two columns —
see :func:`record_failure_for`, :func:`clear_failures` and
``app/operator/services/operator_auth.py``. The principal model is a parameter,
never a second implementation: one lockout rule, one backoff curve, one
``auth_anomaly`` emission, whichever table the account lives in.

**Why the database rather than a process-global dict.** The API runs
``uvicorn --workers 4`` behind a load balancer, and the deployment scales to
more than one replica. An in-process counter would give a brute-forcer
``max_failed × workers × replicas`` attempts and would reset on every deploy.
``users.failed_login_attempts`` / ``users.locked_until`` are durable, shared by
every worker and replica, and survive restarts. The increment is issued as an
atomic ``SET failed_login_attempts = failed_login_attempts + 1`` in SQL — never
a read-modify-write in Python — so two workers racing on the same account
cannot lose a count between them.

**Progressive.** Each lockout beyond the threshold doubles, capped at
:data:`MAX_LOCKOUT_SECONDS` so a user can never lock themselves out
indefinitely and an attacker cannot convert the control into a permanent denial
of an officer's ability to file.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from functools import lru_cache
from typing import Any, Protocol

from sqlalchemy import update
from sqlalchemy.orm import Mapped, Session
from sqlalchemy.sql.elements import ColumnElement

from app.core import security
from app.core.config import AuthSettings, get_settings
from app.core.observability import auth_anomaly
from app.db.base import utc_now
from app.models import User

#: Ceiling on the progressive backoff. A filing deadline is a real thing: an
#: unbounded doubling would let five wrong keystrokes cost an officer a day.
MAX_LOCKOUT_SECONDS = 3600
#: Bound on the doubling exponent, so the multiplication cannot overflow into an
#: absurd number before the cap is applied.
_MAX_DOUBLINGS = 12


class ThrottledPrincipal(Protocol):
    """Any account row carrying the two durable throttle columns.

    ``users`` and ``operator_users`` both satisfy it. Structural, not a base
    class: the tenant and staff identity tables are deliberately unrelated
    (staff_UI.md §1) and must stay that way — only the throttle STATE is
    shared, never the identity.

    The members are typed ``Mapped[...]``, not bare ``int`` / ``datetime |
    None``, because that is what a declarative model actually declares. A
    protocol member that can be WRITTEN is matched invariantly, so the bare
    form rejected every real caller (``Mapped[int]`` is not ``int``) while
    still reading as ``int`` at every use site here — the descriptor is
    applied on access exactly as it is on the model itself.
    """

    failed_login_attempts: Mapped[int]
    locked_until: Mapped[dt.datetime | None]


def _as_aware(value: dt.datetime) -> dt.datetime:
    """SQLite hands back naive datetimes; comparisons must not raise on them."""
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)


def lock_expiry(user: ThrottledPrincipal, *, now: dt.datetime | None = None) -> dt.datetime | None:
    """When the current lockout ends, or ``None`` if the account is not locked."""
    if user.locked_until is None:
        return None
    expiry = _as_aware(user.locked_until)
    return expiry if expiry > (now or utc_now()) else None


def is_locked(user: ThrottledPrincipal, *, now: dt.datetime | None = None) -> bool:
    return lock_expiry(user, now=now) is not None


def minutes_remaining(expiry: dt.datetime, *, now: dt.datetime | None = None) -> int:
    """Whole minutes until ``expiry``, at least 1 — copy for the operator."""
    seconds = (expiry - (now or utc_now())).total_seconds()
    return max(1, int(seconds // 60) + (1 if seconds % 60 else 0))


def _backoff_seconds(attempts: int, settings: AuthSettings) -> int:
    excess = max(0, attempts - settings.max_failed_logins)
    scaled = settings.lockout_seconds * (2 ** min(excess, _MAX_DOUBLINGS))
    return min(scaled, MAX_LOCKOUT_SECONDS)


def record_failure_for(  # noqa: PLR0913 - the principal table is a parameter, not a second implementation
    db: Session,
    principal: ThrottledPrincipal,
    *,
    model: type[Any],
    identity: Sequence[ColumnElement[bool]],
    anomaly_fields: dict[str, Any],
    settings: AuthSettings,
    now: dt.datetime | None = None,
) -> dt.datetime | None:
    """Count one wrong password against ANY principal table and lock at threshold.

    ``model`` + ``identity`` say which row to increment (the caller owns the
    scoping — a tenant user is keyed by id AND organization, a staff account by
    id alone); ``anomaly_fields`` are the identifiers carried into the
    :func:`auth_anomaly` emission. Never pass an email: an account is named by
    id in an operational log, never by the credential it signs in with.

    Commits, because the caller is about to raise: a failure the transaction
    rolls back is a failure that never happened, which is exactly the hole this
    closes. Returns the lock expiry when this attempt was the one that locked
    the account (or extended an existing lock), otherwise ``None``.
    """
    moment = now or utc_now()
    # Atomic in SQL: concurrent workers on the same account both count.
    db.execute(
        update(model)
        .where(*identity)
        .values(failed_login_attempts=model.failed_login_attempts + 1)
        .execution_options(synchronize_session=False)
    )
    # Re-read what the database now holds rather than what this process last saw.
    db.expire(principal, ["failed_login_attempts", "locked_until"])
    expiry: dt.datetime | None = None
    if principal.failed_login_attempts >= settings.max_failed_logins:
        expiry = moment + dt.timedelta(
            seconds=_backoff_seconds(principal.failed_login_attempts, settings)
        )
        principal.locked_until = expiry
    db.commit()
    # The columns above are durable state, not a signal: nothing watches them,
    # so a credential-stuffing run against one account looked identical to
    # silence. Report the lockout — and only the lockout, so ordinary typos do
    # not drown it — with the account identified by id, never by email.
    if expiry is not None:
        auth_anomaly(
            reason="account_locked_after_repeated_failures",
            failed_attempts=principal.failed_login_attempts,
            locked_until=expiry.isoformat(),
            **anomaly_fields,
        )
    return expiry


def record_failure(
    db: Session,
    user: User,
    *,
    now: dt.datetime | None = None,
    settings: AuthSettings | None = None,
) -> dt.datetime | None:
    """Count one wrong password against a TENANT user (login and signing step-up)."""
    return record_failure_for(
        db,
        user,
        model=User,
        identity=(User.id == user.id, User.organization_id == user.organization_id),
        anomaly_fields={
            "organization_id": str(user.organization_id),
            "user_id": str(user.id),
        },
        settings=settings or get_settings().auth,
        now=now,
    )


def clear_failures(principal: ThrottledPrincipal) -> None:
    """Zero the durable throttle state on ANY principal row (no commit)."""
    principal.failed_login_attempts = 0
    principal.locked_until = None


def record_success(db: Session, user: User, *, commit: bool = True) -> None:
    """Clear the throttle after a proven password. Mirrors the login path.

    Deliberately does NOT stamp ``last_login_at``: a step-up is a signature
    ceremony, not a sign-in, and the login history must not claim otherwise.
    """
    clear_failures(user)
    if commit:
        db.commit()


@lru_cache(maxsize=1)
def _equalizer_hash() -> str:
    """A real Argon2id hash of an unguessable constant.

    Verifying a candidate against it burns the same work as a real check, so
    "this account has no password" is not observable from response timing.
    Lazy so the ~100 ms hashing cost is not paid at import by every process.
    """
    return security.hash_password("aequoros-step-up-timing-equalizer")


def burn_password_check(password: str) -> None:
    """Spend one Argon2id verification that can never succeed."""
    security.verify_password(password, _equalizer_hash())
