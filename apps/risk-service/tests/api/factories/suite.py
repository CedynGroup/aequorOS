from __future__ import annotations

from dataclasses import dataclass

from fastapi.testclient import TestClient

from tests.api.factories.assessments import AssessmentFactory
from tests.api.factories.cases import CaseFactory
from tests.api.factories.documents import DocumentFactory, MutableFakeStorage


@dataclass(frozen=True)
class ApiFactories:
    client: TestClient
    fake_storage: MutableFakeStorage
    cases: CaseFactory
    documents: DocumentFactory
    assessments: AssessmentFactory

    def __init__(self, client: TestClient, fake_storage: MutableFakeStorage) -> None:
        cases = CaseFactory(client)
        object.__setattr__(self, "client", client)
        object.__setattr__(self, "fake_storage", fake_storage)
        object.__setattr__(self, "cases", cases)
        object.__setattr__(self, "documents", DocumentFactory(client, fake_storage, cases=cases))
        object.__setattr__(self, "assessments", AssessmentFactory(client, cases=cases))
