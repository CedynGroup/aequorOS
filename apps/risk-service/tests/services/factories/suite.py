from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import Settings
from tests.services.factories.assessments import AssessmentServiceFactory
from tests.services.factories.cases import CaseServiceFactory
from tests.services.factories.documents import DocumentServiceFactory
from tests.services.factories.financial_workspace import FinancialWorkspaceFactory
from tests.services.factories.shared import MutableObjectStorage


@dataclass(frozen=True)
class ServiceFactories:
    db: Session
    storage: MutableObjectStorage
    settings: Settings
    ctx: TenantContext
    cases: CaseServiceFactory
    documents: DocumentServiceFactory
    assessments: AssessmentServiceFactory
    financial: FinancialWorkspaceFactory

    def __init__(
        self,
        db: Session,
        storage: MutableObjectStorage,
        settings: Settings,
        ctx: TenantContext,
    ) -> None:
        cases = CaseServiceFactory(db, ctx)
        object.__setattr__(self, "db", db)
        object.__setattr__(self, "storage", storage)
        object.__setattr__(self, "settings", settings)
        object.__setattr__(self, "ctx", ctx)
        object.__setattr__(self, "cases", cases)
        object.__setattr__(
            self,
            "documents",
            DocumentServiceFactory(db, storage, settings, ctx, cases=cases),
        )
        object.__setattr__(self, "assessments", AssessmentServiceFactory(db, ctx, cases=cases))
        object.__setattr__(self, "financial", FinancialWorkspaceFactory(db, ctx))
