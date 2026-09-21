"""Every way a framework document can be wrong is refused, with a JSON path.

A framework that loads with a requirement quietly dropped is worse than one
that fails to load: the bank would file against a checklist missing a line the
regulator asked for. So every rule below turns a content mistake into a loud
failure naming where it is.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from app.domain.icaap.frameworks.registry import framework_digest
from app.domain.icaap.frameworks.schema import FrameworkSchemaError, parse_framework
from tests.fixtures.icaap.synthetic_frameworks import version_one_raw


def _parse(raw: dict[str, Any]) -> None:
    parse_framework(raw, digest=framework_digest(raw))


def test_the_reference_document_parses() -> None:
    _parse(version_one_raw())


def _break(mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    raw = version_one_raw()
    mutate(raw)
    return raw


def _unknown_top_level(raw: dict[str, Any]) -> None:
    raw["sektions"] = []


def _bad_schema_id(raw: dict[str, Any]) -> None:
    raw["schema"] = "aequoros-icaap-framework-v2"


def _bad_status(raw: dict[str, Any]) -> None:
    raw["status"] = "draftish"


def _bad_date(raw: dict[str, Any]) -> None:
    raw["effective_from"] = "2026-13-45"


def _letter_gap(raw: dict[str, Any]) -> None:
    raw["sections"][1]["letter"] = "z"


def _order_gap(raw: dict[str, Any]) -> None:
    raw["sections"][1]["order"] = 7


def _duplicate_section_key(raw: dict[str, Any]) -> None:
    raw["sections"][1]["key"] = raw["sections"][0]["key"]


def _bad_section_key(raw: dict[str, Any]) -> None:
    raw["sections"][0]["key"] = "Overview!"


def _duplicate_item_id(raw: dict[str, Any]) -> None:
    raw["sections"][1]["requirements"][0]["id"] = raw["sections"][0]["requirements"][0]["id"]


def _item_without_citation(raw: dict[str, Any]) -> None:
    raw["sections"][0]["requirements"][0]["citations"] = []


def _item_too_long(raw: dict[str, Any]) -> None:
    raw["sections"][0]["requirements"][0]["text"] = "x" * 281


def _item_quotes_the_source(raw: dict[str, Any]) -> None:
    raw["sections"][0]["requirements"][0]["text"] = 'The institution "shall" assess.'


def _unknown_citation_document(raw: dict[str, Any]) -> None:
    raw["sections"][0]["citation"]["doc"] = "NOT-A-DOC"


def _unknown_evidence_token(raw: dict[str, Any]) -> None:
    raw["sections"][0]["requirements"][0]["evidence"] = ["telepathy"]


def _unknown_evidence_block(raw: dict[str, Any]) -> None:
    raw["sections"][0]["requirements"][0]["evidence"] = ["block:nonexistent_block"]


def _unknown_evidence_attachment(raw: dict[str, Any]) -> None:
    raw["sections"][0]["requirements"][0]["evidence"] = ["attachment:not_requested"]


def _unknown_condition(raw: dict[str, Any]) -> None:
    raw["sections"][0]["requirements"][0]["applies_when"] = "is_listed"


def _unavailable_data_block(raw: dict[str, Any]) -> None:
    raw["sections"][0]["data_blocks"] = ["risk_register"]


def _category_number_gap(raw: dict[str, Any]) -> None:
    raw["risk_categories"][0]["number"] = 2


def _unknown_method(raw: dict[str, Any]) -> None:
    raw["risk_categories"][0]["components"][0]["allowed_methods"] = ["vibes"]


def _table5_row_without_declaration(raw: dict[str, Any]) -> None:
    raw["risk_categories"][0]["components"][0]["table5_row"] = "credit_concentration"


def _unmapped_table5_row(raw: dict[str, Any]) -> None:
    raw["table5_rows"] = [{"key": "irrbb", "label": "IRRBB", "order": 1}]
    raw["table5_citation"] = {"doc": "ZZ-DOC", "ref": "T5"}


def _null_row_with_a_capitalising_method(raw: dict[str, Any]) -> None:
    raw["table5_rows"] = [{"key": "irrbb", "label": "IRRBB", "order": 1}]
    raw["table5_citation"] = {"doc": "ZZ-DOC", "ref": "T5"}
    raw["risk_categories"][0]["components"].append(
        {
            "key": "irrbb",
            "table5_row": "irrbb",
            "p29_class": "outside_pillar1",
            "p29_basis": "sourced",
            "allowed_methods": ["judgemental"],
        }
    )
    raw["risk_categories"][0]["components"][0]["allowed_methods"] = ["judgemental"]


def _relaxable_regulator_document(raw: dict[str, Any]) -> None:
    raw["attachments"] = [
        {
            "kind": "board_resolution",
            "title": "Board resolution",
            "citations": [{"doc": "ZZ-DOC", "ref": "5"}],
            "gate": "submission",
            "min_count": 1,
            "max_count": None,
            "relaxable_by_signing_policy": True,
            "media_types": ["application/pdf"],
            "applies_when": None,
            "section_keys": [],
        }
    ]


def _duplicate_attachment_kind(raw: dict[str, Any]) -> None:
    entry = {
        "kind": "board_resolution",
        "title": "Board resolution",
        "citations": [],
        "gate": "optional",
        "min_count": 0,
        "max_count": None,
        "relaxable_by_signing_policy": True,
        "media_types": ["application/pdf"],
        "applies_when": None,
        "section_keys": [],
    }
    raw["attachments"] = [entry, dict(entry)]


def _two_freezing_stages(raw: dict[str, Any]) -> None:
    raw["stages"][0]["freeze_on_approve"] = True


def _sourced_section_that_says_pending(raw: dict[str, Any]) -> None:
    raw["sections"][0]["guidance"] = "Heading only; the paragraph is pending the regulator's text."


def _pending_section_that_does_not_say_so(raw: dict[str, Any]) -> None:
    raw["sections"][0]["source_status"] = "pending_primary_text"


def _placeholder_without_a_declared_parameter(raw: dict[str, Any]) -> None:
    raw["sections"][0]["requirements"][0]["text"] = "Project at least {param:some_horizon} years."


def _bad_parameter_code(raw: dict[str, Any]) -> None:
    raw["deadline"]["months_after_fye_param"] = "3"


def _platform_title_on_a_printed_category(raw: dict[str, Any]) -> None:
    raw["risk_categories"][0]["title_status"] = "platform"


def _mapping_without_supersedes(raw: dict[str, Any]) -> None:
    raw["section_key_map"] = [{"from": "overview", "to": "overview", "relation": "equivalent"}]


def _unmapped_relation_with_a_target(raw: dict[str, Any]) -> None:
    raw["supersedes"] = {"code": "zz_example", "version": "0.9"}
    raw["section_key_map"] = [{"from": "old", "to": "overview", "relation": "unmapped"}]


def _table5_citation_without_rows(raw: dict[str, Any]) -> None:
    raw["table5_citation"] = {"doc": "ZZ-DOC", "ref": "T5"}


CASES: tuple[tuple[str, Callable[[dict[str, Any]], None], str], ...] = (
    ("unknown top-level key", _unknown_top_level, ""),
    ("unknown schema id", _bad_schema_id, "schema"),
    ("unknown status", _bad_status, "status"),
    ("impossible date", _bad_date, "effective_from"),
    ("letter out of sequence", _letter_gap, "sections[1].letter"),
    ("order out of sequence", _order_gap, "sections[1].order"),
    ("duplicate section key", _duplicate_section_key, "sections[1].key"),
    ("section key shape", _bad_section_key, "sections[0].key"),
    ("duplicate requirement id", _duplicate_item_id, "sections[1].requirements[0]"),
    ("requirement with no citation", _item_without_citation, "sections[0].requirements[0]"),
    ("requirement text too long", _item_too_long, "sections[0].requirements[0].text"),
    ("requirement quotes the source", _item_quotes_the_source, "sections[0].requirements[0].text"),
    ("citation to an undeclared document", _unknown_citation_document, "sections[0].citation.doc"),
    ("unknown evidence token", _unknown_evidence_token, "sections[0].requirements[0].evidence[0]"),
    ("evidence names an unknown block", _unknown_evidence_block, "sections[0]"),
    ("evidence names an unrequested attachment", _unknown_evidence_attachment, "sections[0]"),
    ("unknown applicability condition", _unknown_condition, "sections[0].requirements[0]"),
    ("section expects a later-phase block", _unavailable_data_block, "sections[0].data_blocks[0]"),
    ("category number out of sequence", _category_number_gap, "risk_categories[0].number"),
    ("unknown Pillar 2 method", _unknown_method, "risk_categories[0].components[0]"),
    ("Table 5 row that is not declared", _table5_row_without_declaration, "risk_categories[0]"),
    ("declared Table 5 row nothing maps to", _unmapped_table5_row, "table5_rows"),
    (
        "capitalising component with no Table 5 row",
        _null_row_with_a_capitalising_method,
        "risk_categories[0].components[0].table5_row",
    ),
    ("regulator document marked relaxable", _relaxable_regulator_document, "attachments[0]"),
    ("duplicate attachment kind", _duplicate_attachment_kind, "attachments"),
    ("two stages freezing the cycle", _two_freezing_stages, "stages"),
    (
        "sourced section describing itself as pending",
        _sourced_section_that_says_pending,
        "sections[0].guidance",
    ),
    (
        "pending section that does not say so",
        _pending_section_that_does_not_say_so,
        "sections[0].guidance",
    ),
    (
        "figure placeholder with no declared parameter",
        _placeholder_without_a_declared_parameter,
        "sections[0].requirements[0].param_refs",
    ),
    ("a number where a parameter code belongs", _bad_parameter_code, "deadline"),
    (
        "platform title on a printed category",
        _platform_title_on_a_printed_category,
        "risk_categories[0].title_status",
    ),
    ("version lineage without a predecessor", _mapping_without_supersedes, "section_key_map"),
    ("unmapped section with a target", _unmapped_relation_with_a_target, "section_key_map[0].to"),
    ("Table 5 citation with no rows", _table5_citation_without_rows, "table5_citation"),
)


@pytest.mark.parametrize(
    ("mutate", "path"),
    [pytest.param(mutate, path, id=name) for name, mutate, path in CASES],
)
def test_a_broken_framework_is_refused_with_its_location(
    mutate: Callable[[dict[str, Any]], None], path: str
) -> None:
    with pytest.raises(FrameworkSchemaError) as caught:
        _parse(_break(mutate))
    assert caught.value.path.startswith(path)
