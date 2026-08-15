"""Operating-Environment desk API contracts (operator control plane ONLY).

Serves the STAFF console under ``app.operator`` (Market Desk → Operating
Environment); nothing here is mounted on the tenant API. Actor identities are
operator emails (workforce IdP), never tenant users.

``inputs`` and ``breakdown`` are typed as open JSON objects with documented
shapes (the same treatment ``DeskDeterminationRead`` gives ``derived_values``/
``qa_results``): the ``breakdown`` mirrors
``app.domain.rating.operating_environment.OperatingEnvironmentResult``.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeskOperatingEnvironmentInputs(ClosedModel):
    """The observable inputs for one jurisdiction/COB.

    Economic + banking-system aggregates and the one judgment sub-score are
    desk-entered (v1). ``sovereign_rating`` and ``policy_rate_pct`` are
    OPTIONAL overrides: left unset, the service auto-pulls the published
    sovereign rating and the MPR reference rate; set, they take precedence and
    are recorded as desk-entered provenance.
    """

    # Pillar 1 — economic risk
    real_gdp_growth_pct: Decimal
    gdp_per_capita_usd: Decimal
    cpi_inflation_pct: Decimal
    private_credit_to_gdp_growth_pct: Decimal
    system_npl_pct: Decimal
    private_debt_to_gdp_pct: Decimal
    # Pillar 2 — industry risk
    regulatory_quality_score: int = Field(ge=1, le=6)
    system_roa_pct: Decimal
    system_credit_growth_pct: Decimal
    system_loan_to_deposit_pct: Decimal
    system_car_pct: Decimal
    external_funding_pct: Decimal
    # Auto-pulled by default; provide to override.
    sovereign_rating: str | None = Field(default=None, max_length=20)
    policy_rate_pct: Decimal | None = None


class DeskOperatingEnvironmentComputeRequest(ClosedModel):
    """Compute-preview / stage-draft request body."""

    jurisdiction_code: str = Field(default="GH", min_length=2, max_length=8)
    cob_date: date
    inputs: DeskOperatingEnvironmentInputs


class DeskOperatingEnvironmentPreview(ClosedModel):
    """A pure computation result — nothing is persisted."""

    jurisdiction_code: str
    cob_date: date
    methodology_version: str
    score: Decimal
    input_digest: str
    inputs: dict[str, Any] = Field(title="Operating Environment Input Snapshot")
    breakdown: dict[str, Any] = Field(title="Operating Environment Breakdown")


class DeskOperatingEnvironmentAssessmentRead(ClosedModel):
    id: UUID
    jurisdiction_code: str
    cob_date: date
    methodology_version: str
    score: Decimal
    status: str
    input_digest: str
    inputs: dict[str, Any] = Field(title="Operating Environment Input Snapshot")
    breakdown: dict[str, Any] = Field(title="Operating Environment Breakdown")
    proposed_by: str
    approved_by: str | None = Field(default=None, title="Operating Environment Approved By")
    approved_at: datetime | None = Field(default=None, title="Operating Environment Approved At")
    published_at: datetime | None = Field(default=None, title="Operating Environment Published At")
    created_at: datetime


class DeskOperatingEnvironmentAssessmentListRead(ClosedModel):
    assessments: list[DeskOperatingEnvironmentAssessmentRead]
    total: int


class DeskOperatingEnvironmentPublishResult(ClosedModel):
    """Outcome of a publish fan-out (per-bank results, error strings only)."""

    assessment_id: UUID
    status: str
    banks: int
    results: list[dict[str, Any]] = Field(title="Operating Environment Publish Results")
