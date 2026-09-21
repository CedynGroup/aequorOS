"""Which AI surfaces exist, and which a tenant has consented to."""

from __future__ import annotations

from typing import Literal, get_args

#: Every AI surface a tenant can switch on independently. Adding one means
#: adding it here, to the consent text, and to the settings panel — a tenant
#: consents to a NAMED use, never to "AI" in general.
AiFeature = Literal["icaap_drafting", "bi_commentary"]
AI_FEATURES: tuple[AiFeature, ...] = get_args(AiFeature)


def is_feature(value: str) -> bool:
    return value in AI_FEATURES


__all__ = ["AI_FEATURES", "AiFeature", "is_feature"]
