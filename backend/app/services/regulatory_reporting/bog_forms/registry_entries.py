"""Registry + template entries for every official BoG return.

Imported by ``registry.py`` / ``templates.py`` to add one :class:`ReturnDefinition`
and one :class:`ReturnTemplate` per form in the catalogue. Kept in the
``bog_forms`` package so the two lists can never drift from the catalogue.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from .catalog import WEEKLY_ANCHOR_WEEKDAY, all_form_specs
from .spec import FormSpec

BOG_FAMILY = "bsd"
BOG_TEMPLATE_PREFIX = "bog-"
BOG_TEMPLATE_SUFFIX = "-official-v1"


def template_id_for(code: str) -> str:
    return f"{BOG_TEMPLATE_PREFIX}{code.lower()}{BOG_TEMPLATE_SUFFIX}"


def is_bog_official_template(template_id: str) -> bool:
    return template_id.startswith(BOG_TEMPLATE_PREFIX) and template_id.endswith(BOG_TEMPLATE_SUFFIX)


def days_after(days: int) -> Callable[[date], date]:
    """Guide time limit: due ``days`` calendar days after the reporting date."""

    def rule(reporting_date: date) -> date:
        return reporting_date + timedelta(days=days)

    return rule


def weekly_reporting_dates(
    as_of: date, *, weeks_back: int = 8, weeks_forward: int = 4
) -> list[date]:
    """Friday-close reporting dates around ``as_of`` (documented convention:
    the Guide fixes the weekly cadence, not the weekday)."""
    delta = (as_of.weekday() - WEEKLY_ANCHOR_WEEKDAY) % 7
    last_anchor = as_of - timedelta(days=delta)
    return [
        last_anchor + timedelta(weeks=offset) for offset in range(-weeks_back, weeks_forward + 1)
    ]


#: Where an official BSD form computes a metric the platform's engines ALSO
#: compute, the form's own methodology is named here so the filed package states
#: which figure it reports (forensic audit §8 / WS-A metric authority registry).
#:
#: BSD5A is the worked case and the ONLY one established today: BoG's template
#: ratio ``E70 = E25/E69`` uses 50% of the net open position and 100% of the
#: three-year average gross income, where the capital engine applies an FX
#: charge × RWA multiplier and a BIA charge × RWA multiplier. The two are
#: DIFFERENT BY CONSTRUCTION and their inequality is pinned by
#: ``tests/services/bog_forms/test_bsd5.py``. Nothing else is declared, because
#: nothing else is established — a guessed methodology id would be worse than
#: an absent one.
_DECLARED_METHODOLOGIES: dict[str, tuple[tuple[str, str], ...]] = {
    "BSD5A": (("car_pct", "bog_bsd5a_form_ratio"),),
}


def build_definitions(return_definition_cls: type[Any]) -> list[Any]:
    """One ReturnDefinition per catalogue form (constructed with the caller's
    class to avoid an import cycle with registry.py)."""
    definitions: list[Any] = []
    for spec in all_form_specs():
        definitions.append(
            return_definition_cls(
                code=spec.code,
                family=BOG_FAMILY,
                title=f"{spec.code} — {spec.title}",
                directive_citation=(
                    f"Bank of Ghana official return template '{spec.workbook}' with the "
                    "Guide for Reporting Institutions (List of Prudential Returns: "
                    f"{spec.frequency}, {spec.time_limit_days} days; basis {spec.basis})."
                ),
                frequency=spec.frequency,
                deadline_rule=days_after(spec.time_limit_days),
                generator="bog_form",
                template_id=template_id_for(spec.code),
                fidelity="CONFIRMED",
                default_channel="email",
                # Declared, not inherited (forensic audit ARCH-8). The BSD set is
                # the Banking Supervision Department's prudential return pack for
                # licensed BANKS in Ghana; the SDI return family is a separate
                # pack BoG has not published (docs/sdi.md §2.3 — obtain it, never
                # infer it). Stating both here means the eligibility authority is
                # reading a reviewed declaration, not a dataclass default.
                institution_classes=("bank",),
                jurisdictions=("GH",),
                required_data=("bank_financial_facts", "canonical_positions"),
                declared_methodologies=_DECLARED_METHODOLOGIES.get(spec.code, ()),
            )
        )
    return definitions


def build_templates(
    return_template_cls: type[Any],
    section_layout_cls: type[Any],
    column_spec_cls: type[Any],
) -> dict[str, Any]:
    """One ReturnTemplate per form: a section per official sheet whose rows are
    the declared input lines (generic csv/pdf/validation rendering); the xlsx
    exporter uses the layout-driven renderer instead."""
    templates: dict[str, Any] = {}
    for spec in all_form_specs():
        templates[template_id_for(spec.code)] = return_template_cls(
            template_id=template_id_for(spec.code),
            return_code=spec.code,
            title=f"{spec.code} — {spec.title}",
            fidelity="CONFIRMED",
            source_citation=(
                f"Official BoG workbook '{spec.workbook}' + Guide for Reporting Institutions"
            ),
            sections=tuple(
                section_layout_cls(
                    section_code=_section_code(sheet.name),
                    layout_id=f"{spec.code.lower()}-{_section_code(sheet.name)}",
                    sheet_title=sheet.name,
                    columns=(
                        column_spec_cls(key="code", header="Line", kind="text"),
                        column_spec_cls(key="description", header="Description", kind="text"),
                        column_spec_cls(key="column", header="Column", kind="text"),
                        column_spec_cls(key="cell", header="Official cell", kind="text"),
                        column_spec_cls(key="value", header=f"Value ({sheet.unit})", kind="amount"),
                        column_spec_cls(key="status", header="Status", kind="text"),
                        column_spec_cls(key="source", header="Source", kind="text"),
                    ),
                    fidelity="CONFIRMED",
                    source_citation=f"{spec.workbook} · sheet '{sheet.name}'",
                    optional=True,
                )
                for sheet in spec.sheets
            ),
            currency_unit=(
                "¢'Million unless the sheet states otherwise (BoG unit conventions preserved)"
            ),
            basis="consolidated" if spec.basis == "consolidated" else "solo",
            notes=spec.scope_notes,
        )
    return templates


def _section_code(sheet_name: str) -> str:
    return "sheet_" + "".join(ch.lower() if ch.isalnum() else "_" for ch in sheet_name).strip("_")


__all__ = [
    "BOG_FAMILY",
    "FormSpec",
    "build_definitions",
    "build_templates",
    "days_after",
    "is_bog_official_template",
    "template_id_for",
    "weekly_reporting_dates",
]
