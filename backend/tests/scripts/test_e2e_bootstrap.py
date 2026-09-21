"""The hermetic e2e fixture users are distinct identities, in both halves.

`scripts/e2e_bootstrap.py` creates one `users` row per fixture and enrols a
signing key for each; `dashboard/e2e/support/mint.ts` mints tokens for the same
identities by id. Two names sharing a UUID collapse to one row, the second key
enrolment refuses, and the Playwright stack cannot boot at all.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from scripts.e2e_bootstrap import E2E_USERS

_MINT_TS = Path(__file__).resolve().parents[2] / "dashboard" / "e2e" / "support" / "mint.ts"
_MINT_USER = re.compile(r"^  (\w+): \{\n    id: \"([0-9a-f-]{36})\",", re.MULTILINE)


def test_every_fixture_user_has_its_own_uuid() -> None:
    duplicated = {
        user_id: [name for name, candidate in E2E_USERS.items() if candidate == user_id]
        for user_id, count in Counter(E2E_USERS.values()).items()
        if count > 1
    }
    assert not duplicated, f"fixture users sharing a UUID: {duplicated}"


def test_token_mint_mirrors_the_bootstrap_identities() -> None:
    minted = {name: user_id for name, user_id in _MINT_USER.findall(_MINT_TS.read_text())}
    assert minted == {name: str(user_id) for name, user_id in E2E_USERS.items()}
