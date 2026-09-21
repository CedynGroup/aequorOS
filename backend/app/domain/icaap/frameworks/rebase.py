"""Moving a cycle from one framework version to its successor (pure domain).

When the regulator publishes the final text of an instrument the platform holds
as an exposure draft, the new text ships as a NEW version with a
``section_key_map`` from the old one. A bank's in-flight cycle then moves by
rebase: a fresh cycle pinned to the target, with each section's text carried
across the mapping and anything the mapping cannot carry cleanly flagged for
review rather than silently dropped.

The plan is computed here, with no database in sight, so the carry rules are
testable on synthetic versions and the service only has to execute them.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.icaap.frameworks.schema import Framework, KeyRelation

#: Relations whose carried text a human must re-read: the target section is not
#: the same question, so the old answer is a starting point, not an answer.
NEEDS_REVIEW: frozenset[str] = frozenset({"partial", "split", "merge"})


class RebaseNotPossible(ValueError):
    """The target framework does not declare itself a successor of the source."""


@dataclass(frozen=True)
class SectionMove:
    from_key: str
    to_key: str | None
    relation: KeyRelation
    needs_review: bool


@dataclass(frozen=True)
class RebasePlan:
    source: tuple[str, str]
    target: tuple[str, str]
    moves: tuple[SectionMove, ...]
    #: Target sections nothing maps into: they start empty.
    new_sections: tuple[str, ...]
    #: Requirement ids present in both versions — their checklist state carries.
    carried_items: frozenset[str]
    #: Requirement ids only in the source — their state is discarded, and the
    #: rebase audit event lists them so the loss is on the record.
    dropped_items: frozenset[str]

    @property
    def needs_review(self) -> tuple[str, ...]:
        return tuple(move.to_key for move in self.moves if move.needs_review and move.to_key)


def plan_rebase(source: Framework, target: Framework) -> RebasePlan:
    """What moving a cycle from ``source`` to ``target`` would do."""
    if target.supersedes != (source.code, source.version):
        msg = (
            f"{target.code} {target.version} does not supersede "
            f"{source.code} {source.version}; a rebase needs a declared lineage."
        )
        raise RebaseNotPossible(msg)

    declared = {mapping.from_key: mapping for mapping in target.section_key_map}
    missing = [section.key for section in source.sections if section.key not in declared]
    if missing:
        msg = (
            f"{target.code} {target.version} maps no target for source section(s) {missing}; "
            "an unmapped section must say so explicitly."
        )
        raise RebaseNotPossible(msg)

    moves = tuple(
        SectionMove(
            from_key=section.key,
            to_key=declared[section.key].to_key,
            relation=declared[section.key].relation,
            needs_review=declared[section.key].relation in NEEDS_REVIEW,
        )
        for section in source.sections
    )
    reached = {move.to_key for move in moves if move.to_key is not None}
    new_sections = tuple(section.key for section in target.sections if section.key not in reached)
    source_items = {item.id for item in source.all_items()}
    target_items = {item.id for item in target.all_items()}
    return RebasePlan(
        source=(source.code, source.version),
        target=(target.code, target.version),
        moves=moves,
        new_sections=new_sections,
        carried_items=frozenset(source_items & target_items),
        dropped_items=frozenset(source_items - target_items),
    )


__all__ = ["NEEDS_REVIEW", "RebaseNotPossible", "RebasePlan", "SectionMove", "plan_rebase"]
