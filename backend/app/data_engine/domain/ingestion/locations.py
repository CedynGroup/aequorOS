"""Staged object handles accepted from tenants by the Data Engine."""

from __future__ import annotations

from pydantic import ConfigDict, Field, RootModel, field_validator

TEMP_SCHEME = "temp://"


class StagedIngestionLocation(RootModel[str]):
    """An opaque key in the requesting bank's temp tier, never a server path.

    Handles are consumed literally: no URL decoding or filesystem normalization.
    The bank's storage namespace is selected separately from authenticated context.
    """

    model_config = ConfigDict(frozen=True, revalidate_instances="always")

    root: str = Field(pattern=r"^temp://[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")

    @field_validator("root")
    @classmethod
    def reject_dot_segments(cls, value: str) -> str:
        if any(part in {".", ".."} for part in value[len(TEMP_SCHEME) :].split("/")):
            raise ValueError("Staged ingestion handles cannot contain dot segments.")
        return value

    @property
    def object_path(self) -> str:
        return self.root[len(TEMP_SCHEME) :]

    def __str__(self) -> str:
        return self.root
