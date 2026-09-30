"""The deterministic commentary. This is the product, not an error path.

Every way the model path can fail to produce something usable — the deployment
switch is off, the tenant has not consented, the queue entry expired, the vendor
refused, the reply broke its schema, the prose failed grounding — ends here, and
a reader must not be able to tell which. So this is written as commentary in its
own right: composed from the SAME insight statements the strip beside it shows,
in the same order, with the platform's own figures already in them.

Three properties make that safe to lean on:

**It is pure.** A fact sheet and its insights in, paragraphs out. No session, no
clock, no catalogue lookup, so the same sheet always produces the same
commentary — which is what lets it be written to the draft row BEFORE the model
is called, and served whatever happens next.

**It restates, and never composes.** Each sentence is an
:class:`~app.services.bi.insights.statements.Insight`'s own ``detail``, which is
production copy that already carries its figure in the precision the fact holds
it, already says a missing figure is neither zero nor flat, and already refuses
to call an advisory number certified. This module decides ORDER and PARAGRAPHING
and nothing else.

**It says so when there is nothing to say.** "No figure moved materially" and
"nothing has been computed" are different answers, and a reader gets whichever is
true rather than an empty page.

No currency, regulator or country name is written here: an amount reaches the
copy through ``statements.render_value``, which names the reporting currency the
institution's own jurisdiction resolves.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date

from app.services.bi.insights.facts import FactSheet
from app.services.bi.insights.rules import InsightSet
from app.services.bi.insights.statements import Insight, StatementClass

__all__ = ["FALLBACK_SOURCE", "deterministic_commentary"]

#: What the read path reports as the source of these paragraphs. The reader is
#: told the commentary is the platform's own, because a bank reviewing its own
#: reporting is entitled to know which sentences a model wrote.
FALLBACK_SOURCE = "platform"

#: Which statement classes belong in which paragraph, and the sentence that opens
#: it. The grouping is editorial and lives here; the sentences are the insights'.
_PARAGRAPHS: tuple[tuple[str, tuple[StatementClass, ...]], ...] = (
    ("What moved", ("movement", "attribution")),
    ("What the figures do not show", ("data_gap",)),
    ("If the observed trend continues", ("projection",)),
)


def deterministic_commentary(
    *,
    sheet: FactSheet,
    insight_set: InsightSet,
    institution_name: str,
    compare_to: date,
) -> tuple[str, ...]:
    """The platform's own commentary on one institution at one reporting date."""
    paragraphs: list[str] = [_opening(sheet, institution_name, compare_to)]
    for heading, classes in _PARAGRAPHS:
        sentences = _sentences(insight_set.insights, classes)
        if sentences:
            paragraphs.append(f"{heading}. " + " ".join(sentences))
    qualifiers = _qualifiers(insight_set.insights)
    if qualifiers:
        paragraphs.append("Read these figures with the following in mind. " + " ".join(qualifiers))
    if len(paragraphs) == 1:
        paragraphs.append(_nothing_to_report(sheet))
    return tuple(paragraphs)


def _opening(sheet: FactSheet, institution_name: str, compare_to: date) -> str:
    """One sentence naming what this is about. Names and dates are the platform's
    own to write here: the placeholder rules bind the MODEL, not this module."""
    figures = len(sheet.facts)
    figure_words = "figure" if figures == 1 else "figures"
    return (
        f"This commentary covers {institution_name} at {sheet.as_of.isoformat()}, "
        f"measured against {compare_to.isoformat()}. It reads {figures} headline "
        f"{figure_words} and states only what those figures support."
    )


def _sentences(insights: Sequence[Insight], classes: Iterable[StatementClass]) -> list[str]:
    """The details of every insight in these classes, in the set's own order."""
    wanted = set(classes)
    return [
        insight.detail.strip()
        for insight in insights
        if insight.statement_class in wanted and insight.detail.strip()
    ]


def _qualifiers(insights: Sequence[Insight]) -> list[str]:
    """Every distinct qualifier, once. An insight repeats its advisory sentence on
    each statement it qualifies; a reader needs to be told once."""
    seen: dict[str, None] = {}
    for insight in insights:
        for qualifier in insight.qualifiers:
            cleaned = qualifier.strip()
            if cleaned:
                seen.setdefault(cleaned, None)
    return list(seen)


def _nothing_to_report(sheet: FactSheet) -> str:
    """The honest empty answer — and there are two of them.

    A sheet with facts and no insights means the figures were read and nothing
    stood out. A sheet with no facts at all means nothing has been computed for
    this date, which is a different sentence and a different thing to do about it.
    """
    if not sheet.facts:
        return (
            "No headline figure has been computed for this reporting date, so there is "
            "nothing to comment on yet. Commentary appears once the figures behind it "
            "have been computed."
        )
    return (
        "None of these figures moved by enough to be worth reporting, none is missing, "
        "and none carries a reservation about how far it has been checked."
    )
