from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.session import get_engine
from app.models import Document, Job, RiskFinding
from tests.api.factories import ApiFactories
from tests.api.helpers import ORG_1


def test_db_rows_are_scoped_to_expected_org(
    api_factories: ApiFactories,
    db_settings: Settings,
) -> None:
    document_id = str(api_factories.documents.create_uploaded().document_id)
    engine = get_engine(db_settings.database.database_url or "")

    with Session(engine) as session:
        document = session.scalar(select(Document).where(Document.id == UUID(document_id)))
        job_count = len(list(session.scalars(select(Job))))
        finding_count = len(list(session.scalars(select(RiskFinding))))
    assert document is not None
    assert document.organization_id == ORG_1
    assert job_count == 0
    assert finding_count == 0
