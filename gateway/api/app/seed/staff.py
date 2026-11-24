"""Sign synthetic staff in for the seed.

In ``local`` and ``test`` this goes through the identity provider's real token path
(mint, validate, record) with a directly minted token: synthetic staff have no account
and cannot sign in through ``/idp``. In ``sandbox`` the seed records the same synthetic
identities directly.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from sqlalchemy.orm import Session

from app.auth.staff import IdTokenIdentity, StaffPrincipal, record_staff, validate_id_token
from app.core.context import AppContext
from app.idp.tokens import IdpKeyResolver, mint_id_token
from app.seed.synthetic_staff import find_dev_staff


def sign_in(session: Session, ctx: AppContext, subject: str) -> StaffPrincipal:
    staff = find_dev_staff(subject)
    if staff is None:
        raise KeyError(subject)
    if ctx.settings.is_dev:
        token = mint_id_token(ctx, staff, groups=staff.groups, mfa=staff.mfa)
        identity = validate_id_token(
            token, settings=ctx.settings, now=ctx.clock.now(), resolver=IdpKeyResolver(ctx.keys)
        )
        return record_staff(session, ctx, identity, token)
    identity = IdTokenIdentity(
        subject=staff.subject,
        name=staff.name,
        given_name=staff.given_name,
        family_name=staff.family_name,
        email=staff.email,
        groups=staff.groups,
        mfa=True,
        expires_at=ctx.clock.now() + timedelta(hours=1),
    )
    return record_staff(session, ctx, identity, "seed-" + secrets.token_hex(16))
