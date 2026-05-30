from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.session import get_engine
from app.models import Document, Job, RiskFinding
from tests.api.factories import CaseFactory, DocumentFactory
from tests.api.helpers import ORG_1


def test_db_rows_are_scoped_to_expected_org(
    db_client: TestClient,
    fake_storage,
    db_settings: Settings,
) -> None:
    case_id = str(CaseFactory(db_client).create()["id"])
    document_id = str(
        DocumentFactory(db_client, fake_storage).create_uploaded(case_id=case_id)["document_id"]
    )
    engine = get_engine(db_settings.database.database_url or "")

    with Session(engine) as session:
        document = session.scalar(select(Document).where(Document.id == UUID(document_id)))
        job_count = len(list(session.scalars(select(Job))))
        finding_count = len(list(session.scalars(select(RiskFinding))))
    assert document is not None
    assert document.organization_id == ORG_1
    assert job_count == 0
    assert finding_count == 0
