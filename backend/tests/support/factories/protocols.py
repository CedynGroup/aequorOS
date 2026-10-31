from __future__ import annotations

from typing import Protocol

from app.case.schemas.cases import CaseRead
from app.models import RiskCase


class CreatesApiCase(Protocol):
    def create(self) -> CaseRead: ...


class CreatesServiceCase(Protocol):
    def create(self) -> RiskCase: ...
