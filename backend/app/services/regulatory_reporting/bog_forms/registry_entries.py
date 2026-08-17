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
