"""``business_units`` — the branch / business-unit register: the bank's own
declaration of its reporting hierarchy, and the ONLY source of a branch's
region.

One row per business unit. Positions carry ``attributes.branch_id``, which is
matched against ``business_unit_id`` (``fact_derivation._business_unit_names``);
a unit nothing references is still a legitimate row — a closed branch, a cost
centre that holds no book — so the register is pushed whole and the latest
accepted batch wins.

**The code's field names are canonical.** Readers key on ``business_unit_id`` /
``business_unit_name``; docs/API_INTEGRATION.md documented ``unit_id`` / ``name``,
so both spellings are in the field. Reference rows are preserved VERBATIM and a
mapping config can only restrict which columns are kept, never rename one
(``ReferenceMapping.fields``), so a bank already pushing the documented spelling
cannot be corrected at the boundary — the aliases have to be resolved on read.
``normalise_row`` is that one place, and every consumer of this dataset goes
through it. (``fact_derivation._business_unit_names`` still reads the canonical
keys directly; it predates this schema and adopting ``normalise_row`` there is
the outstanding half of the alias contract.)

**Region is declared, never inferred.** ``region`` is optional and it is the only
place a branch's region can come from: no ``region`` column exists on
``outlets``, and the address is free text whose keys are not specified, so
parsing it would invent a board figure out of typing. Until a bank supplies the
field the BI branch dimension reports the region as unassigned rather than
guessing one.

Docs: docs/API_INTEGRATION.md §3.5.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping

from . import ReferenceSchema, register

#: Documented spelling → the canonical key readers use. Accepted on input for
#: compatibility; deliberately NOT part of the CSV template's columns, which
#: teaches the canonical names only.
ALIASES: dict[str, str] = {"unit_id": "business_unit_id", "name": "business_unit_name"}

#: The longest value each register field may carry: the width of the
#: ``bi_dim_branch`` column it is copied into verbatim (``branch_code`` /
#: ``name`` / ``region``). Asserted equal to the model by test rather than
#: imported, because this module deliberately stays free of ``app.models``.
UNIT_FIELD_MAX_LENGTHS: dict[str, int] = {
    "business_unit_id": 120,
    "business_unit_name": 255,
    "region": 120,
}

SCHEMA = register(
    ReferenceSchema(
        kind="business_units",
        description=(
            "Branch / business-unit register: identity, the bank's declared region, and the "
            "hierarchy and outlet / cost-centre keys that tie a unit to the rest of its books"
        ),
        grain="one row per business unit; the whole register per push (latest as-of wins)",
        required=("business_unit_id", "business_unit_name"),
        optional=("region", "parent_unit_id", "outlet_number", "cost_centre", "notes"),
        # The widths of ``bi_dim_branch.branch_code`` / ``name`` / ``region``, which
        # carry these three verbatim (pinned by test against the model). A longer
        # value is refused here rather than failing the whole mart build on Postgres.
        max_lengths=UNIT_FIELD_MAX_LENGTHS,
    )
)


def normalise_row(row: Mapping[str, object]) -> dict[str, object]:
    """The row keyed by the canonical field names, each documented alias folded
    onto its canonical key when that key is absent.

    The canonical value always wins; a row that gives both spellings with
    different values is REPORTED by :func:`validate_business_unit_row` rather
    than silently resolved. Alias keys are left in place — reference payloads are
    preserved as ingested, and dropping them here would make the normalised row
    disagree with what lineage says was received.
    """
    normalised = dict(row)
    for alias, canonical in ALIASES.items():
        if normalised.get(canonical) in (None, "") and normalised.get(alias) not in (None, ""):
            normalised[canonical] = normalised[alias]
    return normalised


def validate_business_unit_row(row: dict) -> list[str]:
    """Schema problems AFTER alias resolution — so a row that uses the documented
    spelling is well-formed, while a row that uses neither is told the canonical
    name — plus the two rules the register's own shape imposes: a field spelt
    both ways must agree with itself, and a unit cannot be its own parent."""
    normalised = normalise_row(row)
    problems = SCHEMA.validate_row(normalised)
    for alias, canonical in ALIASES.items():
        canonical_value, alias_value = row.get(canonical), row.get(alias)
        if canonical_value in (None, "") or alias_value in (None, ""):
            continue
        if str(canonical_value).strip() != str(alias_value).strip():
            problems.append(
                f"fields '{canonical}' and '{alias}' disagree ({canonical_value!r} vs "
                f"{alias_value!r}); '{alias}' is an alias of '{canonical}'"
            )
    unit_id = str(normalised.get("business_unit_id") or "").strip()
    parent_id = str(normalised.get("parent_unit_id") or "").strip()
    if unit_id and unit_id == parent_id:
        problems.append(f"field 'parent_unit_id' must not be the unit itself (got {parent_id!r})")
    return problems


# Bound after the function exists, because the rules need the schema they belong
# to. Re-registered so ``schema_for('business_units')`` returns the schema WITH its
# rules — the ingestion path asks ``problems_for``, and without this binding it
# would silently get the declarative half only (audit A7-07 / H-027).
SCHEMA = register(dataclasses.replace(SCHEMA, row_validator=validate_business_unit_row))
