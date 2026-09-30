"""``fact_sheet_hash``: a value-based fingerprint of what the insights assert.

The problem this solves is the one the regulatory ``input_hash`` solves. The
live plane re-derives its facts on every refresh, minting new identifiers and a
new derivation timestamp over figures that have not changed by a penny. A digest
that took the fact set as it stands would move on every refresh, so it could
never answer the only question worth asking of it: *are these the same figures
the reader was shown, or different ones?*

So the rules here are the ones ``app/services/attestation/digests.py`` already
states, applied to a fact sheet:

**Excluded, by RULE rather than by hope.** The whole
:class:`~app.services.bi.insights.facts.FactProvenance` block — the fact's own
id, when it was derived, which mart build produced it — plus the sheet's
``generated_at``. They identify WHICH derivation produced a figure, never WHAT
the figure is. They stay on the record, because they are the evidence a reader
follows back to the build; they are removed from the digest INPUT only.

**Included: everything a reader could be misled by.** Every value, every date,
every measure identity and scope, the favourable direction, the value type, the
advisory designation and the certified verdict.

**Canonicalisation is the platform's one recipe.** Sorted keys, compact
separators, ASCII, and deliberately no ``default=`` handler — reused from
``attestation.digests`` rather than re-implemented, so a BI digest and an
attestation digest cannot drift apart. Everything is pre-stringified before it
gets there, which is what makes a silent ``str()`` coercion impossible.

Two details that decide whether the digest is really value-based:

* **Numbers are canonicalised, not formatted.** ``0.50`` and ``0.5`` are the
  same value and re-derivation may produce either, so every decimal is
  normalised to its plain form first — and a signed zero is written as a plain
  zero, because ``-0`` and ``0`` are the same number.
* **Order is content, not arrival.** Facts are sorted by their own canonical
  JSON, exactly as the regulatory snapshot sorts its fact list, so two
  derivations that produce the same facts in different orders hash the same.

Adding a fact type without teaching :func:`fact_payload` about it raises rather
than silently hashing nothing; ``tests/services/bi/test_insights_digest.py``
pins that every member of the ``Fact`` union is covered.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from app.services.attestation.digests import canonical_json, sha256_hex
from app.services.bi.insights.drivers import BridgeUnavailable, RatioBridge
from app.services.bi.insights.facts import (
    BridgeFact,
    Fact,
    FactSheet,
    MovementFact,
    ObservedFact,
    ProjectionFact,
)
from app.services.bi.insights.projections import Projection, ProjectionUnavailable

__all__ = [
    "FACT_SHEET_SCHEMA",
    "UnhashableFact",
    "canonical_decimal",
    "fact_payload",
    "fact_sheet_hash",
    "fact_sheet_payload",
]

#: Versioned like the regulatory ``INPUT_SCHEMA_VERSION``: a change in what the
#: digest covers is a change of schema, never a silent re-meaning of the hash.
#: v2 (2026-09-29): the per-fact reconciliation ``trust`` payload left the digest
#: with the verdict itself, so a sheet hashed under v1 and the same figures hashed
#: here differ by construction; the schema name says which recipe produced a hash.
FACT_SHEET_SCHEMA = "aequoros-bi-fact-sheet-v2"


class UnhashableFact(TypeError):
    """A fact type the digest does not know how to cover."""


def canonical_decimal(value: Decimal) -> str:
    """The one written form of a number: value-based, never presentational."""
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _decimal_or_none(value: Decimal | None) -> str | None:
    return None if value is None else canonical_decimal(value)


def _common_payload(fact: Fact) -> dict[str, Any]:
    """Everything every fact carries. Provenance is deliberately absent."""
    return {
        "kind": fact.kind,
        "measure_id": fact.measure_id,
        "label": fact.label,
        "value_type": fact.value_type,
        "direction": fact.direction,
        "advisory": fact.advisory,
        "certified": fact.certified,
        "scope": {
            "dimension_id": fact.scope.dimension_id,
            "value_code": fact.scope.value_code,
            "value_label": fact.scope.value_label,
        },
    }


def _bridge_payload(bridge: RatioBridge | BridgeUnavailable) -> dict[str, Any]:
    if isinstance(bridge, BridgeUnavailable):
        return {"available": False, "reason": bridge.reason}
    return {
        "available": True,
        "numerator_measure_id": bridge.numerator_measure_id,
        "denominator_measure_id": bridge.denominator_measure_id,
        "prior": {
            "numerator": canonical_decimal(bridge.prior.numerator),
            "denominator": canonical_decimal(bridge.prior.denominator),
            "value": canonical_decimal(bridge.prior.value),
        },
        "current": {
            "numerator": canonical_decimal(bridge.current.numerator),
            "denominator": canonical_decimal(bridge.current.denominator),
            "value": canonical_decimal(bridge.current.value),
        },
        "change": canonical_decimal(bridge.change),
        "quantum": canonical_decimal(bridge.quantum),
        "favourability": bridge.favourability,
        "legs": [
            {
                "component": leg.component,
                "measure_id": leg.measure_id,
                "label": leg.label,
                "contribution": canonical_decimal(leg.contribution),
            }
            for leg in bridge.legs
        ],
    }


def _projection_payload(projection: Projection | ProjectionUnavailable) -> dict[str, Any]:
    if isinstance(projection, ProjectionUnavailable):
        return {"available": False, "reason": projection.reason}
    return {
        "available": True,
        "method": projection.method,
        "horizon": projection.horizon.isoformat(),
        "value": canonical_decimal(projection.value),
        "change_from_last": canonical_decimal(projection.change_from_last),
        "per_day_change": canonical_decimal(projection.per_day_change),
        "window_start": projection.window_start.isoformat(),
        "window_end": projection.window_end.isoformat(),
        "observation_count": projection.observation_count,
        "assumption": projection.assumption,
        "quantum": canonical_decimal(projection.quantum),
    }


def fact_payload(fact: Fact) -> dict[str, Any]:
    """The digest INPUT for one fact: every value, no provenance."""
    specific = _kind_payload(fact)  # dispatched first: an unknown type fails here
    return {**_common_payload(fact), **specific}


def _kind_payload(fact: Fact) -> dict[str, Any]:
    """What only this kind of fact carries. Dispatched before anything is read,
    so a type the digest does not cover fails here rather than hashing a subset.
    """
    if isinstance(fact, ObservedFact):
        return {
            "as_of": fact.as_of.isoformat(),
            "value": _decimal_or_none(fact.value),
            "missing_reason": fact.missing_reason,
        }
    if isinstance(fact, MovementFact):
        return {
            "as_of": fact.as_of.isoformat(),
            "prior_as_of": fact.prior_as_of.isoformat(),
            "current": _decimal_or_none(fact.current),
            "prior": _decimal_or_none(fact.prior),
            "missing_reason": fact.missing_reason,
        }
    if isinstance(fact, BridgeFact):
        return {
            "as_of": fact.as_of.isoformat(),
            "prior_as_of": fact.prior_as_of.isoformat(),
            "bridge": _bridge_payload(fact.bridge),
        }
    if isinstance(fact, ProjectionFact):
        return {
            "as_of": fact.as_of.isoformat(),
            "projection": _projection_payload(fact.projection),
        }
    raise UnhashableFact(  # pyright: ignore[reportUnreachable] - a new fact type must fail loudly
        f"{type(fact).__name__} is not covered by the fact-sheet digest"
    )


def fact_sheet_payload(sheet: FactSheet) -> dict[str, Any]:
    """The whole digest INPUT, exposed so a difference can be SEEN, not guessed."""
    payloads = [fact_payload(fact) for fact in sheet.facts]
    return {
        "schema": FACT_SHEET_SCHEMA,
        "institution_id": sheet.institution_id,
        "as_of": _as_of(sheet.as_of),
        "catalogue_version": sheet.catalogue_version,
        "facts": sorted(payloads, key=canonical_json),
    }


def _as_of(value: date) -> str:
    return value.isoformat()


def fact_sheet_hash(sheet: FactSheet) -> str:
    """The value-based fingerprint of ``sheet`` (see the module docstring)."""
    return sha256_hex(canonical_json(fact_sheet_payload(sheet)))
