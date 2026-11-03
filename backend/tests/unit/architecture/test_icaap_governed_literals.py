"""ICAAP template wording exposes governed values through report notes."""

from __future__ import annotations

import dataclasses
import re
from typing import cast

import pytest

from app.reporting.service.registry import get_definition
from app.reporting.service.templates import get_template

ICAAP_RETURNS = ("ICAAP-STRESS-APPENDIX2", "SDI-STRESS-ANNUAL", "ICAAP-STRESS", "CAR-RWA")
ALLOWED_TEMPLATE_PERCENTAGES = ("(0.05 = 5%)", "Tier 1 %")
PERCENT_LITERAL = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s?%")


def _strings(obj: object, out: list[str]) -> None:
    if isinstance(obj, str):
        out.append(obj)
    elif dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        for item in dataclasses.fields(obj):
            _strings(cast(object, getattr(obj, item.name)), out)
    elif isinstance(obj, (list, tuple)):
        for item in cast(list[object] | tuple[object, ...], obj):
            _strings(item, out)


@pytest.mark.parametrize("return_code", ICAAP_RETURNS)
def test_icaap_template_text_prints_no_percentage_literal(return_code: str) -> None:
    definition = get_definition(return_code)
    assert definition is not None
    template = get_template(definition.template_id)
    assert template is not None
    offending = _template_percentages(template)
    assert not offending, (
        f"{return_code} template text prints {offending}; state the governed value "
        "through the report notes instead of writing it into the template (D-024)"
    )


def _template_percentages(template: object) -> list[str]:
    texts: list[str] = []
    _strings(template, texts)
    found: list[str] = []
    for text in texts:
        cleaned = text
        for allowed in ALLOWED_TEMPLATE_PERCENTAGES:
            cleaned = cleaned.replace(allowed, "")
        found += [match.group(0) for match in PERCENT_LITERAL.finditer(cleaned)]
    return found


def test_the_template_scan_sees_a_literal_where_one_exists() -> None:
    """Not vacuous: the frozen pre-P0 text (D-021) still carries the literals the
    current templates dropped, and the same scan finds them there."""
    from app.reporting.service.templates_legacy import (  # noqa: PLC0415
        LEGACY_TEMPLATES,
    )

    assert "13%" in _template_percentages(LEGACY_TEMPLATES["bog-icaap-stress-appendix2-v1"])
    assert "1.5%" in _template_percentages(LEGACY_TEMPLATES["bog-bsd2-capital-v1"])
