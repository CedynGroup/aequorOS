"""The insights layer: statements a bank can be shown, and the facts behind them.

``docs/bi.md`` §"Insights layer". One idea holds the package together: an
insight may only restate something a typed fact already carries. There is no
path from a query result to a sentence that does not pass through
:mod:`~app.services.bi.insights.facts`, so an insight cannot assert a figure the
platform did not compute, cannot describe a missing figure as zero or flat, and
cannot present an advisory number as a certified one.

The pieces, in the order a caller uses them:

``facts``
    The closed, typed vocabulary of what may be asserted: an observation, a
    movement, a ratio bridge, a projection. Each carries its own measure
    identity and advisory designation, and each is built from a
    catalogue :class:`~app.domain.bi.catalogue.members.MeasureDef` so none of
    that metadata is a caller's choice.

``digest``
    ``fact_sheet_hash`` — a value-based fingerprint, in the posture of the
    regulatory ``input_hash`` and the attestation digests: identifiers and
    timestamps are excluded by rule, every value is included, and the facts
    are ordered by content so re-derivation over
    unchanged figures produces an unchanged hash.

``drivers``
    The exact ratio bridge, and the one judgement of whether a move was good for
    the bank (D-013 on magnitude).

``projections``
    Forward-looking statements, typed apart from observation and refusing rather
    than guessing when the series will not support one.

``rules``
    The deterministic conditions that turn a fact sheet into insights, and the
    presentation policy that decides how many a reader gets.

``statements``
    What an insight is: the sentence, the evidence, the designation.

``assemble``
    The one impure piece: it reads a bank's headline figures through the QUERY
    PATH's own compiler and authorization decision and builds the sheet the pure
    layer above consumes. Everything else in this package is a pure function of
    its arguments, which is what makes an ``InsightSet`` citable against its own
    ``fact_sheet_hash``.
"""

from __future__ import annotations

from app.services.bi.insights.assemble import (
    HEADLINE_MEASURE_CAP,
    MAX_COMPILED_READS,
    TIME_DATE_DIMENSION,
    AssembledInsights,
    assemble,
    default_compare_to,
    engine_measure_applies,
    engine_regime,
    headline_measures,
)
from app.services.bi.insights.digest import (
    FACT_SHEET_SCHEMA,
    fact_sheet_hash,
    fact_sheet_payload,
)
from app.services.bi.insights.drivers import (
    BridgeLeg,
    BridgeUnavailable,
    Favourability,
    RatioBridge,
    RatioPoint,
    favourability,
    ratio_bridge,
)
from app.services.bi.insights.facts import (
    WHOLE_INSTITUTION,
    BridgeFact,
    Fact,
    FactProvenance,
    FactScope,
    FactSheet,
    MissingReason,
    MovementFact,
    ObservedFact,
    ProjectionFact,
    bridge_fact,
    fact_sheet,
    movement_fact,
    observed_fact,
    projection_fact,
)
from app.services.bi.insights.projections import (
    Observation,
    Projection,
    ProjectionUnavailable,
    project,
)
from app.services.bi.insights.rules import (
    DEFAULT_POLICY,
    InsightPolicy,
    InsightSet,
    derive_insights,
)
from app.services.bi.insights.statements import Emphasis, Insight, StatementClass

__all__ = [
    "AssembledInsights",
    "BridgeFact",
    "BridgeLeg",
    "BridgeUnavailable",
    "DEFAULT_POLICY",
    "Emphasis",
    "FACT_SHEET_SCHEMA",
    "Fact",
    "FactProvenance",
    "FactScope",
    "FactSheet",
    "Favourability",
    "HEADLINE_MEASURE_CAP",
    "Insight",
    "InsightPolicy",
    "InsightSet",
    "MAX_COMPILED_READS",
    "MissingReason",
    "MovementFact",
    "ObservedFact",
    "Observation",
    "Projection",
    "ProjectionFact",
    "ProjectionUnavailable",
    "RatioBridge",
    "RatioPoint",
    "StatementClass",
    "TIME_DATE_DIMENSION",
    "WHOLE_INSTITUTION",
    "assemble",
    "bridge_fact",
    "default_compare_to",
    "derive_insights",
    "engine_measure_applies",
    "engine_regime",
    "fact_sheet",
    "fact_sheet_hash",
    "fact_sheet_payload",
    "favourability",
    "headline_measures",
    "movement_fact",
    "observed_fact",
    "project",
    "projection_fact",
    "ratio_bridge",
]
