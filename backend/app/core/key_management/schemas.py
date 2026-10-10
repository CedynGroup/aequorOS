from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.key_management.types import KeyReference, KeyStatus


class BankKeyConnect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["aws_kms"] = "aws_kms"
    key_id: str = Field(min_length=1, max_length=2048, pattern=r"^arn:aws[^:]*:kms:")
    region: str = Field(min_length=1, max_length=64)
    owner_account: str = Field(pattern=r"^\d{12}$")

    def reference(self) -> KeyReference:
        return KeyReference(self.provider, self.key_id, self.region, self.owner_account)


class BankKeyRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bank_id: str
    provider: str
    key_id: str
    region: str
    owner_account: str
    status: KeyStatus
    checked_at: datetime


class BankKeyRotate(BankKeyConnect):
    reason: str = Field(min_length=10, max_length=2000)
