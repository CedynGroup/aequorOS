"""Ai's interface for other features: re-exports only, no logic."""

from app.ai.service import client, gates, grounding, observability, pseudonymise, quota
from app.ai.service.client import ModelRequest, ModelResult, SystemBlock
from app.ai.service.features import AiFeature

__all__ = [
    "AiFeature",
    "ModelRequest",
    "ModelResult",
    "SystemBlock",
    "client",
    "gates",
    "grounding",
    "observability",
    "pseudonymise",
    "quota",
]
