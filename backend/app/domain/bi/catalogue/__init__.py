"""The BI metric catalogue: every queryable member, declared once (spec §Phase 1).

``catalogue()`` returns the ONE :class:`Catalogue` for this code version —
``certified_engine`` measures derived from the metric authority registry
(``engine.py``), ``portfolio`` measures over the marts (``measures.py``),
dimensions (``dimensions.py``) and hierarchies (``hierarchies.py``) — and
validates it once at construction: unique ids, every ratio's numerator and
denominator and every hierarchy level resolving to a member. The compiler
resolves each member's ``(table, column)`` string pair to a mapped column;
the authorization layer evaluates each member's ``(module, sensitivity)``.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import cache

from app.domain.bi.catalogue.dimensions import dimensions as _dimensions
from app.domain.bi.catalogue.engine import engine_measures
from app.domain.bi.catalogue.hierarchies import hierarchies as _hierarchies

# Both builders are aliased so the SUBMODULES keep their names as attributes of
# this package: an unaliased ``dimensions`` shadowed ``catalogue.dimensions``, so
# ``import app.domain.bi.catalogue.dimensions as d`` handed back the function.
from app.domain.bi.catalogue.measures import portfolio_measures
from app.domain.bi.catalogue.members import (
    ColumnRef,
    DimensionDef,
    EngineRule,
    EnumValue,
    HierarchyDef,
    MeasureDef,
    MemberDef,
    RowFilter,
)
from app.domain.bi.catalogue.targets import TARGET_TABLE, target_variants
from app.domain.bi.catalogue.version import CATALOGUE_VERSION

__all__ = [
    "CATALOGUE_VERSION",
    "Catalogue",
    "ColumnRef",
    "DimensionDef",
    "EngineRule",
    "EnumValue",
    "HierarchyDef",
    "MeasureDef",
    "MemberDef",
    "RowFilter",
    "UnknownMember",
    "catalogue",
]


class UnknownMember(KeyError):
    """A member id that is not in the catalogue (the API maps it to 422)."""


class CatalogueError(ValueError):
    """The catalogue is internally inconsistent; raised at construction."""


@dataclass(frozen=True, slots=True)
class Catalogue:
    version: str
    _measures: Mapping[str, MeasureDef]
    _dimensions: Mapping[str, DimensionDef]
    _hierarchies: tuple[HierarchyDef, ...]

    # -- lookup ----------------------------------------------------------------

    def members(self) -> tuple[MemberDef, ...]:
        """Every measure then every dimension, in catalogue order."""
        return (*self._measures.values(), *self._dimensions.values())

    def measures(self) -> tuple[MeasureDef, ...]:
        return tuple(self._measures.values())

    def dimensions(self) -> tuple[DimensionDef, ...]:
        return tuple(self._dimensions.values())

    def hierarchies(self) -> tuple[HierarchyDef, ...]:
        return self._hierarchies

    def measure(self, member_id: str) -> MeasureDef:
        try:
            return self._measures[member_id]
        except KeyError as exc:
            raise UnknownMember(member_id) from exc

    def dimension(self, member_id: str) -> DimensionDef:
        try:
            return self._dimensions[member_id]
        except KeyError as exc:
            raise UnknownMember(member_id) from exc

    def member(self, member_id: str) -> MemberDef:
        found = self._measures.get(member_id) or self._dimensions.get(member_id)
        if found is None:
            raise UnknownMember(member_id)
        return found

    def __contains__(self, member_id: object) -> bool:
        return member_id in self._measures or member_id in self._dimensions

    def __iter__(self) -> Iterator[MemberDef]:
        return iter(self.members())

    def for_module(self, module: str) -> tuple[MemberDef, ...]:
        """Every member whose authorization module is ``module`` (a ``Module`` value)."""
        return tuple(member for member in self.members() if member.module == module)

    def engine_measures(self) -> tuple[MeasureDef, ...]:
        return tuple(m for m in self._measures.values() if m.measure_kind == "certified_engine")

    def portfolio_measures(self) -> tuple[MeasureDef, ...]:
        """Mart measures over the bank's own book — never a target variant.

        A variant is ``portfolio`` in KIND (a target is the bank's number, not
        a certified engine copy) but it is not a portfolio measure: it reads
        the target mart, it inherits its base's grain and limit source, and an
        engine base's variant inherits that base's advisory designation. The
        discriminator is structural — the table it binds to — rather than the
        shape of its id.
        """
        return tuple(
            m
            for m in self._measures.values()
            if m.measure_kind == "portfolio" and m.table != TARGET_TABLE
        )

    def target_measures(self) -> tuple[MeasureDef, ...]:
        """Every ``.actual`` / ``.target`` / ``.variance`` / ``.variance_pct`` /
        ``.attainment_pct`` variant, in catalogue order."""
        return tuple(m for m in self._measures.values() if m.table == TARGET_TABLE)


def _validate(
    measures: Mapping[str, MeasureDef],
    dims: Mapping[str, DimensionDef],
    hierarchy_defs: tuple[HierarchyDef, ...],
) -> None:
    for measure in measures.values():
        for reference in (measure.numerator, measure.denominator, measure.weight):
            if reference is not None and reference not in measures:
                raise CatalogueError(f"{measure.id} references unknown measure {reference!r}")
        if measure.over is not None and measure.over not in dims:
            raise CatalogueError(
                f"{measure.id} concentrates over unknown dimension {measure.over!r}"
            )
        for dimension_id in measure.allowed_dimensions:
            if dimension_id not in dims:
                raise CatalogueError(f"{measure.id} allows unknown dimension {dimension_id!r}")
    seen: set[str] = set()
    for hierarchy in hierarchy_defs:
        if hierarchy.id in seen:
            raise CatalogueError(f"duplicate hierarchy id {hierarchy.id!r}")
        seen.add(hierarchy.id)
        for level in hierarchy.levels:
            if level not in dims:
                raise CatalogueError(f"hierarchy {hierarchy.id} names unknown dimension {level!r}")


def build_catalogue() -> Catalogue:
    """Assemble and validate a fresh catalogue (``catalogue()`` caches one)."""
    measures: dict[str, MeasureDef] = {}
    bases = (*engine_measures(), *portfolio_measures())
    # Targets are DERIVED from the bases, so they are folded in after them and
    # read the finished definitions: every variant copies its base's module,
    # sensitivity, entitlement, grain and allowed dimensions rather than
    # restating them, and ``_validate`` then checks the variants for free.
    for measure in (*bases, *target_variants(bases)):
        if measure.id in measures:
            raise CatalogueError(f"duplicate measure id {measure.id!r}")
        measures[measure.id] = measure
    dims: dict[str, DimensionDef] = {}
    for dimension in _dimensions():
        if dimension.id in dims or dimension.id in measures:
            raise CatalogueError(f"duplicate member id {dimension.id!r}")
        dims[dimension.id] = dimension
    hierarchy_defs = _hierarchies()
    _validate(measures, dims, hierarchy_defs)
    return Catalogue(CATALOGUE_VERSION, measures, dims, hierarchy_defs)


@cache
def catalogue() -> Catalogue:
    """The catalogue for this code version."""
    return build_catalogue()
