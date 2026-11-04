"""Resolved current-annex answers after authorized data acquisition.

This fake does not select rows or implement a database/session/transaction API.
Ordering, SQL scoping, RLS, locking and persistence need the real adapter.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType

import pytest

from app.icaap.domain.annexes import AnnexKey, AnnexVersion


class InMemoryCurrentAnnexRepository:
    def __init__(self, records: Iterable[tuple[AnnexKey, AnnexVersion]]) -> None:
        versions: dict[AnnexKey, AnnexVersion] = {}
        for key, version in records:
            if key in versions:
                raise ValueError(f"Duplicate resolved current annex: {key}")
            versions[key] = version
        self._versions: Mapping[AnnexKey, AnnexVersion] = MappingProxyType(versions)

    def current(self, key: AnnexKey) -> AnnexVersion | None:
        return self._versions.get(key)


@pytest.fixture
def current_annex_repository() -> type[InMemoryCurrentAnnexRepository]:
    """Use this fixture so protected database contracts can reject the fake."""
    return InMemoryCurrentAnnexRepository
