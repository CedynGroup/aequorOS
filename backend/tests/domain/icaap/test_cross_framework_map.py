"""Which section of one regulator's report answers which of another's.

A group filing in Accra, Lagos and Nairobi writes the same assessment three
times into three different structures. ``cross_framework_section_map`` (A5) is
how the platform says which Ghanaian section a Nigerian or Kenyan one answers,
so a comparison view — and, later, a "start this section from last year's Ghana
text" action — rests on a declared correspondence rather than on matching
section titles at read time.

Two properties make the map trustworthy, and both are checked here.

**Complete in both directions.** Every section of the source framework appears
as a ``from``, so nothing is silently skipped, and every section of the target
is reached by at least one ``to``, so nothing is silently orphaned. A section
with genuinely no counterpart says ``unmapped`` out loud — Kenya's Guidance
Note asks for no separate liquidity planning section, and that is a fact about
the instrument, not a gap in the extraction.

**Not a rebase.** ``section_key_map`` is version lineage within one instrument
and is what ``plan_rebase`` consumes. If this map were folded into it, a cycle
could be "rebased" from a Ghanaian framework onto a Kenyan one, quietly
re-pointing a bank's checklist at another country's regulator. They stay
separate types, and the test below pins that.
"""

from __future__ import annotations

import pytest

from app.domain.icaap.frameworks import registry
from app.domain.icaap.frameworks.schema import CrossFrameworkMap, Framework

MAPPED = [
    (framework, cross)
    for framework in registry.load_all()
    for cross in framework.cross_framework_section_map
]
IDS = [f"{framework.code}<-{cross.framework}" for framework, cross in MAPPED]


def test_at_least_one_framework_declares_an_equivalence() -> None:
    """Otherwise every assertion below would pass over an empty list."""
    assert MAPPED, "no framework maps its sections to another jurisdiction's"


@pytest.mark.parametrize(("framework", "cross"), MAPPED, ids=IDS)
def test_the_named_framework_is_published(framework: Framework, cross: CrossFrameworkMap) -> None:
    _ = framework
    published = {other.code for other in registry.load_all()}
    assert cross.framework in published


@pytest.mark.parametrize(("framework", "cross"), MAPPED, ids=IDS)
def test_every_source_section_appears_exactly_once_as_a_starting_point(
    framework: Framework, cross: CrossFrameworkMap
) -> None:
    """Complete in the source direction: no section of theirs goes unanswered."""
    assert cross.direction == "from"
    source = next(f for f in registry.load_all() if f.code == cross.framework)
    source_keys = [section.key for section in source.sections]

    froms = [mapping.from_key for mapping in cross.mappings]
    unknown = sorted(set(froms) - set(source_keys))
    assert not unknown, (
        f"{framework.code} maps from sections {source.code} does not have: {unknown}"
    )

    missing = [key for key in source_keys if key not in froms]
    assert not missing, f"{source.code} sections with no entry in {framework.code}'s map: {missing}"


@pytest.mark.parametrize(("framework", "cross"), MAPPED, ids=IDS)
def test_every_target_section_is_reached(framework: Framework, cross: CrossFrameworkMap) -> None:
    """Complete in the target direction: no section of ours is orphaned."""
    own_keys = {section.key for section in framework.sections}
    reached = {mapping.to_key for mapping in cross.mappings if mapping.to_key is not None}

    unknown = sorted(reached - own_keys)
    assert not unknown, f"{framework.code}'s map points at sections it does not have: {unknown}"

    orphaned = sorted(own_keys - reached)
    assert not orphaned, f"{framework.code} sections nothing maps to: {orphaned}"


@pytest.mark.parametrize(("framework", "cross"), MAPPED, ids=IDS)
def test_only_an_unmapped_section_has_no_target(
    framework: Framework, cross: CrossFrameworkMap
) -> None:
    """A missing counterpart is stated, never left as a null with a relation."""
    _ = framework
    for mapping in cross.mappings:
        if mapping.relation == "unmapped":
            assert mapping.to_key is None
        else:
            assert mapping.to_key is not None


def test_kenya_says_out_loud_that_it_asks_for_no_liquidity_section() -> None:
    """The one unmapped entry, and the reason the relation exists at all.

    The BoG Guideline prints a liquidity planning and management section; the
    CBK Guidance Note does not, and treats liquidity as a risk to assess rather
    than a chapter to write. Recording that as ``unmapped`` is the honest
    answer; mapping it to the nearest-looking Kenyan section would tell a
    reader the two ask for the same thing.
    """
    kenya = registry.get("cbk_icaap_gn", "2016.11")
    cross = next(c for c in kenya.cross_framework_section_map if c.framework == "bog_icaap")
    unmapped = [m.from_key for m in cross.mappings if m.relation == "unmapped"]
    assert unmapped == ["liquidity_planning"]
    assert not kenya.has_section("liquidity_planning")


def test_a_cross_country_map_can_never_be_used_as_a_rebase() -> None:
    """The two maps stay separate types, and rebase reads only its own.

    ``plan_rebase`` moves a cycle between VERSIONS of one instrument and needs
    ``supersedes`` to do it. Neither Nigeria nor Kenya supersedes anything, so
    even a caller that confused the two would get a refusal rather than a
    checklist swapped for another regulator's.
    """
    for framework in registry.load_all():
        if not framework.cross_framework_section_map:
            continue
        assert framework.supersedes is None
        assert framework.section_key_map == ()
