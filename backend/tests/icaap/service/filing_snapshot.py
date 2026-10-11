"""A frozen ICAAP package snapshot, in the exact shape ``snapshot.py`` freezes.

The filing exporters read nothing but this structure, so the fixture is written
against the builder's own keys rather than against a convenient shape — if the
two drift, these tests fail, which is the point. ``test_filing_snapshot_shape``
pins the agreement from the other direction by walking the builder's source.

The ICAAP prose deliberately contains:

* every block node the editor grammar admits — paragraph, heading, blockquote,
  bulletList, orderedList/listItem, hardBreak, dataBlock, factRef — so the
  renderers' walker is exercised over the whole vocabulary;
* the cedi sign and Ghanaian vowels, which the standard PDF font cannot draw
  (DV-004) and the embedded family must;
* text that looks like markup, because reportlab's ``Paragraph`` parses markup
  and user prose must print as typed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

GENERATED_AT = datetime(2026, 9, 19, 12, 0, 0, tzinfo=UTC)

#: The UUID the editor's ``dataBlock``/``factRef`` nodes reference.
CAPITAL_BLOCK_ID = "0195f2a1-0000-7000-8000-0000000000aa"

#: Characters the standard PDF font cannot draw. The filing PDF must print them.
CEDI = "₵"
AKAN_VOWELS = "ƐɛƆɔŊŋ"

RETURN_CODE = "ICAAP-REPORT"


@dataclass
class FakePackage:
    """The columns the provenance page reads (``from_snapshot.SnapshotPackage``)."""

    id: str = "0195f2a1-0000-7000-8000-0000000000f1"
    version: int = 1
    return_code: str = RETURN_CODE
    return_family: str = "icaap"
    content_digest: str | None = "c" * 64
    snapshot_sha256: str | None = "5" * 64
    generated_at: datetime = GENERATED_AT
    source_runs: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {
                "module": "capital",
                "run_id": "0195f2a1-0000-7000-8000-0000000000b1",
                "input_hash": "a" * 64,
                "engine_version": "capital-2026.3",
            }
        ]
    )


def prose_doc() -> dict[str, Any]:
    """One section document using every block node the grammar admits."""
    return {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "Total regulatory capital of "},
                    {
                        "type": "factRef",
                        "attrs": {
                            "blockId": CAPITAL_BLOCK_ID,
                            "factKey": "total_capital",
                            "suggestionId": None,
                        },
                    },
                    {"type": "text", "text": " was held at the reporting date."},
                ],
            },
            {
                "type": "heading",
                "attrs": {"level": 2},
                "content": [{"type": "text", "text": f"Capital in {CEDI} and {AKAN_VOWELS}"}],
            },
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "Bold", "marks": [{"type": "bold"}]},
                    {"type": "text", "text": " & "},
                    {"type": "text", "text": "italic", "marks": [{"type": "italic"}]},
                    {"type": "hardBreak"},
                    {
                        "type": "text",
                        "text": "<b>not markup</b>",
                        "marks": [{"type": "underline"}],
                    },
                ],
            },
            {
                "type": "blockquote",
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": "Quoted board minute."}],
                    }
                ],
            },
            {
                "type": "bulletList",
                "content": [
                    {
                        "type": "listItem",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": "Credit risk"}],
                            }
                        ],
                    }
                ],
            },
            {
                "type": "orderedList",
                "attrs": {"start": 1},
                "content": [
                    {
                        "type": "listItem",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": "Market risk"}],
                            }
                        ],
                    }
                ],
            },
            {"type": "dataBlock", "attrs": {"blockId": CAPITAL_BLOCK_ID}},
        ],
    }


def capital_block_entry() -> dict[str, Any]:
    return {
        "block_id": CAPITAL_BLOCK_ID,
        "block_key": "capital_position",
        "block_type": "capital_position",
        "title": "Capital position",
        "seq": 1,
        "payload_sha256": "b" * 64,
        "source_kind": "engine_run",
        "source_key": "capital:baseline",
        "source_as_of": "2026-12-31",
        "pin_reason": None,
        "payload": {
            "schema": "icaap-block-payload-v1",
            "title": "Capital position",
            "as_of": "2026-12-31",
            "source_label": "Official capital run (baseline)",
            "unit": {"currency": "GHS", "scale": 1},
            "tables": [
                {
                    "key": "capital_components",
                    "title": "Capital components",
                    "columns": [
                        {"key": "label", "label": "Component", "kind": "text"},
                        {"key": "amount", "label": "Amount", "kind": "amount"},
                    ],
                    "rows": [
                        {"cells": {"label": "CET1 capital", "amount": "1234567"}},
                        {
                            "cells": {"label": "Total capital", "amount": "1500000"},
                            "emphasis": "total",
                        },
                    ],
                }
            ],
        },
        "facts": {
            "total_capital": {"value": "1500000", "kind": "amount", "currency": "GHS"},
            "car_pct": {"value": "13.4", "kind": "ratio_pct"},
        },
        "never_public": False,
    }


def snapshot(
    *,
    cycle_kind: str = "annual",
    framework_status: str = "exposure_draft",
    with_annex: bool = True,
) -> dict[str, Any]:
    """The frozen envelope, as ``services/icaap/snapshot.py`` writes it."""
    sections: list[dict[str, Any]] = [
        {
            "code": "icaap_headline",
            "title": "Capital adequacy summary",
            "rows": [
                {
                    "code": "total_capital",
                    "description": "Total regulatory capital",
                    "value": "1500000",
                    "unit": "ghs",
                },
                {
                    "code": "car_pct",
                    "description": "Capital adequacy ratio",
                    "value": "13.4",
                    "unit": "pct",
                },
            ],
            "total": None,
            "optional": False,
            "unit": "ghs",
        },
        {
            "code": "s_capital_adequacy",
            "title": "a. Capital adequacy assessment",
            "rows": [{"code": "p1", "description": "", "value": "Prose is in the metadata."}],
            "optional": False,
        },
    ]
    if with_annex:
        sections.append(
            {
                "code": "annex_ICAAP-STRESS-APPENDIX2__stress_results",
                "title": "Appendix II — stress results",
                "rows": [
                    {
                        "code": "car_severe",
                        "description": "CAR under the severe scenario",
                        "value": "9.1",
                        "unit": "pct",
                    }
                ],
                "total": {
                    "code": "car_severe_total",
                    "description": "Minimum over the horizon",
                    "value": "9.1",
                    "unit": "pct",
                },
                "optional": False,
            }
        )

    return {
        "schema_version": "regulatory-package-v1",
        "return_code": RETURN_CODE,
        "return_family": "icaap",
        "regulator": "BOG",
        "template_id": "bog-icaap-report-v1",
        "fidelity": "PARTIAL",
        "reporting_date": "2026-12-31",
        "institution": {
            "bank_id": "BK-SAMP0001",
            "name": "Sample Bank Ghana PLC",
            "short_name": "Sample Bank",
            "currency": "GHS",
            "jurisdiction_code": "GH",
            "license_type": "universal",
        },
        "reporting_period": {
            "id": "0195f2a1-0000-7000-8000-0000000000c1",
            "label": "December 2026",
            "period_start": "2026-12-01",
            "period_end": "2026-12-31",
        },
        "sections": sections,
        "totals": [],
        "metadata": {
            "generated_at": GENERATED_AT.isoformat(),
            "icaap": {
                "schema": "icaap-report-snapshot-v1",
                "cycle": {
                    "id": "0195f2a1-0000-7000-8000-0000000000d1",
                    "kind": cycle_kind,
                    "fiscal_year": 2026,
                    "as_of_date": "2026-12-31",
                    "basis": "solo",
                    "round": 2,
                    "title": "ICAAP FY2026",
                    "supersedes_cycle_id": None,
                },
                "framework": {
                    "code": "BOG-ICAAP-2026",
                    "version": "2026.02",
                    "status": framework_status,
                    "digest": "f" * 64,
                    "title": "Guideline on Internal Capital Adequacy Assessment Process",
                    "short_title": "ICAAP Guideline",
                    "exposure_draft": framework_status == "exposure_draft",
                    "effective_from": "2027-01-01",
                },
                "institution_profile": {
                    "register_state_digest": "e" * 64,
                    "row_count": 12,
                },
                "regulator_short": "BoG",
                "sections": [
                    {
                        "key": "capital_adequacy",
                        "letter": "a",
                        "order": 1,
                        "title": "Capital adequacy assessment",
                        "citation": "BOG-ICAAP-ED:49(a)",
                        "citation_label": "ICAAP Guideline ¶49(a)",
                        "source_status": "sourced",
                        "version_no": 3,
                        "doc": prose_doc(),
                        "doc_sha256": "d" * 64,
                        "editor_schema_version": "icaap-editor-v1",
                        "ai_assisted_paragraphs": 2,
                        "requirements": [
                            {
                                "item_id": "REG-ICAAP-049a-1",
                                "text": "State the internal capital the Board considers adequate.",
                                "citation_label": "ICAAP Guideline ¶49(a)",
                                "status": "met",
                                "reason": None,
                                "citations": ["BOG-ICAAP-ED:49(a)"],
                            },
                            {
                                "item_id": "REG-ICAAP-049a-2",
                                "text": "Describe the group scope of the assessment.",
                                "citation_label": "ICAAP Guideline ¶49(a)",
                                "status": "not_applicable",
                                "reason": "The institution has no subsidiaries.",
                                "citations": ["BOG-ICAAP-ED:49(a)"],
                            },
                        ],
                    }
                ],
                "blocks": [capital_block_entry()],
                "attachments": [
                    {
                        "kind": "board_resolution",
                        "title": "Board resolution of 12 March 2027",
                        "sha256": "1" * 64,
                        "byte_size": 204800,
                        "media_type": "application/pdf",
                        "gate": "submission",
                        "attributes": {"resolution_reference": "BR-2027-04"},
                    },
                    {
                        "kind": "senior_management_report",
                        "title": "Senior management report",
                        "sha256": "2" * 64,
                        "byte_size": 51200,
                        "media_type": "application/pdf",
                        "gate": "freeze",
                        "attributes": {"commenting_functions": ["risk", "finance"]},
                    },
                ],
                "attachment_requirements": [
                    {
                        "kind": "board_resolution",
                        "title": "Board resolution",
                        "gate": "submission",
                        "min_count": 1,
                        "applies": True,
                    },
                    {
                        "kind": "senior_management_report",
                        "title": "Senior management report",
                        "gate": "freeze",
                        "min_count": 1,
                        "applies": True,
                    },
                ],
                "stages": [
                    {
                        "seq": 1,
                        "stage_key": "preparation",
                        "title": "Preparation",
                        "decision_kind": "prepare",
                        "officer_titles": [],
                        "freeze_on_approve": False,
                        "source": "framework_default",
                        "decisions": [
                            {
                                "decision": "submitted",
                                "round": 2,
                                "decided_by_name": "Ama Mensah",
                                "officer_title": "Chief Financial Officer",
                                "decided_at": "2027-02-01T09:00:00+00:00",
                                "review_digest": "9" * 64,
                                "comment": None,
                                "return_to_seq": None,
                            }
                        ],
                    },
                    {
                        "seq": 2,
                        "stage_key": "brc_review",
                        "title": "Board Risk Committee",
                        "decision_kind": "approve",
                        "officer_titles": ["Board Risk Committee Chair"],
                        "freeze_on_approve": True,
                        "source": "framework_default",
                        "decisions": [
                            {
                                "decision": "approved",
                                "round": 2,
                                "decided_by_name": "Efua Asante",
                                "officer_title": "Board Risk Committee Chair",
                                "decided_at": "2027-02-10T15:30:00+00:00",
                                "review_digest": "9" * 64,
                                "comment": None,
                                "return_to_seq": None,
                            }
                        ],
                    },
                ],
                "annexes": (
                    [
                        {
                            "return_code": "ICAAP-STRESS-APPENDIX2",
                            "package_id": "0195f2a1-0000-7000-8000-0000000000e1",
                            "version": 2,
                            "content_digest": "7" * 64,
                            "source_runs": [],
                        }
                    ]
                    if with_annex
                    else []
                ),
                "parameters": [
                    {
                        "param_code": "car_min",
                        "resolved": True,
                        "value": "13",
                        "value_json": None,
                        "unit": "pct",
                        "confirmation_status": "pending",
                        "source_citation": "CRD 2018 s.29, pending stakeholder confirmation",
                        "effective_from": "2020-01-01",
                        "scope": "institution_class:bank",
                    },
                    {
                        "param_code": "icaap_stress_horizon_years_min",
                        "resolved": False,
                    },
                ],
                "filing": {
                    "attestation_source_status": "platform_default",
                    "attestation_lines": {
                        "preparer": "Prepared by the officer responsible for the ICAAP: ",
                        "approver": "Approved for submission on behalf of Senior Management: ",
                        "board": (
                            "Approved by the Board of Directors, which reviewed and "
                            "challenged this ICAAP: "
                        ),
                    },
                    "statements": {
                        "preparer": (
                            "I prepared this ICAAP report from the institution's own records "
                            "and calculations as at the reporting date."
                        ),
                        "approver": (
                            "Senior Management has reviewed this ICAAP report and approves "
                            f"its submission. Total capital held is {CEDI}1,500,000."
                        ),
                        "board": (
                            "The Board has reviewed and challenged this ICAAP and approves it."
                        ),
                    },
                    "exposure_draft_line": (
                        "EXPOSURE DRAFT — prepared under a guideline the regulator has "
                        "not finalised."
                    ),
                },
                "review_digest": "9" * 64,
            },
        },
    }


__all__ = [
    "AKAN_VOWELS",
    "CAPITAL_BLOCK_ID",
    "CEDI",
    "GENERATED_AT",
    "RETURN_CODE",
    "FakePackage",
    "capital_block_entry",
    "prose_doc",
    "snapshot",
]
