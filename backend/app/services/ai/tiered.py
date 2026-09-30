"""The tier: walk ``AI_PROVIDER_TIER`` until a vendor answers (D-053).

Purpose is availability, and only availability. Losing credit on one vendor must
not stop AI features, so a vendor that cannot serve the request right now is
stepped over. A vendor that DID answer is never stepped over, however unwelcome
the answer: a refusal, a truncated output, output that does not fit the schema or
(later, in the feature) a draft that fails grounding all stop here and go to the
feature's own deterministic fallback. One bad draft must not stampede three
providers, and it must not spend a bank's quota three times to fail the same way.
:mod:`app.services.ai.vendors` is where that line is drawn, by failure code.

Four things this module is careful about:

* **Adapters are imported LAZILY, by name.** Nothing here imports an SDK or an
  HTTP client, so the API process and the core worker still load none of them,
  and the architecture test still finds each vendor's transport in exactly one
  module.
* **A vendor is prepared before it is built.** ``prepare`` answers "could this
  vendor serve at all" — key present, model named, endpoint permitted, this
  configuration approved for this environment — without opening a socket, so a
  skip is a named reason rather than an exception caught somewhere deep.
* **A programming error is never a failover.** Only
  :class:`~app.services.ai.vendors.VendorUnavailable` is caught. The test
  suite's refusal to construct a real client, a missing adapter attribute or a
  bug in a request builder all propagate, because a tier that swallowed them
  would report "every vendor was down" for what is actually our own fault.
* **Provenance is stamped here, not by the caller.** The vendor names itself; the
  tier adds its position and the record of everything tried before it.
* **The model is resolved PER FEATURE** (D-061, ``model_selection``), and the
  approval lookup below uses that RESOLVED id — so a feature override cannot
  route around ``approved_configurations.json`` by differing from the vendor
  default that was reviewed.
"""

from __future__ import annotations

import importlib
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from app.core.config import Settings, get_settings, is_undeployed_environment
from app.services.ai import approvals
from app.services.ai.client import ModelRequest, ModelResult
from app.services.ai.vendors import VendorDescriptor, VendorUnavailable, advances, failure_class

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Callable, Mapping

    from pydantic import BaseModel

    from app.services.ai.client import DraftModel
    from app.services.ai.features import AiFeature

#: vendor -> the module that owns its transport and its credential class. The one
#: place a vendor name is bound to code; ``AI_PROVIDER_TIER`` validates against
#: the same vocabulary in ``core.config``.
ADAPTER_MODULES: dict[str, str] = {
    "anthropic": "app.services.ai.client",
    "openai": "app.services.ai.openai_model",
    "google": "app.services.ai.google_model",
}

#: What ``prepare`` returns: what the vendor WOULD send, and how to build it.
type Prepared = tuple[VendorDescriptor, "Callable[[], DraftModel]"]
type PrepareFn = "Callable[[Settings], Prepared]"


def _prepare(vendor: str, settings: Settings, feature: AiFeature | None) -> Prepared:
    module = importlib.import_module(ADAPTER_MODULES[vendor])
    return module.prepare(settings, feature)


def describe(
    vendor: str, settings: Settings | None = None, feature: AiFeature | None = None
) -> VendorDescriptor | None:
    """What ``vendor`` would send, WITHOUT reading its credential.

    The enqueue gate needs this: it must decide whether any vendor's exact
    configuration is approved for this environment, and it runs in the API
    process, which must not parse a model key (see ``AiCredentialSettings``).
    ``feature`` selects the model (D-061), so the gate asks about the id that will
    actually be sent. ``None`` when the vendor cannot even be described — no model
    configured, a pinned feature aimed at a floating id — which the gate treats
    the same as unapproved, because it could not be called either.
    """
    settings = settings or get_settings()
    module = importlib.import_module(ADAPTER_MODULES[vendor])
    try:
        return module.describe(settings, feature)
    except VendorUnavailable:
        return None


def _attempt(vendor: str, position: int, outcome: str, code: str | None) -> dict[str, Any]:
    return {
        "vendor": vendor,
        "tier_position": position,
        "outcome": outcome,
        "failure_code": code,
        "failure_class": failure_class(code),
    }


class TieredModel:
    """A ``DraftModel`` that is really the ordered list of them."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        prepares: Mapping[str, PrepareFn] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        #: Test/eval seam. Product code never passes this: the real adapters are
        #: resolved lazily from :data:`ADAPTER_MODULES`.
        self._prepares = prepares

    def _prepare_vendor(self, vendor: str, feature: AiFeature | None) -> Prepared:
        if self._prepares is not None:
            prepare = self._prepares.get(vendor)
            if prepare is None:
                raise VendorUnavailable(vendor, "key_missing")
            return prepare(self._settings)
        return _prepare(vendor, self._settings, feature)

    def _approved(self, request: ModelRequest[Any], descriptor: VendorDescriptor) -> bool:
        """Is THIS vendor's configuration approved for this environment?

        Six dimensions, exact match, no wildcard — the same rule the single-vendor
        gate applied, with the vendor added. Local and test are unaffected; a
        deployed environment with no entry for a vendor skips that vendor, which
        is how "Gemini has not been reviewed yet" stops being a note in a
        handover and becomes something the tier enforces.
        """
        settings = self._settings
        if is_undeployed_environment(settings.app.app_env):
            return True
        return (
            approvals.find(
                feature=request.feature,
                prompt_version=request.prompt_version,
                vendor=descriptor.vendor,
                model=descriptor.model,
                effort=descriptor.effort,
                app_env=settings.app.app_env,
            )
            is not None
        )

    def generate[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        attempts: list[dict[str, Any]] = []
        last: ModelResult[T] | None = None

        for position, vendor in enumerate(self._settings.ai.provider_order, start=1):
            try:
                descriptor, build = self._prepare_vendor(vendor, request.feature)
            except VendorUnavailable as skip:
                attempts.append(_attempt(vendor, position, "skipped", skip.code))
                continue
            if not self._approved(request, descriptor):
                attempts.append(_attempt(vendor, position, "skipped", "not_approved"))
                continue

            result = replace(
                build().generate(request),
                tier_position=position,
                tier_attempts=tuple(attempts),
            )
            if result.vendor is None:  # pragma: no cover - every adapter names itself
                result = replace(
                    result,
                    vendor=descriptor.vendor,
                    degraded=descriptor.degraded,
                    model_source=descriptor.model_source,
                )
            if not advances(result):
                return result
            attempts.append(_attempt(vendor, position, result.outcome, result.failure_code))
            last = result

        if last is not None:
            # Every vendor was tried and every one was unavailable. The LAST
            # outcome is the one the user sees, because it is a true statement
            # about the attempt, with the whole walk recorded beside it.
            return replace(last, tier_attempts=tuple(attempts))
        return ModelResult(
            outcome="failed",
            model_requested=self._settings.ai.model,
            failure_code="no_vendor_available",
            tier_attempts=tuple(attempts),
        )


def configured_vendors(
    settings: Settings | None = None, feature: AiFeature | None = None
) -> tuple[str, ...]:
    """Vendors in the tier that could be prepared right now, in tier order.

    Answers the run gate's "can this process call a model at all" without
    constructing a client or opening a socket. ``feature`` is optional because
    that question is asked once per process, not per request; unset resolves the
    vendor defaults, which is the widest honest answer.
    """
    settings = settings or get_settings()
    usable: list[str] = []
    for vendor in settings.ai.provider_order:
        try:
            _prepare(vendor, settings, feature)
        except VendorUnavailable:
            continue
        usable.append(vendor)
    return tuple(usable)


__all__ = ["ADAPTER_MODULES", "TieredModel", "configured_vendors", "describe"]
