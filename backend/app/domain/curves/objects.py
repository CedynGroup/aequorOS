"""Curve object model — the pure part of spec §7, names from spec §8.

Mirrors what every mature engine converges on: a *definition* (what is
interpolated and how), *nodes* (the fitted values), and an immutable *build
result* carrying QA and a value-based input digest. The digest follows the
platform's value-based hashing philosophy (same idiom as the regulatory
``input_hash``): canonical JSON — sorted keys, tight separators, ASCII — of
the raw inputs, SHA-256. Two builds from identical inputs produce identical
digests; nothing volatile (timestamps, ids) may ever enter the payload.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType
from typing import Literal

from app.domain.curves.conventions import DayCount
from app.domain.curves.forwards import ForwardQaResult

__all__ = [
    "SUPPORTED_CURVE_CODES",
    "CurveBuildResult",
    "CurveDefinition",
    "CurveNodes",
    "CurveObjectError",
    "canonical_input_digest",
    "is_supported_curve_code",
]

type CurveKind = Literal["zero", "forward", "discount"]


class CurveObjectError(ValueError):
    """A curve object violates its own contract (bad code, mismatched nodes)."""


# Spec §8's table: the five Aequor curve codes. The FTP entry is a per-bank
# family — `<BANKID>` is substituted with the platform id (BK-XXXXXXXX).
SUPPORTED_CURVE_CODES: MappingProxyType[str, str] = MappingProxyType(
    {
        "AEQ.GHS.SOV.ZERO": "Aequor Ghana Sovereign Curve (AGS) — GoG zero-coupon sovereign curve",
        "AEQ.GHS.SOV.FWD": "Aequor Ghana Sovereign Forward — sovereign forward curve",
        "AEQ.GHS.OIS": "Aequor Ghana Discounting Curve (AGD) — synthetic OIS / discounting proxy",
        "AEQ.GHS.FTP.<BANKID>": "Aequor [Bank] Funding Curve — per-bank funding/FTP overlay",
        "AEQ.GHS.CORP": "Aequor Ghana Credit Curve — corporate/GFIM credit curve",
    }
)

_FTP_PATTERN = re.compile(r"^AEQ\.GHS\.FTP\.[A-Z0-9-]+$")


def is_supported_curve_code(code: str) -> bool:
    """True for the four fixed codes and any per-bank FTP instantiation."""
    if code in SUPPORTED_CURVE_CODES and code != "AEQ.GHS.FTP.<BANKID>":
        return True
    return bool(_FTP_PATTERN.match(code))


@dataclass(frozen=True)
class CurveDefinition:
    """What a curve *is*: identity, kind, and the methodology knobs.

    ``instrument_selection`` names the versioned methodology parameters that
    governed instrument admission (e.g. ``min_trade_count``,
    ``max_staleness_days``) — the *names*, per spec §5; the values live in the
    methodology register, not in pure code.
    """

    curve_code: str
    curve_kind: CurveKind
    interpolation: str
    day_count: DayCount
    extrapolation: str
    instrument_selection: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not is_supported_curve_code(self.curve_code):
            raise CurveObjectError(
                f"'{self.curve_code}' is not a supported Aequor curve code "
                f"(see SUPPORTED_CURVE_CODES)."
            )

    def as_payload(self) -> dict[str, object]:
        """JSON-safe canonical form for digesting."""
        return {
            "curve_code": self.curve_code,
            "curve_kind": self.curve_kind,
            "interpolation": self.interpolation,
            "day_count": self.day_count.value,
            "extrapolation": self.extrapolation,
            "instrument_selection": list(self.instrument_selection),
        }


@dataclass(frozen=True)
class CurveNodes:
    """The fitted node set: tenors in years (and optionally dates) plus values.

    ``values`` are zero rates for zero curves, forwards for forward curves and
    discount factors for discount curves — the definition's ``curve_kind``
    says which.
    """

    tenor_years: tuple[float, ...]
    values: tuple[float, ...]
    dates: tuple[date, ...] | None = None

    def __post_init__(self) -> None:
        if len(self.tenor_years) != len(self.values):
            raise CurveObjectError("tenor_years and values must be equally long.")
        if not self.tenor_years:
            raise CurveObjectError("A curve needs at least one node.")
        if self.dates is not None and len(self.dates) != len(self.tenor_years):
            raise CurveObjectError("dates, when given, must align with tenor_years.")
        for left, right in zip(self.tenor_years, self.tenor_years[1:], strict=False):
            if right <= left:
                raise CurveObjectError("tenor_years must be strictly increasing.")

    def as_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "tenor_years": list(self.tenor_years),
            "values": list(self.values),
        }
        if self.dates is not None:
            payload["dates"] = [day.isoformat() for day in self.dates]
        return payload


def canonical_input_digest(inputs: Mapping[str, object]) -> str:
    """SHA-256 of the canonical-JSON serialization of a raw-input mapping.

    Canonical means: keys sorted at every level, ``(",", ":")`` separators,
    ASCII-only, NaN/Infinity rejected (``allow_nan=False`` — a non-finite
    input is a data error, not something to hash around). Floats serialize by
    Python's shortest-round-trip repr, which is deterministic across runs and
    platforms for IEEE-754 doubles.
    """
    payload = json.dumps(
        inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class CurveBuildResult:
    """One immutable curve build: definition + nodes + QA + input digest.

    Equality is value equality across every field, so 'the same build' is a
    checkable property: two runs over identical inputs must compare equal —
    the reproducibility invariant of spec §5 ("given the methodology version +
    raw input snapshot, any published curve is exactly reproducible").
    """

    definition: CurveDefinition
    nodes: CurveNodes
    qa: ForwardQaResult | None
    input_digest: str = field(default="")

    @classmethod
    def create(
        cls,
        definition: CurveDefinition,
        nodes: CurveNodes,
        qa: ForwardQaResult | None,
        raw_inputs: Mapping[str, object],
    ) -> CurveBuildResult:
        """Build a result, digesting definition + raw inputs in one envelope.

        The digest covers the definition payload AND the caller's raw inputs
        (quotes, parameter values), never the fitted outputs — it answers
        "what went in", so that identical inputs are provably identical
        upstream of any numerics.
        """
        digest = canonical_input_digest(
            {"definition": definition.as_payload(), "inputs": dict(raw_inputs)}
        )
        return cls(definition=definition, nodes=nodes, qa=qa, input_digest=digest)
