"""BSD4 resolvers — the sector × borrower-class grid over LOAN positions.

FORM BSD4 (Sectoral Analysis of Overdrafts, Loans and Other Advances) is a
grid: 63 economic-sector rows (the Guide's industry list) × 10 borrower-class
column groups (Central Government … NPISH), each group asking for the
PERFORMING amount, the NON-PERFORMING amount and the number of customers.
Every TOTAL column/row is a template formula. Two annexes re-cut the SAME
loan book by SNA institutional sector (4a) and by geographic region (4b).

Everything here AGGREGATES existing canonical state — LOAN position snapshots,
their counterparties and the documented attribute conventions — it never
derives a sector by heuristics (product names, borrower names, …):

* **Sector** (the row): the documented ``sector`` attribute, read from the
  position snapshot's ``attributes`` (facility-level override) and otherwise
  from the counterparty's ``attributes`` (Guide: the sector categorises "the
  type of industry in which a *customer* is involved"). Values are the keys in
  :data:`SECTOR_ROWS` (or the official leaf label where it is unique). A book
  that carries NO ``sector`` attribute on any LOAN yields ``None`` for every
  cell — the form exports blank with status ``input_required`` ("sector
  classification attribute required"); nothing is guessed. Once the book is
  classified, loans without a recognised sector fall to *9. MISCELLANEOUS*,
  which the Guide defines as "activities of bank customers not elsewhere
  specified or … not adequately described".
* **Borrower class** (the column group): the counterparty type, refined by
  the same attribute conventions BSD2 §8 uses (``borrower_class``
  public_institution / public_enterprise on GOVERNMENT_ENTITY, ``scheme``
  cocoa_syndicated, ``institution_class`` on NBFI) plus ``ownership``
  (foreign / indigenous) for the PRIVATE CORPORATIONS split — see
  :func:`borrower_group`. An explicit ``borrower_class`` naming a column key
  always wins.
* **Performing / non-performing**: the platform's loan-classification rule
  (``fact_derivation._classify_loans``): IFRS 9 stage 3 = non-performing,
  everything else performing.
* **No. of Cust.**: COUNT DISTINCT counterparty per cell — never a sum.
* **Scope** (Guide BSD4 note 2 "domestic accounts only"): counterparties whose
  ``resident`` flag is False are excluded from the main sheet (they are the
  *Non-residents* row of Annex 4a); NULL residency is treated as resident.
  Own books only — the query reads the current generation of snapshots.
* **Units**: cedi equivalents — ``balance_ghs`` attribute when the source
  supplies it, the raw balance for base-currency loans, otherwise the
  platform's preferred FX spot at period end (raw balance if no spot).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from functools import cache
from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from app.models.canonical import (
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
)
from app.services import jurisdictions, market_data_sources

from ..sources import ResolveContext, resolver

# ---------------------------------------------------------------------------
# taxonomy — official rows and column groups of sheet ``BSD4``
# ---------------------------------------------------------------------------

#: Official leaf row → documented ``sector`` attribute key (the Guide's
#: industry list, in template order). Section/subtotal rows are formulas.
SECTOR_ROWS: dict[int, str] = {
    # 1. AGRICULTURE FORESTRY & FISHING
    10: "agriculture.cocoa_production",
    11: "agriculture.livestock_breeding",
    12: "agriculture.poultry_farming",
    13: "agriculture.other",
    14: "agriculture.forestry",
    15: "agriculture.logging",
    16: "agriculture.fishing",
    # 2. MINING & QUARRYING
    19: "mining.bauxite",
    20: "mining.diamonds",
    21: "mining.gold",
    22: "mining.manganese",
    23: "mining.quarrying",
    24: "mining.other",
    # 3. MANUFACTURING — A. FOR EXPORT
    28: "manufacturing.export.food_drink_tobacco",
    29: "manufacturing.export.textiles_clothing_footwear",
    30: "manufacturing.export.sawmilling_wood_processing",
    31: "manufacturing.export.paper_pulp_products",
    32: "manufacturing.export.chemicals_fertilizers",
    33: "manufacturing.export.iron_steel",
    34: "manufacturing.export.boat_ship_building",
    35: "manufacturing.export.motor_vehicles",
    36: "manufacturing.export.other",
    # 3. MANUFACTURING — B. FOR HOME MARKET
    38: "manufacturing.home.food_drink_tobacco",
    39: "manufacturing.home.textiles_clothing_footwear",
    40: "manufacturing.home.sawmilling_wood_processing",
    41: "manufacturing.home.paper_pulp_products",
    42: "manufacturing.home.chemicals_fertilizers",
    43: "manufacturing.home.iron_steel",
    44: "manufacturing.home.boat_ship_building",
    45: "manufacturing.home.motor_vehicles",
    46: "manufacturing.home.other",
    # 4. CONSTRUCTION
    49: "construction.construction_works",
    50: "construction.building_construction",
    # 5. ELECTRICITY, GAS & WATER
    53: "utilities.electricity",
    54: "utilities.gas",
    55: "utilities.water",
    # 6. COMMERCE & FINANCE
    59: "commerce.import.motor_vehicles",
    60: "commerce.import.machinery_heavy_equipment",
    61: "commerce.import.other",
    64: "commerce.export.cocoa",
    65: "commerce.export.timber",
    66: "commerce.export.other",
    67: "commerce.cocoa_marketing",
    68: "commerce.timber_marketing",
    69: "commerce.diamond_marketing",
    70: "commerce.mortgage_financing",
    72: "commerce.ofi.hire_purchase",
    73: "commerce.ofi.insurance",
    74: "commerce.ofi.building_bodies",
    75: "commerce.other",
    # 7. TRANSPORT, STORAGE AND COMMUNICATION
    78: "transport.railway",
    79: "transport.road",
    80: "transport.water",
    81: "transport.air",
    82: "transport.storage_warehousing",
    83: "transport.communications",
    # 8. SERVICES
    86: "services.printing_publishing",
    87: "services.business",
    88: "services.recreation",
    89: "services.personal",
    90: "services.salary_credit",
    91: "services.other_incl_government",
    # 9. MISCELLANEOUS
    93: "miscellaneous",
}

MISCELLANEOUS = "miscellaneous"
SECTOR_KEYS: frozenset[str] = frozenset(SECTOR_ROWS.values())

#: Column group key → (PERFORMING col, NON-PERFORMING col, No. of Cust. col);
#: the group's TOTAL column (D, H, L, …) and the AP:AS grand columns are
#: template formulas. Order = template order (row 6 headers).
BORROWER_GROUPS: dict[str, tuple[str, str, str]] = {
    "central_government": ("B", "C", "E"),
    "public_institutions": ("F", "G", "I"),
    "public_enterprises": ("J", "K", "M"),
    "commercial_banks": ("N", "O", "Q"),
    "other_depository_institutions": ("R", "S", "U"),
    "other_financial_institutions": ("V", "W", "Y"),
    "private_foreign": ("Z", "AA", "AC"),
    "private_indigenous": ("AD", "AE", "AG"),
    "households": ("AH", "AI", "AK"),
    "npish": ("AL", "AM", "AO"),
}

MEASURES: tuple[str, str, str] = ("performing", "non_performing", "customer_count")

#: Explicit ``borrower_class`` attribute values → column group (singular
#: spellings are BSD2's; plurals/aliases accepted for the same meaning).
BORROWER_CLASS_ALIASES: dict[str, str] = {
    "central_government": "central_government",
    "government": "central_government",
    "sovereign": "central_government",
    "public_institution": "public_institutions",
    "public_institutions": "public_institutions",
    "public_enterprise": "public_enterprises",
    "public_enterprises": "public_enterprises",
    "commercial_bank": "commercial_banks",
    "commercial_banks": "commercial_banks",
    "bank": "commercial_banks",
    "other_depository_institution": "other_depository_institutions",
    "other_depository_institutions": "other_depository_institutions",
    "odi": "other_depository_institutions",
    "other_financial_institution": "other_financial_institutions",
    "other_financial_institutions": "other_financial_institutions",
    "ofi": "other_financial_institutions",
    "private_foreign": "private_foreign",
    "private_corporation_foreign": "private_foreign",
    "private_indigenous": "private_indigenous",
    "private_corporation_indigenous": "private_indigenous",
    "household": "households",
    "households": "households",
    "individual": "households",
    "npish": "npish",
}

#: Guide "Other depository institutions": rural banks, credit unions, savings
#: and loans, discount houses, the building society (BSD2's institution_class
#: vocabulary + ``other_depository``).
ODI_INSTITUTION_CLASSES: frozenset[str] = frozenset(
    {
        "rural_bank",
        "credit_union",
        "savings_and_loans",
        "discount_house",
        "building_society",
        "other_depository",
    }
)

_BANK_TYPES: frozenset[str] = frozenset({"BANK_OECD", "BANK_NON_OECD"})
_PRIVATE_TYPES: frozenset[str] = frozenset({"CORPORATE", "SME"})


def _norm(text: Any) -> str:
    """Lower-case, drop a leading "(i)"/"(a)"/"1." token, '&'→'and', collapse."""
    value = str(text or "").strip().lower()
    value = re.sub(r"^\(?[ivx]+\)\s*|^\(?[a-z]\)\s*|^\d+\.\s*", "", value)
    value = value.replace("&", " and ")
    value = re.sub(r"[^a-z0-9 ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _token(value: Any) -> str:
    """An attribute value as a snake_case token (``"Public Institution"`` →
    ``public_institution``)."""
    return re.sub(r"[\s\-]+", "_", str(value or "").strip().lower())


@cache
def _label_aliases() -> dict[str, str]:
    """Official leaf labels (normalised) → key, only where the label is unique
    (the manufacturing sub-lines repeat between EXPORT and HOME MARKET)."""
    from ..layout import load_layout  # noqa: PLC0415 — layout only for aliases

    sheet = load_layout("BSD4").sheet("BSD4")
    seen: dict[str, str | None] = {}
    for row, key in SECTOR_ROWS.items():
        label = _norm(sheet.label_for_row(row))
        seen[label] = None if label in seen else key
    return {label: key for label, key in seen.items() if key is not None}


def sector_key(raw: Any) -> str | None:
    """Documented ``sector`` value → row key; ``None`` when absent/unknown."""
    if raw in (None, ""):
        return None
    text = str(raw).strip().lower()
    if text in SECTOR_KEYS:
        return text
    return _label_aliases().get(_norm(text))


#: Counterparty types that map to a column group without any attribute.
_TYPE_GROUPS: dict[str, str] = {
    "SOVEREIGN": "central_government",
    "BANK_OECD": "commercial_banks",
    "BANK_NON_OECD": "commercial_banks",
    "MULTILATERAL_DEV_BANK": "other_financial_institutions",
    "RETAIL_INDIVIDUAL": "households",
}


def borrower_group(  # noqa: PLR0911 — one early return per Guide borrower class
    counterparty: CanonicalCounterparty | None, attrs: dict[str, Any]
) -> str | None:
    """The BSD4 column group of a loan, or ``None`` when it cannot be placed
    (no counterparty; CENTRAL_BANK / OTHER without an explicit class;
    GOVERNMENT_ENTITY without the public_institution / public_enterprise
    ``borrower_class`` split — exactly BSD2 §8's convention)."""
    explicit = BORROWER_CLASS_ALIASES.get(_token(attrs.get("borrower_class")))
    if explicit is not None:
        return explicit
    if counterparty is None:
        return None
    if _token(attrs.get("scheme")) == "cocoa_syndicated":
        return "public_enterprises"  # BSD2 §8 (c)(i) — the COCOBOD syndication
    ctype = counterparty.counterparty_type
    if ctype == "NBFI":
        klass = _token(attrs.get("institution_class"))
        if klass in ODI_INSTITUTION_CLASSES:
            return "other_depository_institutions"
        return "other_financial_institutions"
    if ctype in _PRIVATE_TYPES:
        if _token(attrs.get("ownership")) == "foreign":
            return "private_foreign"
        return "private_indigenous"
    return _TYPE_GROUPS.get(ctype)


def is_non_performing(snapshot: CanonicalPositionSnapshot) -> bool:
    """The platform's classification rule: IFRS 9 stage 3 = non-performing."""
    return snapshot.ifrs9_stage == 3


# ---------------------------------------------------------------------------
# loan book (memoised per form computation via rc.cache)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Loan:
    snapshot: CanonicalPositionSnapshot
    position: CanonicalPosition
    counterparty: CanonicalCounterparty | None
    amount_ghs: Decimal
    #: merged attributes: counterparty attributes overlaid by snapshot attributes
    attrs: dict[str, Any]

    @property
    def resident(self) -> bool:
        return self.counterparty is None or self.counterparty.resident is not False


def _merged_attrs(
    snapshot: CanonicalPositionSnapshot, counterparty: CanonicalCounterparty | None
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    if counterparty is not None and isinstance(counterparty.attributes, dict):
        merged.update(counterparty.attributes)
    if isinstance(snapshot.attributes, dict):
        merged.update(snapshot.attributes)
    return merged


def _spot(rc: ResolveContext, currency: str, base: str) -> Decimal | None:
    key = f"bsd4:spot:{currency}"
    if key in rc.cache:
        cached = rc.cache[key]
        return cached if isinstance(cached, Decimal) else None
    rate: Decimal | None = None
    try:
        view = market_data_sources.preferred_fx_spot(
            rc.db, rc.ctx.organization_id, rc.bank.id, currency, base, rc.period.period_end
        )
        rate = Decimal(str(view.rate)) if view is not None else None
    except Exception:  # noqa: BLE001 — no spot ⇒ raw balance (documented fallback)
        rate = None
    rc.cache[key] = rate if rate is not None else False
    return rate


def _amount_ghs(
    rc: ResolveContext,
    snapshot: CanonicalPositionSnapshot,
    position: CanonicalPosition,
    attrs: dict[str, Any],
    base: str,
) -> Decimal:
    raw = attrs.get("balance_ghs")
    if raw not in (None, ""):
        try:
            return Decimal(str(raw))
        except ArithmeticError:
            pass
    balance = Decimal(str(snapshot.balance or 0))
    if position.currency == base:
        return balance
    rate = _spot(rc, position.currency, base)
    return balance * rate if rate is not None else balance


def load_loans(rc: ResolveContext) -> list[Loan]:
    """Current-generation LOAN snapshots (latest as-of ≤ period end) with their
    position and counterparty, cedi equivalents attached. Memoised."""
    key = "bsd4:loans"
    cached = rc.cache.get(key)
    if isinstance(cached, list):
        return cached
    base = jurisdictions.base_currency(rc.bank)
    snap = CanonicalPositionSnapshot
    latest = (
        select(snap.position_id.label("pid"), func.max(snap.as_of_date).label("as_of"))
        .where(
            snap.organization_id == rc.ctx.organization_id,
            snap.bank_id == rc.bank.id,
            snap.as_of_date <= rc.period.period_end,
            snap.superseded_by.is_(None),
        )
        .group_by(snap.position_id)
        .subquery()
    )
    stmt = (
        select(snap, CanonicalPosition, CanonicalCounterparty)
        .join(latest, (latest.c.pid == snap.position_id) & (latest.c.as_of == snap.as_of_date))
        .join(CanonicalPosition, CanonicalPosition.id == snap.position_id)
        .outerjoin(
            CanonicalCounterparty,
            (CanonicalCounterparty.id == snap.counterparty_id)
            & (CanonicalCounterparty.organization_id == snap.organization_id),
        )
        .where(
            snap.organization_id == rc.ctx.organization_id,
            snap.bank_id == rc.bank.id,
            snap.superseded_by.is_(None),
            CanonicalPosition.superseded_by.is_(None),
            CanonicalPosition.position_type == "LOAN",
        )
    )
    loans: list[Loan] = []
    for row in rc.db.execute(stmt).all():
        snapshot, position, counterparty = row[0], row[1], row[2]
        attrs = _merged_attrs(snapshot, counterparty)
        loans.append(
            Loan(
                snapshot=snapshot,
                position=position,
                counterparty=counterparty,
                amount_ghs=_amount_ghs(rc, snapshot, position, attrs, base),
                attrs=attrs,
            )
        )
    rc.cache[key] = loans
    return loans


def book_is_sector_classified(loans: list[Loan]) -> bool:
    """True when at least one LOAN carries the documented ``sector`` attribute
    (position or counterparty level). False ⇒ every sector cell is
    input_required — nothing is guessed."""
    return any(sector_key(loan.attrs.get("sector")) is not None for loan in loans)


# ---------------------------------------------------------------------------
# main sheet grid
# ---------------------------------------------------------------------------


@dataclass
class GridCell:
    performing: Decimal = Decimal(0)
    non_performing: Decimal = Decimal(0)
    customers: set[UUID] = field(default_factory=set)

    def measure(self, name: str) -> Decimal | int:
        if name == "customer_count":
            return len(self.customers)
        if name == "non_performing":
            return self.non_performing
        return self.performing


def build_grid(loans: list[Loan]) -> dict[tuple[str, str], GridCell]:
    """(sector key, column group) → cell over RESIDENT, placeable loans.
    Loans without a recognised sector fall to ``miscellaneous`` (Guide);
    loans without a placeable borrower class are left out (documented residual)."""
    grid: dict[tuple[str, str], GridCell] = {}
    for loan in loans:
        if not loan.resident:
            continue
        group = borrower_group(loan.counterparty, loan.attrs)
        if group is None:
            continue
        sector = sector_key(loan.attrs.get("sector")) or MISCELLANEOUS
        cell = grid.setdefault((sector, group), GridCell())
        if is_non_performing(loan.snapshot):
            cell.non_performing += loan.amount_ghs
        else:
            cell.performing += loan.amount_ghs
        if loan.snapshot.counterparty_id is not None:
            cell.customers.add(loan.snapshot.counterparty_id)
    return grid


def _grid(rc: ResolveContext) -> dict[tuple[str, str], GridCell] | None:
    key = "bsd4:grid"
    if key in rc.cache:
        cached = rc.cache[key]
        return cached if isinstance(cached, dict) else None
    loans = load_loans(rc)
    grid = build_grid(loans) if book_is_sector_classified(loans) else None
    rc.cache[key] = grid if grid is not None else False
    return grid


def _column_parts(rc: ResolveContext, params: dict[str, Any]) -> tuple[str, str]:
    """(column group, measure) — explicit params win, else parsed from the line
    map's column key ``"<group>.<measure>"``."""
    group = params.get("borrower_class")
    measure = params.get("measure")
    if group is None or measure is None:
        head, _, tail = rc.column.rpartition(".")
        group = group or head
        measure = measure or tail
    if group not in BORROWER_GROUPS:
        msg = f"bsd4.cell: unknown borrower class {group!r}"
        raise ValueError(msg)
    if measure not in MEASURES:
        msg = f"bsd4.cell: unknown measure {measure!r}"
        raise ValueError(msg)
    return str(group), str(measure)


@resolver("bsd4.cell")
def _bsd4_cell(rc: ResolveContext, params: dict[str, Any]) -> Decimal | int | None:
    """One cell of the BSD4 grid.

    params: ``sector`` (row key from :data:`SECTOR_ROWS`), optional
    ``borrower_class`` / ``measure`` (otherwise from the column key
    ``"<group>.<measure>"``; measure ∈ performing | non_performing |
    customer_count). Returns ``None`` (input_required) while the book carries
    no sector classification; ``0`` for an empty cell of a classified book.
    """
    sector = str(params["sector"])
    if sector not in SECTOR_KEYS:
        msg = f"bsd4.cell: unknown sector {sector!r}"
        raise ValueError(msg)
    group, measure = _column_parts(rc, params)
    grid = _grid(rc)
    if grid is None:
        return None
    cell = grid.get((sector, group))
    if cell is None:
        return 0
    return cell.measure(measure)


# ---------------------------------------------------------------------------
# Annex 4a — SNA institutional sectors (all loans, residents + non-residents)
# ---------------------------------------------------------------------------

ANNEX_4A_ROWS: dict[int, str] = {
    7: "deposit_takers",
    8: "central_bank",
    9: "other_financial_corporations",
    10: "general_government",
    11: "nonfinancial_corporations",
    12: "other_domestic_sectors",
    13: "non_residents",
}


#: BSD4 column group → Annex 4a institutional sector (Guide Annex 4A notes
#: 2–6). Groups absent here (public enterprises, households, NPISH) fall to
#: "other domestic sectors" — "sole proprietorships, individuals, public
#: enterprises and any others not covered in the above".
_GROUP_TO_4A: dict[str, str] = {
    "commercial_banks": "deposit_takers",
    "other_depository_institutions": "deposit_takers",
    "other_financial_institutions": "other_financial_corporations",
    "central_government": "general_government",
    "public_institutions": "general_government",
    "private_foreign": "nonfinancial_corporations",
    "private_indigenous": "nonfinancial_corporations",
}


def annex_4a_sector(loan: Loan) -> str | None:
    """Guide Annex 4A notes 2–6 over the platform's counterparty types.
    ``None`` only when the loan has no counterparty at all."""
    cpty = loan.counterparty
    if cpty is None:
        return None
    if not loan.resident:
        return "non_residents"
    if cpty.counterparty_type == "CENTRAL_BANK":
        return "central_bank"
    group = borrower_group(cpty, loan.attrs)
    return _GROUP_TO_4A.get(group or "", "other_domestic_sectors")


def _annex_totals(
    rc: ResolveContext, cache_key: str, classify: Callable[[Loan], str | None]
) -> tuple[dict[str, Decimal], Decimal]:
    if cache_key in rc.cache:
        return rc.cache[cache_key]
    totals: dict[str, Decimal] = {}
    for loan in load_loans(rc):
        bucket = classify(loan)
        if bucket is None:
            continue
        totals[bucket] = totals.get(bucket, Decimal(0)) + loan.amount_ghs
    grand = sum(totals.values(), Decimal(0))
    rc.cache[cache_key] = (totals, grand)
    return totals, grand


def _annex_value(
    rc: ResolveContext, params: dict[str, Any], totals: dict[str, Decimal], grand: Decimal
) -> Decimal | None:
    """Column ``amount`` → cedi amount; column ``share`` → % of total loans
    (unscaled). ``None`` when the book holds no LOAN positions at all."""
    if not load_loans(rc):
        return None
    bucket = str(params["bucket"])
    _, _, measure = rc.column.rpartition(".")
    measure = str(params.get("measure") or measure or "amount")
    amount = totals.get(bucket, Decimal(0))
    if measure == "share":
        return (amount / grand * 100) if grand else Decimal(0)
    return amount


@resolver("bsd4.annex4a")
def _bsd4_annex4a(rc: ResolveContext, params: dict[str, Any]) -> Decimal | None:
    """Annex 4a row: params ``bucket`` ∈ :data:`ANNEX_4A_ROWS` values; the
    column key ends in ``amount`` (¢) or ``share`` (percentage of total loans)."""
    if params["bucket"] not in ANNEX_4A_ROWS.values():
        msg = f"bsd4.annex4a: unknown bucket {params['bucket']!r}"
        raise ValueError(msg)
    totals, grand = _annex_totals(rc, "bsd4:annex4a", annex_4a_sector)
    return _annex_value(rc, params, totals, grand)


# ---------------------------------------------------------------------------
# Annex 4b — geographic regions (IMF WEO country groups over ISO 3166-1 α2)
# ---------------------------------------------------------------------------

ANNEX_4B_ROWS: dict[int, str] = {
    7: "domestic",
    8: "advanced_economies",
    10: "africa",
    11: "africa.sub_saharan",  # of which
    12: "asia",
    13: "europe",
    14: "europe.fsu",  # of which: FSU, including Russia
    15: "middle_east",
    16: "western_hemisphere",
}
#: Row 9 "Regions, excluding advanced economies" is a heading of rows 10–16
#: (blank in the template, no formula) and is deliberately not bound.


def _iso(codes: str) -> frozenset[str]:
    return frozenset(codes.split())


#: IMF WEO "Advanced economies" (ISO 3166-1 alpha-2).
ADVANCED_ECONOMIES = _iso(
    "AD AT AU BE CA CH CY CZ DE DK EE ES FI FR GB GR HK HR IE IL IS IT JP KR LT LU LV MO "
    "MT NL NO NZ PR PT SE SG SI SK SM TW US"
)
#: Sub-Saharan Africa (IMF WEO group).
SUB_SAHARAN_AFRICA = _iso(
    "AO BF BI BJ BW CD CF CG CI CM CV DJ ER ET GA GH GM GN GQ GW KE KM LR LS MG ML MR MU "
    "MW MZ NA NE NG RW SC SD SL SN SO SS ST SZ TD TG TZ UG ZA ZM ZW"
)
NORTH_AFRICA = _iso("DZ EG EH LY MA TN")
#: Former Soviet Union (the Baltic states are advanced economies first).
FSU = _iso("AM AZ BY GE KG KZ MD RU TJ TM UA UZ EE LV LT")
MIDDLE_EAST = _iso("AE BH IQ IR JO KW LB OM PS QA SA SY YE")
OTHER_EUROPE = _iso("AL BA BG FO GG GI GL HU IM JE LI MC ME MK PL RO RS TR VA XK")
ASIA = _iso(
    "AF BD BN BT CN FJ FM ID IN KH KI KP LA LK MH MM MN MP MV MY NP NR PG PH PK PW SB TH "
    "TL TO TV VN VU WS"
)
WESTERN_HEMISPHERE = _iso(
    "AG AI AR AW BB BL BM BO BQ BR BS BZ CL CO CR CU CW DM DO EC GD GT GY HN HT JM KN KY "
    "LC MF MQ MS MX NI PA PE PY SR SV SX TC TT UY VC VE VG VI"
)

#: Precedence: advanced economies first (Baltics before FSU), then regions;
#: "of which" buckets are ADDITIONAL to their parent.
_REGION_TABLE: tuple[tuple[frozenset[str], tuple[str, ...]], ...] = (
    (ADVANCED_ECONOMIES, ("advanced_economies",)),
    (SUB_SAHARAN_AFRICA, ("africa", "africa.sub_saharan")),
    (NORTH_AFRICA, ("africa",)),
    (FSU, ("europe", "europe.fsu")),
    (OTHER_EUROPE, ("europe",)),
    (MIDDLE_EAST, ("middle_east",)),
    (ASIA, ("asia",)),
    (WESTERN_HEMISPHERE, ("western_hemisphere",)),
)


def annex_4b_regions(loan: Loan, home_country: str) -> tuple[str, ...]:
    """The 4b buckets a loan lands in ("of which" rows are ADDITIONAL to their
    parent). Empty when the region cannot be determined (no counterparty, or a
    non-resident without a recognised country code)."""
    cpty = loan.counterparty
    if cpty is None:
        return ()
    country = (cpty.country_code or "").strip().upper()
    if country == home_country or (loan.resident and not country):
        return ("domestic",)
    for codes, buckets in _REGION_TABLE:
        if country in codes:
            return buckets
    return ()


_TOP_LEVEL_4B: tuple[str, ...] = (
    "domestic",
    "advanced_economies",
    "africa",
    "asia",
    "europe",
    "middle_east",
    "western_hemisphere",
)


@resolver("bsd4.annex4b")
def _bsd4_annex4b(rc: ResolveContext, params: dict[str, Any]) -> Decimal | None:
    """Annex 4b row: params ``bucket`` ∈ :data:`ANNEX_4B_ROWS` values; column
    key ends in ``amount`` or ``share``. Shares are against total loans (Σ of
    the top-level rows), NOT the template's C18 which also adds the two
    "of which" rows."""
    bucket = str(params["bucket"])
    if bucket not in ANNEX_4B_ROWS.values():
        msg = f"bsd4.annex4b: unknown bucket {bucket!r}"
        raise ValueError(msg)
    key = "bsd4:annex4b"
    if key not in rc.cache:
        home = (rc.bank.jurisdiction_code or "").strip().upper()
        totals: dict[str, Decimal] = {}
        for loan in load_loans(rc):
            for region in annex_4b_regions(loan, home):
                totals[region] = totals.get(region, Decimal(0)) + loan.amount_ghs
        grand = sum((totals.get(r, Decimal(0)) for r in _TOP_LEVEL_4B), Decimal(0))
        rc.cache[key] = (totals, grand)
    totals, grand = rc.cache[key]
    return _annex_value(rc, params, totals, grand)
