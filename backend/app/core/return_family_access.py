"""Shared return-family classification for authorization and sealed storage."""

from dataclasses import dataclass
from typing import Final

from app.core.authorization import Module, Sensitivity


@dataclass(frozen=True)
class FamilyGate:
    """The authority one return family is read and written under."""

    module: Module
    sensitivity: Sensitivity


GATED: Final[dict[str, FamilyGate]] = {
    "icaap": FamilyGate(module=Module.CAPITAL, sensitivity=Sensitivity.CONFIDENTIAL),
}
RETURN_GATE: Final[FamilyGate] = FamilyGate(
    module=Module.REGULATORY, sensitivity=Sensitivity.RESTRICTED
)


def gate_for(family: str | None) -> FamilyGate:
    return GATED.get(family or "", RETURN_GATE)
