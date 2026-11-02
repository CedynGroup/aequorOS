from __future__ import annotations

from dataclasses import dataclass

from app.case.schemas.assessments import AssessmentRunRead
from app.case.schemas.calculations import CalculationRunListRead
from app.case.schemas.cases import CaseBulkActionRead, CaseRead, ScoreRead
from app.case.schemas.findings import FindingRead


@dataclass(frozen=True)
class CaseResponses:
    restricted: bool

    def case(self, value: object) -> CaseRead:
        result = CaseRead.model_validate(value)
        if not self.restricted:
            result = result.model_copy(
                update={
                    "metadata": {k: v for k, v in result.metadata.items() if k != "structured_data"}
                }
            )
        return result

    def bulk(self, result: CaseBulkActionRead) -> CaseBulkActionRead:
        return result.model_copy(
            update={
                "succeeded": [
                    item.model_copy(update={"case": self.case(item.case)})
                    for item in result.succeeded
                ]
            }
        )

    def score(self, value: object) -> ScoreRead:
        result = ScoreRead.model_validate(value)
        if not self.restricted:
            result = result.model_copy(
                update={
                    "input_snapshot": {},
                    "rule_results": [
                        {
                            k: v
                            for k, v in rule.items()
                            if k in ("rule_id", "risk_type", "score_impact")
                        }
                        for rule in result.rule_results
                    ],
                }
            )
        return result

    def finding(self, value: object) -> FindingRead:
        result = FindingRead.model_validate(value)
        if not self.restricted:
            result = result.model_copy(
                update={
                    "details": {},
                    "summary": f"{result.risk_type.replace('_', ' ').capitalize()} finding.",
                    "rationale": None,
                    "disposition_reason": None,
                }
            )
        return result

    def assessment_run(self, value: object) -> AssessmentRunRead:
        result = AssessmentRunRead.model_validate(value)
        if not self.restricted:
            result = result.model_copy(
                update={
                    "summary": {
                        k: v
                        for k, v in result.summary.items()
                        if k
                        in (
                            "findings_created",
                            "risk_score",
                            "risk_level",
                            "rules_evaluated",
                            "scoring_version",
                            "score_id",
                            "input_hash",
                        )
                    },
                    "error": "Assessment failed." if result.error is not None else None,
                }
            )
        return result

    def calculation_runs(self, result: CalculationRunListRead) -> CalculationRunListRead:
        if self.restricted:
            return result
        return result.model_copy(
            update={
                field: [
                    run.model_copy(
                        update={
                            "error": run.error.model_copy(
                                update={"details": None, "message": "Calculation failed."}
                            )
                            if run.error is not None
                            else None
                        }
                    )
                    for run in runs
                ]
                for field, runs in (
                    ("runs", result.runs),
                    (
                        "latest_successful_runs_by_scenario",
                        result.latest_successful_runs_by_scenario,
                    ),
                )
            }
        )
