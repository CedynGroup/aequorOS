from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from app.schemas.common import JsonObject, JsonValue
from tests.factories.defaults import (
    DEFAULT_ASSESSMENT_NAME,
    DEFAULT_ASSESSMENT_TYPE,
    DEFAULT_CASE_STATUS,
    DEFAULT_CASE_TITLE,
    DEFAULT_CASE_TYPE,
    DEFAULT_UPLOAD_BYTE_SIZE,
    DEFAULT_UPLOAD_CONTENT_TYPE,
    DEFAULT_UPLOAD_FILENAME,
)


@dataclass(frozen=True)
class CasePayload:
    title: str
    case_type: str
    subject_type: str | None
    subject_name: str | None
    description: str | None
    status: str
    metadata: JsonObject = field(default_factory=dict)

    def api_json(self) -> dict[str, JsonValue]:
        return {
            "title": self.title,
            "case_type": self.case_type,
            "status": self.status,
            "subject_type": self.subject_type,
            "subject_name": self.subject_name,
            "description": self.description,
            "metadata": self.metadata,
        }


def case_payload(  # noqa: PLR0913
    *,
    title: str = DEFAULT_CASE_TITLE,
    case_type: str = DEFAULT_CASE_TYPE,
    status: str = DEFAULT_CASE_STATUS,
    subject_type: str | None = None,
    subject_name: str | None = None,
    description: str | None = None,
    metadata: JsonObject | None = None,
) -> CasePayload:
    return CasePayload(
        title=title,
        case_type=case_type,
        subject_type=subject_type,
        subject_name=subject_name,
        description=description,
        status=status,
        metadata=metadata if metadata is not None else {},
    )


@dataclass(frozen=True)
class UploadPayload:
    case_id: UUID
    filename: str
    content_type: str
    byte_size: int
    sha256: str | None

    def api_json(self) -> dict[str, JsonValue]:
        return {
            "case_id": str(self.case_id),
            "filename": self.filename,
            "content_type": self.content_type,
            "byte_size": self.byte_size,
            "sha256": self.sha256,
        }


def upload_payload(
    *,
    case_id: UUID,
    filename: str = DEFAULT_UPLOAD_FILENAME,
    content_type: str = DEFAULT_UPLOAD_CONTENT_TYPE,
    byte_size: int = DEFAULT_UPLOAD_BYTE_SIZE,
    sha256: str | None = None,
) -> UploadPayload:
    return UploadPayload(
        case_id=case_id,
        filename=filename,
        content_type=content_type,
        byte_size=byte_size,
        sha256=sha256,
    )


@dataclass(frozen=True)
class AssessmentPayload:
    case_id: UUID
    assessment_type: str
    name: str

    def api_json(self) -> dict[str, JsonValue]:
        return {
            "case_id": str(self.case_id),
            "assessment_type": self.assessment_type,
            "name": self.name,
        }


def assessment_payload(
    *,
    case_id: UUID,
    assessment_type: str = DEFAULT_ASSESSMENT_TYPE,
    name: str = DEFAULT_ASSESSMENT_NAME,
) -> AssessmentPayload:
    return AssessmentPayload(case_id=case_id, assessment_type=assessment_type, name=name)
