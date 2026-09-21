"""Two versions of a made-up instrument, for testing version lineage.

Ghana publishes one version today, so rebase would otherwise be untestable
until a second one exists — and rebase is exactly the machinery that has to
work the first time the regulator's final text lands. These frameworks carry no
country identity: the jurisdiction is ``ZZ``, which is the ISO user-assigned
range.
"""

from __future__ import annotations

from typing import Any

from app.domain.icaap.frameworks.registry import framework_digest
from app.domain.icaap.frameworks.schema import Framework, parse_framework

_DOC = {
    "id": "ZZ-DOC",
    "title": "Guidance Note on Internal Capital Adequacy",
    "short_label": "Guidance Note",
    "issuer": "Example Regulator",
    "issued": "2026-01",
    "status": "final",
    "url": None,
}
_CITE = {"doc": "ZZ-DOC", "ref": "1"}


def _item(item_id: str, *, waivable: bool = False, applies_when: str | None = None) -> dict:
    return {
        "id": item_id,
        "text": f"Address the matter recorded as {item_id}.",
        "citations": [dict(_CITE)],
        "evidence": ["narrative"],
        "waivable": waivable,
        "applies_when": applies_when,
    }


def _section(key: str, letter: str, order: int, items: list[dict]) -> dict:
    return {
        "key": key,
        "letter": letter,
        "order": order,
        "title": key.replace("_", " ").title(),
        "citation": dict(_CITE),
        "source_status": "sourced",
        "guidance": f"What the {key} section must cover.",
        "data_blocks": [],
        "ai_draftable": True,
        "public_disclosure": False,
        "requirements": items,
    }


def _base(version: str, sections: list[dict], jurisdiction: str = "ZZ") -> dict[str, Any]:
    return {
        "schema": "aequoros-icaap-framework-v1",
        "code": "zz_example",
        "jurisdiction": jurisdiction,
        "regulator": "EXR",
        "title": "Example Guidance Note on ICAAP",
        "short_title": "Example ICAAP Note",
        "version": version,
        "status": "final",
        "effective_from": "2026-01-01",
        "effective_from_citations": [dict(_CITE)],
        "first_as_of_date": "2024-12-31",
        "first_as_of_basis": "sourced",
        "institution_classes": ["bank"],
        "filing_return_code": None,
        "supersedes": None,
        "section_key_map": [],
        "cross_framework_section_map": [],
        "documents": [dict(_DOC)],
        "related_instruments": [],
        "deadline": {
            "as_of": "fy_end",
            "fy_end_month_day": "12-31",
            "months_after_fye_param": "icaap_submission_months",
            "citations": [dict(_CITE)],
        },
        "document_citations": [],
        "sections": sections,
        "table5_rows": [],
        "table5_citation": None,
        "risk_categories": [
            {
                "key": "credit",
                "number": 1,
                "title": "Credit risk",
                "citation": dict(_CITE),
                "title_status": "verbatim",
                "pillar1_coverage": "partial",
                "custom": False,
                "components": [
                    {
                        "key": "credit_concentration",
                        "table5_row": None,
                        "p29_class": "pillar1_not_fully_captured",
                        "p29_basis": "inferred",
                        "allowed_methods": ["not_capitalised"],
                    }
                ],
                "sub_requirements": [],
            }
        ],
        "attachments": [],
        "stages": [
            {
                "seq": 1,
                "key": "preparation",
                "title": "Preparation",
                "decision": "prepare",
                "default_officer_titles": [],
                "freeze_on_approve": False,
                "citations": [],
            },
            {
                "seq": 2,
                "key": "board_approval",
                "title": "Board approval",
                "decision": "approve",
                "default_officer_titles": [],
                "freeze_on_approve": True,
                "citations": [],
            },
        ],
        "stages_source_status": "platform_default",
        "material_change_triggers": [
            {"code": "other", "label": "Other material change", "citations": [dict(_CITE)]}
        ],
        "materiality_matrix": {
            "source_status": "platform_default",
            "likelihood_levels": [{"score": 1, "key": "rare", "label": "Rare"}],
            "impact_levels": [{"score": 1, "key": "minor", "label": "Minor"}],
            "rating_bands_param": "icaap_materiality_rating_bands",
            "material_if": {
                "min_score_param": "icaap_materiality_material_min_score",
                "or_min_impact_param": "icaap_materiality_material_min_impact",
            },
        },
        "disclosure": None,
        "notes": [],
    }


def version_one_raw(jurisdiction: str = "ZZ") -> dict[str, Any]:
    return _base(
        "1.0",
        [
            _section("overview", "a", 1, [_item("keep_one"), _item("drop_one")]),
            _section("risk_profile", "b", 2, [_item("keep_two")]),
            _section("appetite", "c", 3, [_item("keep_three")]),
            _section("models", "d", 4, [_item("drop_two")]),
            _section("retired_topic", "e", 5, [_item("drop_three")]),
        ],
        jurisdiction,
    )


def version_two_raw(jurisdiction: str = "ZZ") -> dict[str, Any]:
    """A successor that renames, splits, merges and drops a section."""
    raw = _base(
        "2.0",
        [
            _section("summary", "a", 1, [_item("keep_one")]),
            _section("risk_and_appetite", "b", 2, [_item("keep_two"), _item("keep_three")]),
            _section("models", "c", 3, []),
            _section("new_topic", "d", 4, [_item("brand_new")]),
        ],
        jurisdiction,
    )
    # Later than version one's, so version one still applies to the years it
    # was published for: a successor supersedes from ITS first reporting date,
    # not retrospectively.
    raw["first_as_of_date"] = "2027-12-31"
    raw["supersedes"] = {"code": "zz_example", "version": "1.0"}
    raw["section_key_map"] = [
        {"from": "overview", "to": "summary", "relation": "equivalent"},
        {"from": "risk_profile", "to": "risk_and_appetite", "relation": "merge"},
        {"from": "appetite", "to": "risk_and_appetite", "relation": "merge"},
        {"from": "models", "to": "models", "relation": "partial"},
        {"from": "retired_topic", "to": None, "relation": "unmapped"},
    ]
    return raw


def _parse(raw: dict[str, Any]) -> Framework:
    return parse_framework(raw, digest=framework_digest(raw))


def version_one(jurisdiction: str = "ZZ") -> Framework:
    return _parse(version_one_raw(jurisdiction))


def version_two(jurisdiction: str = "ZZ") -> Framework:
    return _parse(version_two_raw(jurisdiction))


def unrelated() -> Framework:
    """A framework that declares no lineage, so a rebase onto it is refused."""
    raw = _base("3.0", [_section("overview", "a", 1, [_item("keep_one")])])
    return _parse(raw)


__all__ = [
    "unrelated",
    "version_one",
    "version_one_raw",
    "version_two",
    "version_two_raw",
]
