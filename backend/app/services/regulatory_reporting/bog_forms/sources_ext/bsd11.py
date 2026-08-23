"""BSD11 (Statutory Return) resolvers — the institution's own registers.

One resolver, ``bsd11.register``, feeds the per-row cells of BSD11's register
sheets from platform state that already exists:

- the **related-party register** (``related_parties`` / ``related_party_roles`` /
  ``shareholdings`` — the ORASS corporate-profile mirror, docs/submission_pipeline_plan.md
  §W4) for *who* the directors and officers are (name, date appointed, shares held);
- **canonical positions** for *what* they owe the bank (present balance, rate,
  facility type) — a counterparty is linked to a register party by the SAME rule the
  Large-Exposures return documents: a normalised-name match against the register
  (``le_generation._funding_entities``); nothing is inferred beyond that;
- canonical positions again for the Sheet-6 ranking of the largest customer exposures
  (advances, credits and guarantees; Section 47), with **net worth := BSD2 line 16
  "Shareholders' Funds" (D135)** of the same reporting date — BoG's own balance-sheet
  figure, computed first as a declared dependency — so the percentage cells are
  exposure ÷ net worth × 100 exactly as the sheet header states.

Params: ``{"register": "directors" | "officers", "rank": N}`` (N-th party in a stable
order), ``{"register": "summary", "group": "directors" | "officers"}`` (Sheet-1 aggregate),
``{"register": "large_exposures", "rank": N}``. The **column key** of the bound cell
(``rc.column``: ``name``, ``appointed``, ``balance``, ``pct_total`` …) selects the field.
Text is returned for text cells (name, dates as ISO text, share particulars); ``None``
for anything the registers do not hold, so the cell stays ``input_required`` with the
row's note. Read-only; no BoG figure is computed by a new rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from app.domain.ingestion.constants import INCLUDED_VALIDATION_STATUSES
from app.models.canonical import (
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
)
from app.models.institution_profile import RelatedParty, RelatedPartyRole, Shareholding
from app.services import jurisdictions

from ..sources import ResolveContext, resolver

#: Roles that make a related party a *director* for Sheets 1/2/4 (Guide BSD11:
#: "each Director of the bank (including Chairman and Managing Directors)").
DIRECTOR_ROLES: frozenset[str] = frozenset({"director", "board_chairman"})
#: Roles that make a related party an *officer* (Sections 44/45 "officers"): key
#: management as recorded in the ORASS mirror. A person who is also a director is
#: reported once, as a director.
OFFICER_ROLES: frozenset[str] = frozenset(
    {
        "chief_executive_officer",
        "chief_finance_officer",
        "chief_risk_officer",
        "chief_compliance_officer",
        "head_internal_audit",
        "other_key_management",
        "board_secretary",
    }
)
#: On-balance-sheet credit exposure (the platform's LE definition) and the
#: off-balance-sheet credit types the sheet calls "credits and guarantees".
ON_BALANCE_TYPES: tuple[str, ...] = ("LOAN", "INTERBANK_PLACEMENT", "SECURITY_HOLDING")
OFF_BALANCE_TYPES: tuple[str, ...] = ("LC_GUARANTEE", "COMMITMENT_UNDRAWN")
#: Facilities to directors/officers: overdrafts, loans and other advances.
FACILITY_TYPES: tuple[str, ...] = ("LOAN",)
#: Sovereign / central-bank / government counterparties are not "customers" of
#: the Section 47 list (same exemption basis as the LE return).
EXEMPT_COUNTERPARTY_TYPES: frozenset[str] = frozenset(
    {"SOVEREIGN", "CENTRAL_BANK", "GOVERNMENT_ENTITY"}
)
#: BSD2 cell that carries Shareholders' Funds (paid-up capital + reserves) — the
#: net-worth denominator for Sheet-6.
NET_WORTH_CELL: tuple[str, str, str] = ("BSD2", "BSD2", "D135")

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


# ---------------------------------------------------------------------------
# small pure helpers
# ---------------------------------------------------------------------------


def normalized_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None


def _fmt_number(value: Decimal) -> str:
    if value == value.to_integral_value():
        return f"{int(value):,}"
    return f"{value:,.2f}"


def _amount_ghs(
    snapshot: CanonicalPositionSnapshot,
    position: CanonicalPosition,
    base_currency: str,
    *,
    prefer_notional: bool,
) -> Decimal:
    """Cedi amount of a snapshot under the platform's documented convention:
    an ingested ``balance_ghs``/``notional_ghs`` attribute wins; a base-currency
    book uses the raw figure; a foreign-currency book WITHOUT an ingested
    conversion contributes zero (never an invented rate)."""
    attributes = snapshot.attributes or {}
    if prefer_notional:
        ingested = _dec(attributes.get("notional_ghs"))
        if ingested is not None:
            return ingested
        if position.currency == base_currency and snapshot.notional is not None:
            return Decimal(str(snapshot.notional))
    ingested = _dec(attributes.get("balance_ghs"))
    if ingested is not None:
        return ingested
    if position.currency == base_currency:
        return Decimal(str(snapshot.balance or _ZERO))
    return _ZERO


# ---------------------------------------------------------------------------
# register: parties (directors / officers)
# ---------------------------------------------------------------------------


@dataclass
class _Party:
    id: UUID
    name: str
    party_type: str
    contact: dict[str, Any]
    roles: list[RelatedPartyRole] = field(default_factory=list)
    holdings: list[Shareholding] = field(default_factory=list)

    @property
    def role_codes(self) -> set[str]:
        return {r.role for r in self.roles}

    def first_appointed(self, roles: frozenset[str]) -> date | None:
        dates = [r.appointed_on for r in self.roles if r.role in roles and r.appointed_on]
        return min(dates) if dates else None


def _load_parties(rc: ResolveContext) -> list[_Party]:
    key = "bsd11:parties"
    cached = rc.cache.get(key)
    if cached is not None:
        return cached
    parties = [
        _Party(id=p.id, name=p.full_name, party_type=p.party_type, contact=dict(p.contact or {}))
        for p in rc.db.scalars(
            select(RelatedParty)
            .where(
                RelatedParty.organization_id == rc.ctx.organization_id,
                RelatedParty.bank_id == rc.bank.id,
                RelatedParty.status == "active",
            )
            .order_by(RelatedParty.full_name, RelatedParty.id)
        )
    ]
    by_id = {p.id: p for p in parties}
    if by_id:
        for role in rc.db.scalars(
            select(RelatedPartyRole).where(
                RelatedPartyRole.organization_id == rc.ctx.organization_id,
                RelatedPartyRole.party_id.in_(list(by_id)),
            )
        ):
            by_id[role.party_id].roles.append(role)
        for holding in rc.db.scalars(
            select(Shareholding).where(
                Shareholding.organization_id == rc.ctx.organization_id,
                Shareholding.party_id.in_(list(by_id)),
            )
        ):
            by_id[holding.party_id].holdings.append(holding)
    rc.cache[key] = parties
    return parties


def _ranked(
    parties: list[_Party], roles: frozenset[str], *, exclude: frozenset[str]
) -> list[_Party]:
    """Individuals holding one of ``roles`` (and none of ``exclude``), in a stable
    order: earliest appointment first (unknown dates last), then name."""
    selected = [
        p
        for p in parties
        if p.party_type == "individual" and (p.role_codes & roles) and not (p.role_codes & exclude)
    ]
    selected.sort(
        key=lambda p: (
            p.first_appointed(roles) is None,
            p.first_appointed(roles) or date.max,
            normalized_name(p.name),
            str(p.id),
        )
    )
    return selected


def directors(rc: ResolveContext) -> list[_Party]:
    return _ranked(_load_parties(rc), DIRECTOR_ROLES, exclude=frozenset())


def officers(rc: ResolveContext) -> list[_Party]:
    return _ranked(_load_parties(rc), OFFICER_ROLES, exclude=DIRECTOR_ROLES)


def _shares_text(party: _Party) -> str | None:
    """ "NUMBER AND PERCENTAGE OF SHARES HELD": the register's holdings when the
    party is a shareholder; the register positively says "no shareholder role" →
    Nil; a shareholder role with no holding rows is incomplete → None."""
    if party.holdings:
        number = sum((Decimal(str(h.number_of_shares)) for h in party.holdings), _ZERO)
        pct = sum((Decimal(str(h.pct_shareholding)) for h in party.holdings), _ZERO)
        return f"{_fmt_number(number)} ({pct:.2f}%)"
    if "shareholder" in party.role_codes:
        return None
    return "Nil"


def _name_and_address(party: _Party) -> str:
    address = party.contact.get("address") or party.contact.get("postal_address")
    if isinstance(address, dict):
        address = ", ".join(str(v) for v in address.values() if v)
    return f"{party.name}, {address}" if address else party.name


# ---------------------------------------------------------------------------
# canonical facilities (current generation, latest snapshot on/before period end)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Facility:
    position: CanonicalPosition
    snapshot: CanonicalPositionSnapshot
    counterparty: CanonicalCounterparty | None
    product: CanonicalProduct | None
    amount_ghs: Decimal


def _load_facilities(rc: ResolveContext, position_types: tuple[str, ...]) -> list[_Facility]:
    key = f"bsd11:facilities:{','.join(position_types)}"
    cached = rc.cache.get(key)
    if cached is not None:
        return cached
    latest = (
        select(
            CanonicalPositionSnapshot.position_id.label("pid"),
            func.max(CanonicalPositionSnapshot.as_of_date).label("as_of"),
        )
        .where(
            CanonicalPositionSnapshot.organization_id == rc.ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == rc.bank.id,
            CanonicalPositionSnapshot.as_of_date <= rc.period.period_end,
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPositionSnapshot.withdrawn_at.is_(None),
            # The "latest as-of" must be the latest ADMITTED one (re-audit D-4).
            # Without this the newest date could come from a pending/error
            # snapshot that the outer query then refuses, and the facility would
            # vanish from the return instead of falling back to its last
            # accepted snapshot — a silent omission, not a visible refusal.
            CanonicalPositionSnapshot.validation_status.in_(INCLUDED_VALIDATION_STATUSES),
        )
        .group_by(CanonicalPositionSnapshot.position_id)
        .subquery()
    )
    records = rc.db.execute(
        select(
            CanonicalPositionSnapshot, CanonicalPosition, CanonicalCounterparty, CanonicalProduct
        )
        .join(
            latest,
            (latest.c.pid == CanonicalPositionSnapshot.position_id)
            & (latest.c.as_of == CanonicalPositionSnapshot.as_of_date),
        )
        .join(CanonicalPosition, CanonicalPosition.id == CanonicalPositionSnapshot.position_id)
        .outerjoin(
            CanonicalCounterparty,
            CanonicalCounterparty.id == CanonicalPositionSnapshot.counterparty_id,
        )
        .outerjoin(CanonicalProduct, CanonicalProduct.id == CanonicalPositionSnapshot.product_id)
        .where(
            CanonicalPositionSnapshot.organization_id == rc.ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == rc.bank.id,
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPositionSnapshot.withdrawn_at.is_(None),
            CanonicalPositionSnapshot.validation_status.in_(INCLUDED_VALIDATION_STATUSES),
            CanonicalPosition.superseded_by.is_(None),
            CanonicalPosition.withdrawn_at.is_(None),
            CanonicalPosition.position_type.in_(list(position_types)),
        )
        .order_by(CanonicalPositionSnapshot.source_reference)
    ).all()
    base = jurisdictions.base_currency(rc.bank)
    facilities = [
        _Facility(
            position=position,
            snapshot=snapshot,
            counterparty=counterparty,
            product=product,
            amount_ghs=_amount_ghs(
                snapshot,
                position,
                base,
                prefer_notional=position.position_type in OFF_BALANCE_TYPES,
            ),
        )
        for snapshot, position, counterparty, product in records
    ]
    rc.cache[key] = facilities
    return facilities


def _party_facilities(rc: ResolveContext, party: _Party) -> list[_Facility]:
    wanted = normalized_name(party.name)
    return [
        f
        for f in _load_facilities(rc, FACILITY_TYPES)
        if f.counterparty is not None and normalized_name(f.counterparty.name) == wanted
    ]


def _party_field(rc: ResolveContext, party: _Party, column: str) -> Any:  # noqa: PLR0911
    if column == "name":
        return _name_and_address(party)
    if column == "appointed":
        appointed = party.first_appointed(DIRECTOR_ROLES | OFFICER_ROLES)
        return appointed.isoformat() if appointed else None
    if column == "shares":
        return _shares_text(party)
    facilities = _party_facilities(rc, party)
    if column == "balance":
        return sum((f.amount_ghs for f in facilities), _ZERO) if facilities else None
    if column == "rate":
        # a single facility has one contractual rate; several do not (bank states them)
        if len(facilities) == 1 and facilities[0].snapshot.interest_rate is not None:
            return Decimal(str(facilities[0].snapshot.interest_rate)) * _HUNDRED
        return None
    if column == "facility_type":
        names = sorted({f.product.name for f in facilities if f.product is not None})
        return ", ".join(names) if names else None
    if column == "security":
        classes = sorted(
            {
                str((f.snapshot.attributes or {}).get("crm_collateral_class"))
                for f in facilities
                if (f.snapshot.attributes or {}).get("crm_collateral_class")
            }
        )
        return ", ".join(classes) if classes else None
    # employment (full/part time), interests in other companies, secured /
    # unsecured / guaranteed split, date approved by the board: not held by any
    # register → input_required (the row note names the register).
    return None


# ---------------------------------------------------------------------------
# Section 47: largest customer exposures as % of net worth
# ---------------------------------------------------------------------------


@dataclass
class _Entity:
    key: str
    name: str
    on_balance: Decimal = _ZERO
    off_balance: Decimal = _ZERO
    security: Decimal | None = None

    @property
    def total(self) -> Decimal:
        return self.on_balance + self.off_balance


def _entity_key(counterparty: CanonicalCounterparty) -> tuple[str, str]:
    if counterparty.group_reference:
        return counterparty.group_reference, f"Group: {counterparty.group_reference}"
    return f"cp:{counterparty.id}", " ".join(counterparty.name.split())


def largest_exposures(rc: ResolveContext) -> list[_Entity]:
    """Customers (single counterparty, or connected group by ``group_reference``)
    ranked by total on- + off-balance-sheet exposure, sovereign/central-bank/
    government counterparties and counterparty-less positions excluded."""
    key = "bsd11:entities"
    cached = rc.cache.get(key)
    if cached is not None:
        return cached
    entities: dict[str, _Entity] = {}
    for facility in _load_facilities(rc, ON_BALANCE_TYPES + OFF_BALANCE_TYPES):
        cpt = facility.counterparty
        if cpt is None or cpt.counterparty_type in EXEMPT_COUNTERPARTY_TYPES:
            continue
        ekey, name = _entity_key(cpt)
        entity = entities.setdefault(ekey, _Entity(key=ekey, name=name))
        if facility.position.position_type in OFF_BALANCE_TYPES:
            entity.off_balance += facility.amount_ghs
        else:
            entity.on_balance += facility.amount_ghs
        attributes = facility.snapshot.attributes or {}
        for attr in ("crm_collateral_ghs", "crm_guarantee_ghs"):
            value = _dec(attributes.get(attr))
            if value is not None:
                entity.security = (entity.security or _ZERO) + value
    ranked = sorted(entities.values(), key=lambda e: (-e.total, normalized_name(e.name), e.key))
    rc.cache[key] = ranked
    return ranked


def net_worth(rc: ResolveContext) -> Decimal | None:
    form, sheet, ref = NET_WORTH_CELL
    dep = rc.dependencies.get(form)
    if dep is None:
        return None
    raw = _dec(dep.get((sheet, ref)))
    return raw if raw is not None and raw > _ZERO else None


def _pct(numerator: Decimal, denominator: Decimal | None) -> Decimal | None:
    if denominator is None:
        return None
    return numerator / denominator * _HUNDRED


def _exposure_field(rc: ResolveContext, entity: _Entity, column: str) -> Any:  # noqa: PLR0911
    if column == "name":
        return entity.name
    if column == "on_balance":
        return entity.on_balance
    if column == "off_balance":
        return entity.off_balance
    if column == "total":
        return entity.total
    if column == "security":
        return entity.security
    worth = net_worth(rc)
    if column == "pct_total":
        return _pct(entity.total, worth)
    if entity.security is None:
        return None
    secured = min(entity.total, entity.security)
    if column == "pct_secured":
        return _pct(secured, worth)
    if column == "pct_unsecured":
        return _pct(entity.total - secured, worth)
    return None


# ---------------------------------------------------------------------------
# the resolver
# ---------------------------------------------------------------------------


@resolver("bsd11.register")
def _register(rc: ResolveContext, params: dict[str, Any]) -> Any:
    register = params["register"]
    column = rc.column
    if register in ("directors", "officers"):
        parties = directors(rc) if register == "directors" else officers(rc)
        rank = int(params["rank"])
        if rank < 1 or rank > len(parties):
            return None
        return _party_field(rc, parties[rank - 1], column)
    if register == "summary":
        group = params["group"]
        parties = directors(rc) if group == "directors" else officers(rc)
        if column != "current":
            # opening balance / granted / repaid / written off are half-year
            # movements the platform's monthly snapshots do not hold
            return None
        amounts = [f.amount_ghs for p in parties for f in _party_facilities(rc, p)]
        return sum(amounts, _ZERO) if amounts else None
    if register == "large_exposures":
        entities = largest_exposures(rc)
        rank = int(params["rank"])
        if rank < 1 or rank > len(entities):
            return None
        return _exposure_field(rc, entities[rank - 1], column)
    msg = f"bsd11.register: unknown register {register!r}"
    raise ValueError(msg)
