"""Which model each FEATURE calls, and which features may let that float (D-061).

The code has never capped a model — the ids are settings, so changing one is an
env edit plus an approval entry, not a deploy. What D-061 adds is that "whatever
is newest" is the wrong default for some surfaces and a reasonable one for
others, and the difference is not quality:

* **An ICAAP narrative rides a FILED regulatory document.** If the model floats,
  identical inputs produce different text while the provenance stamped into that
  document's evidence describes something that changed underneath it. Capability
  changes matter as much as quality ones — structured-output support, thinking
  parameters, context limits — so a silent upgrade can remove the exact feature
  the grounding validator depends on. ICAAP therefore PINS.
* **BI commentary is advisory and regenerable.** Nothing is filed, so tracking a
  vendor's floating alias costs nothing a reader cannot simply regenerate.

Resolution is three steps and stops at the first answer:

    feature override (``AI_FEATURE_MODELS``) -> vendor default -> unset

so a deployment that has only ever set ``AI_MODEL`` / ``AI_OPENAI_MODEL`` /
``AI_GOOGLE_MODEL`` behaves exactly as it did before this module existed.

Two rules this module enforces, both as NAMED vendor skips rather than crashes,
so a misconfigured feature degrades the way every other configuration problem in
the tier does:

* a ``pinned`` feature may not resolve to a floating id (``model_not_pinned``);
* a floating id may not be aimed at a vendor that publishes no such form
  (``model_alias_unsupported``) — that would be the platform inventing an alias,
  and it would 404 in production instead of being caught here.

The platform never writes an alias STRING of its own. ``is_floating_id`` is a
shape test over what the OPERATOR configured, and
:data:`VENDORS_PUBLISHING_FLOATING_IDS` only records for which vendors pointing
at one is a published idea at all.

The real pin, in the end, is the approval key: it contains the exact RESOLVED id,
so an operator who changes a model falls out of approval and is refused until the
new configuration has been reviewed. This module makes sure the id the key is
looked up with is the id that will actually be sent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from app.core.config import AI_VENDORS, AiVendor, Settings, get_settings
from app.services.ai.features import AI_FEATURES, AiFeature
from app.services.ai.vendors import VendorUnavailable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Iterator

ModelPolicy = Literal["pinned", "may_float"]
ModelSource = Literal["feature_override", "vendor_default"]

#: Per-feature pinning policy. Exhaustive over ``AiFeature`` by test, so a new
#: surface cannot be added without someone deciding whether its model may move.
FEATURE_MODEL_POLICY: dict[AiFeature, ModelPolicy] = {
    "icaap_drafting": "pinned",
    "bi_commentary": "may_float",
    # Translating a question into a catalogue query is not a filed artifact and
    # carries no bank figure, so a newer snapshot of the same family is an
    # improvement rather than a change to something governed. It floats for the
    # same reason commentary does.
    "bi_nlq": "may_float",
}

#: What an operator-configured FLOATING id looks like. A shape test, not a list
#: of alias strings: the platform must never invent one, and every vendor that
#: publishes this idea spells it with this suffix.
FLOATING_ID_SUFFIX = "-latest"

#: Vendors for which "point me at the latest of this family" is a PUBLISHED id
#: form. OpenAI and Google both publish suffixed aliases that resolve to the
#: current snapshot. Anthropic publishes undated GENERATION ids (an alias within
#: one model generation) but no cross-generation "latest", so a feature that may
#: float simply takes the pinned default there — the alternative would be
#: inventing an id the vendor does not serve.
VENDORS_PUBLISHING_FLOATING_IDS: frozenset[str] = frozenset({"openai", "google"})


class AiModelConfigurationError(ValueError):
    """``AI_FEATURE_MODELS`` names something that does not exist.

    A programming/deployment fault rather than a runtime state: the vendor and the
    grammar are already validated at boot by ``AiSettings``, so reaching this means
    the FEATURE name is wrong, and no feature would ever pick up the override.
    """


@dataclass(frozen=True)
class ResolvedModel:
    """One (feature, vendor) slot, resolved."""

    feature: AiFeature | None
    vendor: AiVendor
    model: str
    source: ModelSource
    policy: ModelPolicy | None
    floating: bool


def _vendor_default(vendor: str, settings: Settings) -> str:
    ai = settings.ai
    defaults: dict[str, str] = {
        "anthropic": ai.model,
        "openai": ai.openai_model,
        "google": ai.google_model,
    }
    return (defaults.get(vendor) or "").strip()


def is_floating_id(model: str) -> bool:
    """Does this id mean "whatever is newest" rather than one snapshot?"""
    return model.strip().casefold().endswith(FLOATING_ID_SUFFIX)


def policy_for(feature: AiFeature | None) -> ModelPolicy | None:
    """``None`` for a featureless question (the run gate asking "any model at
    all"), which is not a place a pinning rule can be applied."""
    if feature is None:
        return None
    try:
        return FEATURE_MODEL_POLICY[feature]
    except KeyError as missing:  # pragma: no cover - the exhaustiveness test pins this
        message = f"no model policy is declared for AI feature {feature!r}"
        raise AiModelConfigurationError(message) from missing


def _overrides(settings: Settings) -> dict[tuple[str, str], str]:
    overrides = settings.ai.feature_model_overrides
    unknown = sorted({feature for feature, _vendor in overrides if feature not in AI_FEATURES})
    if unknown:
        message = (
            f"AI_FEATURE_MODELS names unknown AI feature(s) {unknown}; "
            f"permitted values are {list(AI_FEATURES)}."
        )
        raise AiModelConfigurationError(message)
    return overrides


def resolve(
    feature: AiFeature | None, vendor: AiVendor, settings: Settings | None = None
) -> ResolvedModel:
    """The model ``vendor`` will be asked for on behalf of ``feature``.

    Raises :class:`~app.services.ai.vendors.VendorUnavailable` — which the tier
    records against that vendor and steps over — when the slot cannot be served:
    nothing configured, a pinned feature pointed at a floating id, or a floating
    id pointed at a vendor that publishes none.
    """
    settings = settings or get_settings()
    try:
        overrides = _overrides(settings)
    except AiModelConfigurationError as invalid:
        raise VendorUnavailable(vendor, "model_override_invalid") from invalid

    override = overrides.get((feature, vendor)) if feature is not None else None
    model = (override or "").strip() or _vendor_default(vendor, settings)
    if not model:
        raise VendorUnavailable(vendor, "model_unconfigured")

    floating = is_floating_id(model)
    policy = policy_for(feature)
    if floating and policy == "pinned":
        raise VendorUnavailable(vendor, "model_not_pinned")
    if floating and vendor not in VENDORS_PUBLISHING_FLOATING_IDS:
        raise VendorUnavailable(vendor, "model_alias_unsupported")

    return ResolvedModel(
        feature=feature,
        vendor=vendor,
        model=model,
        source="feature_override" if override else "vendor_default",
        policy=policy,
        floating=floating,
    )


def resolve_model(
    feature: AiFeature | None, vendor: AiVendor, settings: Settings | None = None
) -> str:
    """:func:`resolve`, when only the id is wanted."""
    return resolve(feature, vendor, settings).model


def matrix(settings: Settings | None = None) -> Iterator[ResolvedModel | VendorUnavailable]:
    """Every (feature, vendor) slot this deployment could call, resolved.

    The shape a preflight check wants: it has to verify the id that will ACTUALLY
    be sent per feature, not only the vendor default, because an override changes
    it. Unresolvable slots are yielded as the refusal rather than raised, so one
    misconfigured slot does not hide the rest.
    """
    settings = settings or get_settings()
    for feature in AI_FEATURES:
        for vendor in AI_VENDORS:
            try:
                yield resolve(feature, vendor, settings)
            except VendorUnavailable as unavailable:
                yield unavailable


__all__ = [
    "FEATURE_MODEL_POLICY",
    "FLOATING_ID_SUFFIX",
    "VENDORS_PUBLISHING_FLOATING_IDS",
    "AiModelConfigurationError",
    "ModelPolicy",
    "ModelSource",
    "ResolvedModel",
    "is_floating_id",
    "matrix",
    "policy_for",
    "resolve",
    "resolve_model",
]
