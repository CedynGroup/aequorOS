"""Resolving the governed numbers the Pillar 2 engine reads, and refusing without them.

Founder directive D-024: every regulatory or methodological number is a row in
the control plane that staff propose and approve in the console, effective
dated. This module is the only place the Pillar 2 services touch that plane. It
resolves a code at the cycle's as-of date, checks the body against its declared
shape before anybody parses it, and hands back typed bundles.

Three refusals, all typed, none of them a substitution:

* **missing_parameter** — no approved row for this institution at this date.
  The message names the code so staff know which console row to fill.
* **parameter_malformed** — an approved row whose body does not match its
  shape, naming the path inside it. A malformed band table must not become a
  500; it is a governance failure with an address.
* **basis_mismatch** — a like-for-like type error from the domain, surfaced
  rather than swallowed, because silently comparing a baseline with a stressed
  figure is the mistake the domain types exist to stop.

Per-year floors (DV-005) are resolved here too. A projection's year three is
measured against the floor in force at that year end, not today's, because a
capital plan that quietly measured 2029 against 2026's regime would report
headroom that does not exist.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.domain.icaap.pillar2 import bands as band_domain
from app.domain.icaap.pillar2.types import MissingParameter, ParameterUse
from app.domain.policy import parameter_shapes
from app.models import Bank
from app.services import regulatory_parameters

#: Rows whose citation begins with this marker are invented starting points,
#: not published benchmarks (M14). Every surface that shows such a figure has
#: to say so.
REPRESENTATIVE_PREFIX = "REPRESENTATIVE:"
PENDING = "pending"

P2_PARAMETER_CODES: frozenset[str] = regulatory_parameters.P2_PARAMETER_CODES

_PURPOSE = "This ICAAP Pillar 2 calculation reads a governed figure."


def missing_parameter(param_code: str, detail: str = _PURPOSE) -> HTTPException:
    """The one refusal shape for an unresolvable governed figure (D-024 §4)."""
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "error_code": "missing_parameter",
            "param_code": param_code,
            "message": (
                f"{detail} It is a governed parameter ({param_code}) with no approved row "
                "for this institution — staff configure it in the operator console."
            ),
        },
    )


def parameter_malformed(param_code: str, path: str, message: str) -> HTTPException:
    """An approved row whose body cannot be read as the shape its code declares."""
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "error_code": "parameter_malformed",
            "param_code": param_code,
            "path": path,
            "message": (
                f"The approved value of {param_code} does not match the shape that code "
                f"requires ({path}: {message}). Staff correct it in the operator console."
            ),
        },
    )


def basis_mismatch(argument: str) -> HTTPException:
    """A baseline figure was handed where a stressed one belongs, or the reverse."""
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "error_code": "basis_mismatch",
            "argument": argument,
            "message": (
                "Baseline and stressed Pillar 2 figures were compared against each "
                "other. They are not like for like, so the comparison is refused."
            ),
        },
    )


def from_missing(exc: MissingParameter) -> HTTPException:
    """Map the domain's refusal onto the API's, keeping the code."""
    return missing_parameter(exc.param_code)


@dataclass(frozen=True)
class ParameterRow:
    """One resolved governed row, with everything a reader must be told."""

    code: str
    value: Decimal | None
    value_json: dict[str, Any] | None
    unit: str
    confirmation_status: str
    source_citation: str
    effective_from: date
    parameter_id: str

    @property
    def representative(self) -> bool:
        return self.source_citation.startswith(REPRESENTATIVE_PREFIX)

    @property
    def pending(self) -> bool:
        return self.confirmation_status == PENDING

    @property
    def printable(self) -> str | None:
        if self.value is None:
            return None
        value = self.value
        return str(
            value.quantize(Decimal(1)) if value == value.to_integral_value() else value.normalize()
        )

    def provenance(self) -> dict[str, Any]:
        return {
            "param_code": self.code,
            "value": self.printable,
            "unit": self.unit,
            "confirmation_status": self.confirmation_status,
            "representative": self.representative,
            "source_citation": self.source_citation,
            "effective_from": self.effective_from.isoformat(),
            "parameter_id": self.parameter_id,
        }


def _row(resolved: regulatory_parameters.ResolvedParameter) -> ParameterRow:
    return ParameterRow(
        code=resolved.param_code,
        value=resolved.value,
        value_json=dict(resolved.value_json) if resolved.value_json else None,
        unit=resolved.unit,
        confirmation_status=resolved.confirmation_status,
        source_citation=resolved.source_citation,
        effective_from=resolved.effective_from,
        parameter_id=str(resolved.parameter_id),
    )


@dataclass(frozen=True)
class P2Parameters:
    """Every governed code this cycle could resolve, at one as-of date."""

    as_of: date
    rows: Mapping[str, ParameterRow]
    missing: frozenset[str]

    def has(self, code: str) -> bool:
        return code in self.rows

    def optional(self, code: str) -> ParameterRow | None:
        return self.rows.get(code)

    def require(self, code: str) -> ParameterRow:
        row = self.rows.get(code)
        if row is None:
            raise missing_parameter(code)
        return row

    def decimal(self, code: str) -> Decimal:
        row = self.require(code)
        if row.value is None:
            raise missing_parameter(code)
        return row.value

    def optional_decimal(self, code: str) -> Decimal | None:
        row = self.rows.get(code)
        return None if row is None else row.value

    def integer(self, code: str) -> int:
        value = self.decimal(code)
        if value != value.to_integral_value():
            raise missing_parameter(code)
        return int(value)

    def optional_integer(self, code: str) -> int | None:
        value = self.optional_decimal(code)
        if value is None or value != value.to_integral_value():
            return None
        return int(value)

    def flag(self, code: str) -> bool:
        return self.decimal(code) != Decimal(0)

    def optional_flag(self, code: str) -> bool | None:
        value = self.optional_decimal(code)
        return None if value is None else value != Decimal(0)

    def body(self, code: str) -> Mapping[str, Any]:
        row = self.require(code)
        if row.value_json is None:
            raise missing_parameter(code)
        return row.value_json

    def optional_body(self, code: str) -> Mapping[str, Any] | None:
        row = self.rows.get(code)
        return None if row is None or row.value_json is None else row.value_json

    def band_table(self, code: str) -> band_domain.BandTable:
        """A governed band table, parsed by the domain's own parser."""
        try:
            return band_domain.parse_band_table(self.body(code), param_code=code)
        except band_domain.BandTableError as exc:
            index = getattr(exc, "index", None)
            path = "bands" if index is None else f"bands[{index}]"
            raise parameter_malformed(code, path, exc.code) from exc

    def uses(self, codes: Iterable[str]) -> list[dict[str, Any]]:
        """Provenance rows for the codes a result rested on, in a stable order."""
        out: list[dict[str, Any]] = []
        for code in sorted(set(codes)):
            row = self.rows.get(code)
            if row is not None:
                out.append(row.provenance())
        return out

    def provenance_for(self, uses: Sequence[ParameterUse]) -> list[dict[str, Any]]:
        """Attach the row provenance to the domain's code-only parameter uses."""
        roles: dict[str, list[str]] = {}
        for use in uses:
            roles.setdefault(use.code, [])
            if use.role is not None and use.role not in roles[use.code]:
                roles[use.code].append(use.role)
        out: list[dict[str, Any]] = []
        for code in sorted(roles):
            row = self.rows.get(code)
            entry: dict[str, Any] = (
                {"param_code": code, "value": None, "resolved": False}
                if row is None
                else {**row.provenance(), "resolved": True}
            )
            entry["roles"] = roles[code]
            out.append(entry)
        return out

    def representative_codes(self, codes: Iterable[str]) -> tuple[str, ...]:
        return tuple(
            code
            for code in sorted(set(codes))
            if (row := self.rows.get(code)) is not None and row.representative
        )

    def pending_codes(self, codes: Iterable[str]) -> tuple[str, ...]:
        return tuple(
            code
            for code in sorted(set(codes))
            if (row := self.rows.get(code)) is not None and row.pending
        )

    def row_ids(self, codes: Iterable[str]) -> dict[str, str]:
        """``code -> parameter row id`` — the staleness key for a computation."""
        return {
            code: row.parameter_id
            for code in sorted(set(codes))
            if (row := self.rows.get(code)) is not None
        }


def resolve_p2(
    db: Session,
    bank: Bank,
    *,
    as_of: date,
    codes: Iterable[str] | None = None,
) -> P2Parameters:
    """Resolve the governed P2 codes at ``as_of``, shape-checking every body.

    Nothing is refused here for being absent: a method that needs a code it
    did not get raises its own ``missing_parameter`` naming that code, so the
    operator is told about the row they actually need rather than the first
    one the resolver happened to miss.
    """
    wanted = sorted(set(codes) if codes is not None else P2_PARAMETER_CODES)
    # REPORT plane, not calculation (D-078 residual; architecture audit M4).
    # These ARE the governed numbers the ICAAP methods measure against — but the
    # ICAAP plane seals no ``RegulatoryRun``, so there is no run for these rows
    # to be the provenance OF. Recording them fed an ambient session ledger that
    # the next sealed run drains, and the only thing keeping that harmless was
    # the unstated invariant "no ICAAP session seals a run". The report records
    # what it read on its own snapshot instead (``snapshot._parameter_provenance``,
    # which is inside ``package_digest``), which is a stronger record than the
    # ledger: it is bound to the document rather than to whichever run came next.
    resolver = regulatory_parameters.PrefetchedParameterResolver.load(
        db, bank, as_of_dates=[as_of], record=False
    )
    rows: dict[str, ParameterRow] = {}
    missing: list[str] = []
    for code in wanted:
        resolved = resolver.try_resolve(code, as_of=as_of)
        if resolved is None:
            missing.append(code)
            continue
        try:
            parameter_shapes.validate(code, resolved.value, resolved.value_json)
        except parameter_shapes.ParameterShapeError as exc:
            raise parameter_malformed(code, exc.path, exc.message) from exc
        rows[code] = _row(resolved)
    return P2Parameters(as_of=as_of, rows=rows, missing=frozenset(missing))


def resolve_by_date(
    db: Session,
    bank: Bank,
    *,
    dates: Sequence[date],
    codes: Iterable[str],
) -> dict[date, P2Parameters]:
    """The same codes resolved at each of several dates (DV-005, per-year).

    One prefetch covers the whole window, so a five-year projection costs one
    query rather than five, and each year still gets the generation in force
    at ITS year end.
    """
    wanted = sorted(set(codes))
    if not dates:
        return {}
    # REPORT plane — see :func:`resolve_p2`.
    resolver = regulatory_parameters.PrefetchedParameterResolver.load(
        db, bank, as_of_dates=dates, record=False
    )
    out: dict[date, P2Parameters] = {}
    for as_of in sorted(set(dates)):
        rows: dict[str, ParameterRow] = {}
        missing: list[str] = []
        for code in wanted:
            resolved = resolver.try_resolve(code, as_of=as_of)
            if resolved is None:
                missing.append(code)
                continue
            try:
                parameter_shapes.validate(code, resolved.value, resolved.value_json)
            except parameter_shapes.ParameterShapeError as exc:
                raise parameter_malformed(code, exc.path, exc.message) from exc
            rows[code] = _row(resolved)
        out[as_of] = P2Parameters(as_of=as_of, rows=rows, missing=frozenset(missing))
    return out


__all__ = [
    "P2_PARAMETER_CODES",
    "PENDING",
    "REPRESENTATIVE_PREFIX",
    "P2Parameters",
    "ParameterRow",
    "basis_mismatch",
    "from_missing",
    "missing_parameter",
    "parameter_malformed",
    "resolve_by_date",
    "resolve_p2",
]
