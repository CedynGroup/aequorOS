"""Administrator-facing bank key setup and diagnostic contracts."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.key_management.schemas import BankKeyRead


class BankKeyStatusRead(BaseModel):
    connected: bool
    key: BankKeyRead | None = None
    current_version: UUID | None = None
    last_successful_use_at: datetime | None = None
    last_rotated_at: datetime | None = None
    rotation_period_days: int | None = None
    rotation_confirmed_at: datetime | None = None
    workload_role_arn: str | None = None


class BankKeyTestRead(BaseModel):
    passed: bool
    message: str


class BankKeyScheduleConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key_id: str = Field(min_length=1, max_length=2048)
    period_days: int = Field(ge=90, le=2560)
    reason: str = Field(min_length=10, max_length=2000)
