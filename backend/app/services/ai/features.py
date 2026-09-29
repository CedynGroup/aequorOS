"""Which AI surfaces exist, and which a tenant can actually consent to."""

from __future__ import annotations

from typing import Literal, get_args

#: Every AI surface a tenant can switch on independently. Adding one means
#: adding it here, to the consent text, and to the settings panel — a tenant
#: consents to a NAMED use, never to "AI" in general.
AiFeature = Literal["icaap_drafting", "bi_commentary", "bi_nlq"]
AI_FEATURES: tuple[AiFeature, ...] = get_args(AiFeature)

#: The surfaces the CURRENT consent text actually describes, and therefore the
#: only ones a tenant can enable. A surface may exist in code and be absent here.
#:
#: ``bi_nlq`` is the surface that is absent, and deliberately (audit A11-F2).
#: ``consent/ai-consent-2026-09-v1.md`` promises, in its own words, that no
#: "name, title, email address or identifier of any individual — staff, officer,
#: director, shareholder or customer" is ever sent, and that monetary amounts and
#: dates are "replaced by placeholders". A natural-language question is a sentence
#: a person typed, and a question like "the exposure to <a customer> at <a date>
#: above <an amount>" contains all three. The screening applied before a question
#: is sent refuses control characters, template braces, emails, URLs and every name
#: in the tenant's institution, related-party and user registers — but NOT the
#: counterparty register, so a customer's name in a question is not caught.
#:
#: So the promise and the surface could not both stand, and the resolution was a
#: product decision rather than an engineering one. **It was taken on 2026-09-29:
#: the consent text was amended.** ``ai-consent-2026-09-v2`` describes the question
#: surface in its own section — what is sent (the typed question plus a catalogue
#: of permitted figure NAMES), what is not (any figure, any answer, anything the
#: asker may not see), and, stated plainly rather than buried, that the screening
#: cannot catch a customer name it has never been told. Raising the version
#: switches AI assistance off for every organisation until an Owner accepts the
#: new text, which is the mechanism working rather than a cost of it.
#:
#: The rule this list enforces is unchanged and still load-bearing: the platform
#: refuses to let a tenant consent to something its own consent document does not
#: describe. Adding a feature here without a consent section that covers it is the
#: defect; ``gates.evaluate`` enforces it at both enqueue and run, so a database
#: row cannot out-rank the document.
#:
#: This was previously believed to "hold by construction" because no settings
#: panel exists in the dashboard yet. That is not a gate: the API accepts the
#: field, and a tenant that had accepted this version for ICAAP drafting could
#: have added ``bi_nlq`` under it with no new consent at all. A promise to a
#: customer needs an enforcement, not an accident.
CONSENT_COVERED_FEATURES: tuple[AiFeature, ...] = ("icaap_drafting", "bi_commentary", "bi_nlq")

#: Named so the refusal can say WHY rather than only that it refused.
CONSENT_PENDING_FEATURES: tuple[AiFeature, ...] = tuple(
    feature for feature in AI_FEATURES if feature not in CONSENT_COVERED_FEATURES
)


def is_feature(value: str) -> bool:
    return value in AI_FEATURES


def is_consentable(value: str) -> bool:
    """Whether a tenant may enable this surface under the current consent text."""

    return value in CONSENT_COVERED_FEATURES


__all__ = [
    "AI_FEATURES",
    "CONSENT_COVERED_FEATURES",
    "CONSENT_PENDING_FEATURES",
    "AiFeature",
    "is_consentable",
    "is_feature",
]
