"""Which authority a governed export needs, decided from the query itself.

``docs/bi.md`` §Exports states the rule in one sentence: *summary exports need
``view``; record-level or confidential exports need ``export``*. Everything here
exists to make that sentence decidable from the SERVER's own reading of the
request, because the alternative — a ``confidential: true`` flag on the request
body — would put the classification in the hands of the party the classification
constrains.

**How the class is read.** Every catalogue member declares its own
``sensitivity`` (D-028), and that declaration is already the platform's statement
about what the member discloses: ``aggregated`` is a total, ``restricted`` names
a legal person, ``confidential`` identifies ONE record. So the class of an export
is a property of the member set, not of the caller's intent, and the member set
is taken from the same walk the authorization decision uses
(``authorization.query_members``) — measures, the measures they are composed
from, dimensions, filters, the Top-N and pivot axes and every sort key. A filter
counts: "total exposure WHERE counterparty name = X" is a statement about X.

**Why ``restricted`` sits with ``confidential`` rather than with ``aggregated``.**
The spec's two words are "record-level" and "confidential"; the catalogue's three
non-public levels are ``aggregated``, ``restricted`` and ``confidential``. The
only mapping that satisfies the sentence in the deny-by-default direction is to
treat everything above ``aggregated`` as the second class: a spreadsheet of named
obligors leaving the building is the case the elevated authority is FOR, and it
is ``restricted``, not ``confidential``. The reverse mapping would let a viewer
export the obligor list. Recorded as D-066.

**Both permissions, not the higher one.** A record-level export requires
``view`` AND ``export``. They are separate sentences in an indivisible binding
row, and no ordering is implied between them anywhere in
``app/core/authorization.py``; requiring only ``export`` would mean a bundle that
carried it without ``view`` could pull rows its holder cannot open interactively,
which is exactly the property ``docs/bi.md`` forbids.

**Checked twice.** The class is read before authorization (from the member walk)
so the right sentence is required, and again after compilation (from
``CompiledQuery.member_ids``, which additionally carries any injected data-scope
member) so a compilation that widened the member set cannot be served under an
authority that did not cover it. The second check can only ever refuse.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Literal

from app.core.authorization import Permission, Sensitivity
from app.domain.bi.catalogue import Catalogue, MemberDef

#: ``summary`` — every member is a total or a published figure; ``record_level``
#: — at least one member names a person, a counterparty or a single record. The
#: second value covers BOTH of the spec's words (record-level and confidential):
#: see the module docstring.
ExportClass = Literal["summary", "record_level"]

SUMMARY: ExportClass = "summary"
RECORD_LEVEL: ExportClass = "record_level"

#: Sensitivities a SUMMARY export may contain. Stated as the admitted set rather
#: than the refused one, so a sensitivity added to the platform later is
#: record-level until someone decides otherwise.
SUMMARY_SENSITIVITIES: frozenset[str] = frozenset(
    {Sensitivity.PUBLISHED.value, Sensitivity.AGGREGATED.value}
)

#: Production copy for each class, for the artifact and for a refusal.
CLASS_LABELS: dict[ExportClass, str] = {
    SUMMARY: "Summary",
    RECORD_LEVEL: "Record level",
}


def classify(members: Iterable[MemberDef]) -> ExportClass:
    """The class of an export over ``members``. Deny-by-default on an unknown level."""

    for member in members:
        if str(member.sensitivity) not in SUMMARY_SENSITIVITIES:
            return RECORD_LEVEL
    return SUMMARY


def classify_ids(cat: Catalogue, member_ids: Sequence[str]) -> ExportClass:
    """The class of an export over member IDS, as a compiled query reports them.

    An id the catalogue does not know classifies as record-level rather than
    raising: this runs AFTER a successful compilation, where every id is known,
    and the only way to reach the unknown branch is a catalogue that changed
    under the request — which must not be resolved by widening the answer.
    """

    members: list[MemberDef] = []
    for member_id in member_ids:
        try:
            members.append(cat.member(member_id))
        except KeyError:
            return RECORD_LEVEL
    return classify(members)


def permissions_for(export_class: ExportClass) -> tuple[Permission, ...]:
    """Every permission the class requires, in the order they are evaluated."""

    if export_class == SUMMARY:
        return (Permission.VIEW,)
    return (Permission.VIEW, Permission.EXPORT)


def record_level_members(members: Iterable[MemberDef]) -> tuple[str, ...]:
    """The ids that put an export in the record-level class, for the audit trail."""

    return tuple(
        member.id for member in members if str(member.sensitivity) not in SUMMARY_SENSITIVITIES
    )


__all__ = [
    "CLASS_LABELS",
    "RECORD_LEVEL",
    "SUMMARY",
    "SUMMARY_SENSITIVITIES",
    "ExportClass",
    "classify",
    "classify_ids",
    "permissions_for",
    "record_level_members",
]
