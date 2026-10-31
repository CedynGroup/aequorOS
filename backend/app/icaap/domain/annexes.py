"""Resolved current-annex inputs to frozen-report validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class AnnexKey:
    organization_id: str
    bank_id: str
    return_code: str
    reporting_date: date
    basis: str


@dataclass(frozen=True)
class AnnexVersion:
    package_id: UUID
    version: int
    content_digest: str | None


class CurrentAnnexRepository(Protocol):
    def current(self, key: AnnexKey) -> AnnexVersion | None: ...
