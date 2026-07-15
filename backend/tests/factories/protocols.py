from __future__ import annotations

from typing import Protocol

from app.models import RiskCase
from app.schemas.cases import CaseRead


class CreatesApiCase(Protocol):
    def create(self) -> CaseRead: ...


class CreatesServiceCase(Protocol):
    def create(self) -> RiskCase: ...
