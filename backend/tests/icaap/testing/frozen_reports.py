"""Coherent frozen-report inputs; no persistence or lifecycle is simulated."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from app.icaap.domain.annexes import AnnexKey, AnnexVersion
from app.icaap.domain.frameworks.schema import Framework
from app.reporting.public import RegulatoryPackage
from tests.icaap.testing.annexes import InMemoryCurrentAnnexRepository
from tests.support.helpers import ORG_1

AS_OF = date(2025, 12, 31)
BANK_ID = "BK-RULE0001"
ANNEX_CODE = "ICAAP-STRESS-APPENDIX2"


@dataclass
class FrozenReportInput:
    package: RegulatoryPackage
    assessment: dict[str, object]
    blocks: list[dict[str, object]]
    sections: list[dict[str, object]]
    annex_key: AnnexKey
    annex_version: AnnexVersion
    annexes: InMemoryCurrentAnnexRepository


def frozen_report_input(
    framework: Framework, repository: type[InMemoryCurrentAnnexRepository]
) -> FrozenReportInput:
    sections: list[dict[str, object]] = [
        {
            "key": section.key,
            "version_no": 1,
            "doc_sha256": "d" * 64,
            "source_status": section.source_status,
            "requirements": [
                {"item_id": item.id, "status": "met", "reason": None, "citations": []}
                for item in section.requirements
            ],
        }
        for section in framework.sections
    ]
    facts: dict[str, dict[str, object]] = {
        "capital_position": {"tier1_ratio_pct": "20", "total_rwa": "50000"},
        "capital_reconciliation": {"internal_capital_coverage_pct": "125"},
        "appendix_ii": {
            "horizon_years": "3",
            "severe_scenario_count": "2",
            "with_management_actions": True,
        },
        "reverse_stress": {"breach_scenario": "funding withdrawal"},
        "stress_narratives": {"attested_on": AS_OF.isoformat()},
    }
    blocks: list[dict[str, object]] = [
        {
            "block_type": kind,
            "block_key": kind,
            "seq": 1,
            "source_as_of": AS_OF.isoformat(),
            "facts": {key: {"value": value} for key, value in values.items()},
        }
        for kind, values in facts.items()
    ]
    annex_key = AnnexKey(ORG_1, BANK_ID, ANNEX_CODE, AS_OF, "solo")
    annex_version = AnnexVersion(UUID("22222222-2222-4222-8222-222222222222"), 2, "a" * 64)
    assessment: dict[str, object] = {
        "cycle": {"kind": "annual"},
        "framework": {
            "code": framework.code,
            "version": framework.version,
            "digest": framework.digest,
            "exposure_draft": False,
        },
        "sections": sections,
        "blocks": blocks,
        "attachments": [
            {
                "kind": "senior_management_report",
                "sha256": "b" * 64,
                "attributes": {"commenting_functions": ["Risk", "Finance"]},
            }
        ],
        "attachment_requirements": [
            {"kind": "senior_management_report", "gate": "freeze", "min_count": 1}
        ],
        "annexes": [{"return_code": ANNEX_CODE, "content_digest": annex_version.content_digest}],
        "parameters": [
            {
                "param_code": code,
                "value": value,
                "resolved": True,
                "confirmation_status": "pending",
            }
            for code, value in (
                ("icaap_stress_horizon_years_min", "3"),
                ("icaap_stress_severe_scenarios_min", "2"),
            )
        ],
    }
    package = RegulatoryPackage(
        id=UUID("11111111-1111-4111-8111-111111111111"),
        organization_id=ORG_1,
        bank_id=BANK_ID,
        return_code="ICAAP-REPORT",
        return_family="icaap",
        reporting_date=AS_OF,
        frequency="annual",
        basis="solo",
        status="generated",
        version=1,
        snapshot={"metadata": {"icaap": assessment}},
    )
    return FrozenReportInput(
        package,
        assessment,
        blocks,
        sections,
        annex_key,
        annex_version,
        repository([(annex_key, annex_version)]),
    )
