"""LRT corporate return-pack generators (docs/submission_pipeline_plan.md §W5).

Five event-driven "corporate" family packs, each pre-filled EXCLUSIVELY from
the W4 institution-profile register (``app/services/institution_profile.py``)
— no engine recomputation, no external calls, ``source_runs = []`` because
these are master-data packs, not run-derived returns.

Structures follow the ORASS LRT Portal User Guide v1.0 (Sept 2020, draft):
form-set structure documented; field-level layouts transcribed from the
guide's screenshots. The guide is a DRAFT — templates carry that caveat.

Snapshots use the shared ``regulatory-package-v1`` shape via the public seams
in ``generation.py`` (``build_envelope`` / ``snapshot_row`` / …), so packs
ride the same validate → approve → export → submit lifecycle as every other
package. Values are text-first (dates ISO-formatted, decimals stringified);
the only declared cross-foot total is the shareholder-register ``pct_total``
in the Capital Injection pack.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, BankReportingPeriod
from app.schemas.institution_profile import (
    InstitutionProfileFullRead,
    InstitutionProfileRead,
    RelatedPartyRead,
)
from app.services import institution_profile
from app.services.regulatory_reporting.generation import (
    GeneratedReturn,
    build_envelope,
    snapshot_row,
    snapshot_section,
    snapshot_total,
)
from app.services.regulatory_reporting.registry import ReturnDefinition

LRT_GUIDE_CITATION = (
    "ORASS LRT Portal User Guide v1.0 (Sept 2020, draft) — form-set structure "
    "documented; field-level layouts transcribed from the guide's screenshots"
)

_DIRECTOR_ROLES = ("director", "board_chairman", "board_secretary")
_KMP_ROLES = (
    "chief_executive_officer",
    "chief_finance_officer",
    "chief_risk_officer",
    "chief_compliance_officer",
    "head_internal_audit",
    "other_key_management",
)
_OUTSOURCED_ROLES = ("outsourced_company", "outsourced_individual")


def _text(value: Any) -> str:
    """Snapshot-safe text: '' for absent values, ISO for dates, str otherwise."""
    if value is None:
        return ""
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _optional_number(value: Decimal | None) -> str | None:
    """Numeric-or-blank extra cell: None renders blank; never the text 'None'."""
    return None if value is None else str(value)


def _missing_master_data_409(error_code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"error_code": error_code, "message": message},
    )


def _register(db: Session, ctx: TenantContext, bank: Bank) -> InstitutionProfileFullRead:
    return institution_profile.get_full_profile(db, ctx, bank.id)


def _profile_or_409(register: InstitutionProfileFullRead, artifact: str) -> InstitutionProfileRead:
    profile = register.profile
    if profile is None:
        raise _missing_master_data_409(
            "no_institution_profile",
            f"No institution profile is recorded for this bank; {artifact} pre-fills "
            "from the corporate profile register. Capture the profile under "
            "Governance → Institution Profile first.",
        )
    return profile


def _metadata(form_set: str, orass_forms: tuple[str, ...]) -> dict[str, Any]:
    return {
        "master_data_source": "institution_profile register (plan W4)",
        "form_set": form_set,
        "orass_forms": list(orass_forms),
        "source_citation": LRT_GUIDE_CITATION,
        "event_driven": True,
    }


def _checklist_row(code: str, description: str) -> dict[str, Any]:
    return snapshot_row(code, description, "required")


def _address_text(address: dict[str, Any]) -> str:
    return ", ".join(str(value) for _, value in sorted(address.items()) if value)


def _party_roles(party: RelatedPartyRead, roles: tuple[str, ...]) -> list[str]:
    return [role.role for role in party.roles if role.role in roles]


# ---------------------------------------------------------------------------
# LRT-PROFILE — Corporate Profile Update pack
# ---------------------------------------------------------------------------


def generate_lrt_profile(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
) -> GeneratedReturn:
    register = _register(db, ctx, bank)
    profile = _profile_or_409(register, "the Corporate Profile Update pack")
    general_rows = [
        snapshot_row("institution_type", "Institution type", profile.institution_type),
        snapshot_row(
            "legal_entity_structure", "Legal entity structure", profile.legal_entity_structure
        ),
        snapshot_row("authorisation_date", "Authorisation date", _text(profile.authorisation_date)),
        snapshot_row("approved_capital", "Approved capital (GHS)", _text(profile.approved_capital)),
        snapshot_row("tin", "Tax identification number (TIN)", _text(profile.tin)),
        snapshot_row(
            "registration_number", "Registration number", _text(profile.registration_number)
        ),
        snapshot_row(
            "orass_institution_code",
            "ORASS institution code",
            _text(profile.orass_institution_code),
        ),
        snapshot_row("incorporation_date", "Incorporation date", _text(profile.incorporation_date)),
    ]
    business_rows = [
        snapshot_row(
            "primary_business", "Primary business (institution type)", profile.institution_type
        ),
        snapshot_row("license_type", "Licence type", _text(bank.license_type)),
    ]
    exchange_rows = [
        snapshot_row(
            "traded_on_exchange",
            "Traded on a stock exchange",
            "Yes" if profile.traded_on_exchange else "No",
        ),
        snapshot_row("exchange_name", "Exchange name", _text(profile.exchange_name)),
        snapshot_row("isin", "ISIN", _text(profile.isin)),
    ]
    ownership_rows = [
        snapshot_row(
            "ownership_local_pct", "Local ownership (%)", _text(profile.ownership_local_pct)
        ),
        snapshot_row(
            "ownership_foreign_pct", "Foreign ownership (%)", _text(profile.ownership_foreign_pct)
        ),
        snapshot_row(
            "parent_country_code", "Parent company country", _text(profile.parent_country_code)
        ),
    ]
    license_rows = [
        snapshot_row(
            str(index),
            row.license_name,
            row.status,
            license_class=_text(row.license_class),
            issued_on=_text(row.issued_on),
        )
        for index, row in enumerate(register.licenses, start=1)
    ]
    name_rows = [
        snapshot_row(
            str(index),
            entry.previous_name,
            _text(entry.changed_on),
            change_reason=_text(entry.change_reason),
        )
        for index, entry in enumerate(register.name_history, start=1)
    ]
    sections = [
        snapshot_section("general_details", "General Details", general_rows),
        snapshot_section("business_activities", "Business Activities", business_rows),
        snapshot_section("exchange_membership", "Stock Exchange Membership", exchange_rows),
        snapshot_section("ownership", "Ownership", ownership_rows),
        snapshot_section("licenses", "Licenses", license_rows, optional=True),
        snapshot_section("name_history", "Name History", name_rows, optional=True),
    ]
    totals = [
        snapshot_row(
            "approved_capital_ghs",
            "Approved capital",
            profile.approved_capital if profile.approved_capital is not None else Decimal("0"),
            unit="ghs",
        ),
        snapshot_row(
            "active_license_count",
            "Active licenses",
            sum(1 for row in register.licenses if row.status == "active"),
        ),
    ]
    metadata = _metadata(
        "Reporting Institution Profile (General Details / Business Activities / "
        "Stock Exchange / Ownership)",
        ("General Details", "RD"),
    )
    return GeneratedReturn(
        snapshot=build_envelope(bank, period, definition, sections, totals, metadata),
        source_runs=[],
    )


# ---------------------------------------------------------------------------
# LRT-OUTLET — Outlet Opening / Relocation / Closure pack
# ---------------------------------------------------------------------------


def generate_lrt_outlet(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
) -> GeneratedReturn:
    register = _register(db, ctx, bank)
    open_rows = [
        snapshot_row(
            outlet.outlet_number or str(index),
            outlet.name,
            outlet.outlet_type,
            opened_on=_text(outlet.opened_on),
            address=_address_text(outlet.address),
            relocated_from=_text(outlet.relocated_from),
        )
        for index, outlet in enumerate(
            (row for row in register.outlets if row.status == "active"), start=1
        )
    ]
    closed_rows = [
        snapshot_row(
            outlet.outlet_number or str(index),
            outlet.name,
            outlet.outlet_type,
            opened_on=_text(outlet.opened_on),
            closed_on=_text(outlet.closed_on),
            address=_address_text(outlet.address),
            relocated_from=_text(outlet.relocated_from),
        )
        for index, outlet in enumerate(
            (row for row in register.outlets if row.status == "closed"), start=1
        )
    ]
    # Static required-documents checklist per the guide's OO/CRO form sets.
    checklist_rows = [
        _checklist_row("RES", "Board Resolution authorising the outlet action (RES)"),
        _checklist_row("RD", "Required Documents upload list (RD)"),
        _checklist_row("SOP", "Submission of Payments — processing-fee evidence (SOP)"),
    ]
    sections = [
        snapshot_section("outlets_open", "Opening of Outlets (OO)", open_rows, optional=True),
        snapshot_section(
            "outlets_closed", "Closure and Relocation of Outlets (CRO)", closed_rows, optional=True
        ),
        snapshot_section("required_documents", "Required Documents (RD)", checklist_rows),
    ]
    totals = [
        snapshot_row("outlets_open_count", "Active outlets", len(open_rows)),
        snapshot_row("outlets_closed_count", "Closed outlets", len(closed_rows)),
    ]
    metadata = _metadata(
        "Contact Information (ACI add / UCI update) with OO Opening of Outlets, "
        "CRO Closure and Relocation, RD Required Documents",
        ("ACI", "UCI", "OO", "CRO", "RD", "RES", "SOP"),
    )
    return GeneratedReturn(
        snapshot=build_envelope(bank, period, definition, sections, totals, metadata),
        source_runs=[],
    )


# ---------------------------------------------------------------------------
# LRT-PARTY — Related Party / Service Provider pack
# ---------------------------------------------------------------------------


def _shareholder_rows(
    parties: list[RelatedPartyRead], names: dict[Any, str]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for party in parties:
        if not _party_roles(party, ("shareholder",)):
            continue
        if not party.shareholdings:
            rows.append(
                snapshot_row(
                    str(len(rows) + 1),
                    party.full_name,
                    "",
                    share_type="",
                    share_subtype="",
                    rights="",
                    number_of_shares=None,
                    ubo_name="",
                )
            )
            continue
        for holding in party.shareholdings:
            rows.append(
                snapshot_row(
                    str(len(rows) + 1),
                    party.full_name,
                    _text(holding.pct_shareholding),
                    share_type=holding.share_type,
                    share_subtype=_text(holding.share_subtype),
                    rights=holding.shareholder_rights,
                    number_of_shares=str(holding.number_of_shares),
                    ubo_name=(
                        names.get(holding.ubo_party_id, "")
                        if holding.ubo_party_id is not None
                        else ""
                    ),
                )
            )
    return rows


def _due_diligence_rows(parties: list[RelatedPartyRead]) -> list[dict[str, Any]]:
    """CDD for every party; EDD + PNF for individuals only (guide form set)."""
    rows: list[dict[str, Any]] = []
    for index, party in enumerate(parties, start=1):
        rows.append(
            _checklist_row(f"CDD-{index}", f"Comprehensive due diligence (CDD) — {party.full_name}")
        )
        if party.party_type == "individual":
            rows.append(
                _checklist_row(
                    f"EDD-{index}",
                    f"Enhanced due diligence (EDD, individuals) — {party.full_name}",
                )
            )
            rows.append(
                _checklist_row(
                    f"PNF-{index}",
                    f"Personality note form (PNF, individuals) — {party.full_name}",
                )
            )
    return rows


def generate_lrt_party(  # noqa: PLR0914
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
) -> GeneratedReturn:
    register = _register(db, ctx, bank)
    parties = register.related_parties
    if not parties:
        raise _missing_master_data_409(
            "no_related_parties",
            "No related parties are recorded for this bank; the Related Party / "
            "Service Provider pack pre-fills from the related-party register. "
            "Capture the parties under Governance → Institution Profile first.",
        )
    names = {party.id: party.full_name for party in parties}

    director_rows: list[dict[str, Any]] = []
    kmp_rows: list[dict[str, Any]] = []
    auditor_rows: list[dict[str, Any]] = []
    ubo_rows: list[dict[str, Any]] = []
    outsourced_rows: list[dict[str, Any]] = []
    liquidator_rows: list[dict[str, Any]] = []
    for party in parties:
        for role in party.roles:
            if role.role in _DIRECTOR_ROLES:
                director_rows.append(
                    snapshot_row(
                        str(len(director_rows) + 1),
                        party.full_name,
                        role.role,
                        appointed_on=_text(role.appointed_on),
                        term_of_appointment=_text(role.term_of_appointment),
                        sitting_allowance=_optional_number(role.sitting_allowance),
                        travel_allowance=_optional_number(role.travel_allowance),
                        annual_fees=_optional_number(role.annual_fees),
                    )
                )
            elif role.role in _KMP_ROLES:
                kmp_rows.append(
                    snapshot_row(
                        str(len(kmp_rows) + 1),
                        party.full_name,
                        role.role,
                        appointed_on=_text(role.appointed_on),
                        other_responsibilities=_text(role.other_responsibilities),
                    )
                )
            elif role.role == "external_auditor":
                auditor_rows.append(
                    snapshot_row(
                        str(len(auditor_rows) + 1),
                        party.full_name,
                        _text(role.icag_registration),
                        appointed_on=_text(role.appointed_on),
                        regulated_jurisdiction=_text(party.regulated_jurisdiction),
                    )
                )
            elif role.role == "ultimate_beneficial_owner":
                ubo_rows.append(
                    snapshot_row(
                        str(len(ubo_rows) + 1),
                        party.full_name,
                        party.status,
                        contact_email=_text(party.contact.get("email")),
                    )
                )
            elif role.role in _OUTSOURCED_ROLES:
                outsourced_rows.append(
                    snapshot_row(
                        str(len(outsourced_rows) + 1),
                        party.full_name,
                        role.role,
                        other_responsibilities=_text(role.other_responsibilities),
                    )
                )
            elif role.role == "liquidator":
                liquidator_rows.append(
                    snapshot_row(
                        str(len(liquidator_rows) + 1),
                        party.full_name,
                        _text(role.appointed_on),
                    )
                )

    # One section per party category PRESENT in the register — empty
    # categories are omitted rather than emitted as empty tables.
    category_sections = [
        ("shareholders", "Shareholder Information (ASI)", _shareholder_rows(parties, names)),
        ("directors", "Director Information (ADI)", director_rows),
        ("key_management", "Key Management Personnel (ARD)", kmp_rows),
        ("auditors", "Auditor Information (AAI)", auditor_rows),
        ("ubos", "Ultimate Beneficial Owners", ubo_rows),
        ("outsourced", "Outsourced Service Providers", outsourced_rows),
        ("liquidators", "Liquidators", liquidator_rows),
    ]
    sections = [
        snapshot_section(code, title, rows, optional=True)
        for code, title, rows in category_sections
        if rows
    ]
    sections.append(
        snapshot_section(
            "due_diligence_checklist",
            "Due Diligence Checklist (CDD / EDD / PNF)",
            _due_diligence_rows(parties),
            optional=True,
        )
    )
    totals = [
        snapshot_row("related_party_count", "Related parties on the register", len(parties)),
        snapshot_row(
            "individual_party_count",
            "Individual (natural-person) parties",
            sum(1 for party in parties if party.party_type == "individual"),
        ),
    ]
    metadata = _metadata(
        "Related Parties / Service Providers (ARP add) with ARD role details, "
        "ARCI contact information, CDD/EDD/PNF due diligence, ASI/ADI/AAI "
        "information forms, RD required documents",
        ("ARP", "ARD", "ARCI", "CDD", "EDD", "PNF", "ASI", "ADI", "AAI", "RD"),
    )
    return GeneratedReturn(
        snapshot=build_envelope(bank, period, definition, sections, totals, metadata),
        source_runs=[],
    )


# ---------------------------------------------------------------------------
# LRT-CAPITAL — Capital Injection pack
# ---------------------------------------------------------------------------


def generate_lrt_capital(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
) -> GeneratedReturn:
    register = _register(db, ctx, bank)
    profile = _profile_or_409(register, "the Capital Injection pack")
    names = {party.id: party.full_name for party in register.related_parties}

    register_rows: list[dict[str, Any]] = []
    total_pct = Decimal("0")
    holding_parties: set[Any] = set()
    for party in register.related_parties:
        for holding in party.shareholdings:
            total_pct += holding.pct_shareholding
            holding_parties.add(party.id)
            register_rows.append(
                snapshot_row(
                    str(len(register_rows) + 1),
                    party.full_name,
                    _text(holding.pct_shareholding),
                    share_type=holding.share_type,
                    share_subtype=_text(holding.share_subtype),
                    rights=holding.shareholder_rights,
                    number_of_shares=str(holding.number_of_shares),
                    ubo_name=(
                        names.get(holding.ubo_party_id, "")
                        if holding.ubo_party_id is not None
                        else ""
                    ),
                )
            )

    summary_rows: list[dict[str, Any]] = []
    if profile.approved_capital is not None:
        summary_rows.append(
            snapshot_row(
                "approved_capital_ghs", "Approved capital", profile.approved_capital, unit="ghs"
            )
        )
    summary_rows.append(
        snapshot_row(
            "total_pct_shareholding",
            "Total shareholding across the register (%)",
            total_pct,
            unit="pct",
        )
    )
    checklist_rows = [
        _checklist_row("URP", "Update Related Party form (URP)"),
        _checklist_row("USI", "Update Shareholder Information form (USI)"),
        _checklist_row("RES", "Board Resolution (RES)"),
        _checklist_row("SOP", "Submission of Payments — processing-fee evidence (SOP)"),
    ]
    sections = [
        snapshot_section(
            "shareholder_register",
            "Shareholder Register (USI)",
            register_rows,
            snapshot_total(
                "pct_total",
                "Total shareholding (%)",
                total_pct,
                equals_sum_of_rows=True,
            ),
            optional=True,
        ),
        snapshot_section("capital_summary", "Capital Summary", summary_rows),
        snapshot_section("capital_checklist", "Required Companion Forms", checklist_rows),
    ]
    totals = [
        snapshot_row("total_pct_shareholding", "Total shareholding (%)", total_pct, unit="pct"),
        snapshot_row("shareholder_count", "Parties holding shares", len(holding_parties)),
    ]
    metadata = _metadata(
        "Capital Injection (URP update related party / USI update shareholder "
        "information / RES Resolution / SOP Submission of Payments)",
        ("URP", "USI", "RES", "SOP"),
    )
    return GeneratedReturn(
        snapshot=build_envelope(bank, period, definition, sections, totals, metadata),
        source_runs=[],
    )


# ---------------------------------------------------------------------------
# LRT-PRODUCT — Product / Service Approval pack
# ---------------------------------------------------------------------------


def generate_lrt_product(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
) -> GeneratedReturn:
    register = _register(db, ctx, bank)
    if not register.products:
        raise _missing_master_data_409(
            "no_products",
            "No products or services are recorded for this bank; the Product / "
            "Service Approval pack pre-fills from the product register. Capture "
            "the products under Governance → Institution Profile first.",
        )
    ordered = sorted(register.products, key=lambda product: (product.status, product.name))
    product_rows = [
        snapshot_row(
            str(index),
            product.name,
            product.status,
            product_type=product.product_type,
            approval_reference=_text(product.approval_reference),
        )
        for index, product in enumerate(ordered, start=1)
    ]
    checklist_rows = [
        _checklist_row("DN", "Declaration (DN)"),
        _checklist_row("MOU", "Memorandum of Understanding (MOU)"),
        _checklist_row("RES", "Board Resolution (RES)"),
        _checklist_row("SOP", "Submission of Payments — processing-fee evidence (SOP)"),
    ]
    sections = [
        snapshot_section("products", "Products / Services by Status (AP)", product_rows),
        snapshot_section(
            "product_checklist", "Required Companion Forms (DN / MOU / RES / SOP)", checklist_rows
        ),
    ]
    totals = [
        snapshot_row("product_count", "Products on the register", len(product_rows)),
        snapshot_row(
            "approved_product_count",
            "Approved products",
            sum(1 for product in register.products if product.status == "approved"),
        ),
    ]
    metadata = _metadata(
        "Products/Services (AP add) with DN Declaration, MOU, RES Resolution, "
        "SOP Submission of Payments",
        ("AP", "DN", "MOU", "RES", "SOP"),
    )
    return GeneratedReturn(
        snapshot=build_envelope(bank, period, definition, sections, totals, metadata),
        source_runs=[],
    )


LRT_GENERATORS = {
    "lrt_profile": generate_lrt_profile,
    "lrt_outlet": generate_lrt_outlet,
    "lrt_party": generate_lrt_party,
    "lrt_capital": generate_lrt_capital,
    "lrt_product": generate_lrt_product,
}

__all__ = [
    "LRT_GENERATORS",
    "LRT_GUIDE_CITATION",
    "generate_lrt_capital",
    "generate_lrt_outlet",
    "generate_lrt_party",
    "generate_lrt_product",
    "generate_lrt_profile",
]
