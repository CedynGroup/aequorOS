"""Refresh-token lifecycle: rotation, reuse detection, revocation (P0-5).

Before migration ``202608220028`` a refresh token was a bare 14-day JWT: no
``jti``, no server-side state, no way to end a session short of deactivating the
account. These tests pin the replacement — one refresh per token, a new token on
every refresh, a revoked family the moment a retired token is replayed, and an
outright revoke on logout / password change / deactivation.

Everything here runs hermetically (``db_client``): login, refresh and logout use
the cross-tenant system session, which falls back to ``DATABASE_URL`` when
``WORKER_DATABASE_URL`` is blank — exactly the suite's configuration.

This module's other-suite cases live in ``test_refresh_tokens_integration.py``.
"""

from __future__ import annotations

import pytest

from app.core import security
from app.core.config import get_settings
from tests.support.helpers import ORG_1, USER_1


def test_create_token_refuses_to_mint_a_refresh_token_without_a_jti() -> None:
    with pytest.raises(ValueError, match="must carry a jti"):
        security.create_token(
            subject=USER_1,
            organization_id=ORG_1,
            roles=["admin"],
            authorization_version=1,
            token_type="refresh",
            settings=get_settings().auth,
        )
