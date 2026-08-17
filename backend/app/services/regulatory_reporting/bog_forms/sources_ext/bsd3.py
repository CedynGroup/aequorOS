"""BSD3A / BSD3B resolvers — the N-th largest depositor / exposure.

The three BSD3 sheets are RANKED ROSTERS: twenty largest depositors, ten
largest monetary-sector exposures, fifty largest non-monetary-sector exposures
(Guide BSD3 items 1, 3, 4). Nothing here computes a BoG figure by a new rule:
the resolvers aggregate the canonical position book per counterparty (or per
connected group — Guide: "placements from different depositors within the same
group should be aggregated"; "separate companies within the same group treated
as a single entity"), sort, and hand back one attribute of the N-th entity.

Reuse (documented): the canonical slice and the connected-counterparty
identity are the Large-Exposures / LMT engine's own
(``le_generation._load_canonical_rows`` — current generation, accepted/warning
snapshots AT the period end, ``balance_ghs`` cedi-equivalents exactly like
``fact_derivation``; ``le_generation._entity_identity`` — group_reference first,
then the single counterparty, then the issuer attribute). BSD3A therefore
reconciles to LE-MONTHLY / LMT by construction; a position the LE engine cannot
attribute (no counterparty, no issuer) is excluded here too.

Population rules (from the Wave-2 brief; recorded in docs/bog_returns/bsd3a_line_map.md):

- ``depositor``            — DEPOSIT positions (INTERBANK_BORROWING is BSD2 §21–25, not a
  "depositor" line).
- ``monetary_exposure``    — exposure positions whose counterparty type is
  BANK_OECD / BANK_NON_OECD / CENTRAL_BANK / NBFI (Guide item 4: "banks, discount
  houses, building societies and other financial institutions participating in
  the money market").
- ``non_monetary_exposure`` — exposure positions with any other (or no)
  counterparty type.

Exposure = drawn (LOAN, INTERBANK_PLACEMENT, SECURITY_HOLDING balances) +
undrawn (COMMITMENT_UNDRAWN) + other contingent (LC_GUARANTEE) — the Guide's
"total exposure column should include drawn and undrawn facilities and other
contingent liabilities"; off-balance amounts take ``notional_ghs`` (else the
balance), the LMT Table 2 convention. Foreign / cedi components split on the
POSITION currency against the bank's base currency (Guide item 6: "the cedi
equivalent of foreign component"). Amounts are BASE units (cedis).

Resolvers::

    bsd3.rank   {kind, rank, field?}   → attribute of the rank-th entity (field
                                          defaults to the bound column key)
    bsd3.count  {kind}                 → number of distinct counterparties in
                                          the population (unscaled count)

Fields: name · account_type · maturity · currency · foreign · cedi · amount
(= total) · on_balance / drawn · undrawn · contingent · exposure_type.
Text fields return ``str``; a rank beyond the population returns ``None`` for
every field (the official row stays blank).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from ..sources import ResolveContext, resolver

_ZERO = Decimal("0")

#: Guide item 4 — the monetary sector, on the canonical counterparty taxonomy.
MONETARY_SECTOR_TYPES: frozenset[str] = frozenset(
    {"BANK_OECD", "BANK_NON_OECD", "CENTRAL_BANK", "NBFI"}
)
DEPOSIT_POSITION_TYPES: tuple[str, ...] = ("DEPOSIT",)
DRAWN_POSITION_TYPES: tuple[str, ...] = ("LOAN", "INTERBANK_PLACEMENT", "SECURITY_HOLDING")
UNDRAWN_POSITION_TYPES: tuple[str, ...] = ("COMMITMENT_UNDRAWN",)
CONTINGENT_POSITION_TYPES: tuple[str, ...] = ("LC_GUARANTEE",)
EXPOSURE_POSITION_TYPES: tuple[str, ...] = (
    *DRAWN_POSITION_TYPES,
    *UNDRAWN_POSITION_TYPES,
    *CONTINGENT_POSITION_TYPES,
)
_ALL_POSITION_TYPES: tuple[str, ...] = (*DEPOSIT_POSITION_TYPES, *EXPOSURE_POSITION_TYPES)

KINDS: tuple[str, ...] = ("depositor", "monetary_exposure", "non_monetary_exposure")

#: Canonical position type → the Guide's exposure vocabulary (Sheet 3 "Type of
#: Exposure"). Presentation of the canonical taxonomy, not a classification.
_EXPOSURE_LABELS: dict[str, str] = {
    "LOAN": "Loan / advance",
    "INTERBANK_PLACEMENT": "Placement",
    "SECURITY_HOLDING": "Investment",
    "COMMITMENT_UNDRAWN": "Undrawn commitment",
    "LC_GUARANTEE": "Guarantee / contingent",
}


@dataclass
class RankedEntity:
    """One roster line: a counterparty or a connected group, aggregated."""

    key: str
    name: str
    connection: str  # "single" | "group"
    on_balance: Decimal = _ZERO
    undrawn: Decimal = _ZERO
    contingent: Decimal = _ZERO
    foreign: Decimal = _ZERO  # cedi equivalent of the foreign-currency components
    cedi: Decimal = _ZERO
    currencies: set[str] = field(default_factory=set)
    account_types: set[str] = field(default_factory=set)
    position_types: set[str] = field(default_factory=set)
    maturity: date | None = None
    counterparty_ids: set[str] = field(default_factory=set)

    @property
    def total(self) -> Decimal:
        return self.on_balance + self.undrawn + self.contingent


@dataclass(frozen=True)
class Ranking:
    kind: str
    entities: tuple[RankedEntity, ...]  # largest first
    counterparty_count: int  # distinct counterparties in the population

    def at(self, rank: int) -> RankedEntity | None:
        if rank < 1 or rank > len(self.entities):
            return None
        return self.entities[rank - 1]


def _row_amount(row: Any) -> Decimal:
    """LMT Table 2 convention: off-balance rows report their GHS notional."""
    off_balance = (*UNDRAWN_POSITION_TYPES, *CONTINGENT_POSITION_TYPES)
    if row.position_type in off_balance and row.notional_ghs is not None:
        return Decimal(str(row.notional_ghs))
    return Decimal(str(row.balance_ghs))


def _accumulate(entity: RankedEntity, row: Any, base_currency: str) -> None:
    """Fold one canonical row into its roster line."""
    amount = _row_amount(row)
    if row.position_type in UNDRAWN_POSITION_TYPES:
        entity.undrawn += amount
    elif row.position_type in CONTINGENT_POSITION_TYPES:
        entity.contingent += amount
    else:
        entity.on_balance += amount
    if row.currency == base_currency:
        entity.cedi += amount
    else:
        entity.foreign += amount
    entity.currencies.add(row.currency)
    entity.position_types.add(row.position_type)
    if row.deposit_account_type:
        entity.account_types.add(str(row.deposit_account_type).upper())
    if row.contractual_maturity is not None and (
        entity.maturity is None or row.contractual_maturity > entity.maturity
    ):
        entity.maturity = row.contractual_maturity
    if row.counterparty_id is not None:
        entity.counterparty_ids.add(str(row.counterparty_id))


def _in_population(kind: str, row: Any) -> bool:
    if kind == "depositor":
        return row.position_type in DEPOSIT_POSITION_TYPES
    if row.position_type not in EXPOSURE_POSITION_TYPES:
        return False
    monetary = row.counterparty_type in MONETARY_SECTOR_TYPES
    return monetary if kind == "monetary_exposure" else not monetary


def build_ranking(rows: list[Any], kind: str, base_currency: str) -> Ranking:
    """Aggregate ``rows`` (le_generation ``_CanonicalRow`` records) into the
    ranked roster for ``kind`` — largest total first, ties by name."""
    # Local import: le_generation → generation → bog_forms.generation → engine →
    # sources_ext would cycle at module import time.
    from app.services.regulatory_reporting.le_generation import (  # noqa: PLC0415
        _entity_identity,
    )

    if kind not in KINDS:
        msg = f"bsd3: unknown kind {kind!r} (expected one of {KINDS})"
        raise ValueError(msg)
    entities: dict[str, RankedEntity] = {}
    for row in rows:
        if not _in_population(kind, row):
            continue
        identity = _entity_identity(row)
        if identity is None:
            continue  # unattributed — excluded exactly as in LE-MONTHLY
        key, name, connection, _tin = identity
        entity = entities.get(key)
        if entity is None:
            entity = RankedEntity(key=key, name=name, connection=connection)
            entities[key] = entity
        _accumulate(entity, row, base_currency)
    ordered = sorted(entities.values(), key=lambda e: (-e.total, e.name))
    counterparties = {cp for entity in ordered for cp in entity.counterparty_ids}
    return Ranking(kind=kind, entities=tuple(ordered), counterparty_count=len(counterparties))


def _ranking(rc: ResolveContext, kind: str) -> Ranking:
    """Memoised per form computation: one canonical load, three rankings."""
    rows = rc.cache.get("bsd3:rows")
    if rows is None:
        from app.services.regulatory_reporting.le_generation import (  # noqa: PLC0415
            _load_canonical_rows,
        )

        rows = _load_canonical_rows(
            rc.db, rc.ctx, rc.bank, rc.period.period_end, _ALL_POSITION_TYPES
        )
        rc.cache["bsd3:rows"] = rows
    cache_key = f"bsd3:ranking:{kind}"
    ranking = rc.cache.get(cache_key)
    if ranking is None:
        ranking = build_ranking(rows, kind, (rc.bank.currency or "").strip().upper())
        rc.cache[cache_key] = ranking
    return ranking


def _join(values: set[str]) -> str | None:
    return ", ".join(sorted(values)) if values else None


def entity_field(entity: RankedEntity, field_name: str) -> Decimal | str | None:  # noqa: PLR0911 — one return per official column
    """One official column of a roster line (see module docstring)."""
    if field_name == "name":
        return entity.name
    if field_name == "account_type":
        return _join(entity.account_types)
    if field_name == "maturity":
        return entity.maturity.isoformat() if entity.maturity is not None else None
    if field_name == "currency":
        return _join(entity.currencies)
    if field_name == "foreign":
        return entity.foreign
    if field_name == "cedi":
        return entity.cedi
    if field_name in ("amount", "total"):
        return entity.total
    if field_name in ("on_balance", "drawn"):
        return entity.on_balance
    if field_name == "undrawn":
        return entity.undrawn
    if field_name == "contingent":
        return entity.contingent
    if field_name == "exposure_type":
        labels = {_EXPOSURE_LABELS.get(t, t) for t in entity.position_types}
        return "; ".join(sorted(labels)) if labels else None
    msg = f"bsd3.rank: unknown field {field_name!r}"
    raise ValueError(msg)


@resolver("bsd3.rank")
def _rank(rc: ResolveContext, params: dict[str, Any]) -> Decimal | str | None:
    """``{"kind": ..., "rank": N, "field"?: ...}`` → that attribute of the N-th
    largest entity; ``field`` defaults to the bound column key (``rc.column``)."""
    kind = str(params["kind"])
    rank = int(params["rank"])
    field_name = str(params.get("field") or rc.column)
    entity = _ranking(rc, kind).at(rank)
    if entity is None:
        return None
    return entity_field(entity, field_name)


@resolver("bsd3.count")
def _count(rc: ResolveContext, params: dict[str, Any]) -> int:
    """``{"kind": ...}`` → number of distinct counterparties in the population
    (Sheet 1 row 25 "Total no. of depositors"); an unscaled count."""
    return _ranking(rc, str(params["kind"])).counterparty_count
