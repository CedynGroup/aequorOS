"""Shared setup for the ICAAP service tests: a bank, a cycle, and the flag on."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess, TenantContext
from app.core.config import get_settings
from app.domain.icaap.frameworks import registry
from app.models import Bank
from app.schemas.icaap import IcaapCycleCreate, IcaapCycleRead
from app.services.icaap import cycles
from tests.api.helpers import ORG_1, USER_1
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)
from tests.fixtures.icaap import synthetic_frameworks as synthetic

# P3 publishes a complete test instrument through ICAAP_EXTRA_FRAMEWORKS_DIR
# (Ghana's is 15/17 pending_primary_text, D-006). Registered here so the P3
# suites take it as an ordinary fixture instead of importing it per module.
from tests.services.icaap.p3_support import extra_frameworks  # noqa: F401

#: The canonical book runs 2025-04 .. 2026-03, so FY2025 has a 31 December
#: period end and a rehearsal cycle can bind real figures to it.
FY = 2025
AS_OF = date(2025, 12, 31)
GH_FRAMEWORK = ("bog_icaap", "2026.02-ed.1")


@pytest.fixture(autouse=True)
def icaap_enabled(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def canonical_book(db_session: Session) -> Session:
    materialize_canonical_test_book(db_session)
    db_session.commit()
    return db_session


@pytest.fixture
def access(canonical_book: Session) -> IcaapAccess:
    bank = canonical_book.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    return IcaapAccess(
        ctx=TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1),
        bank=bank,
    )


def rehearsal_payload(**overrides: object) -> IcaapCycleCreate:
    payload = {
        "fiscal_year": FY,
        "cycle_kind": "rehearsal",
        "basis": "solo",
        "framework_code": GH_FRAMEWORK[0],
        "framework_version": GH_FRAMEWORK[1],
        "reason": "Dry run before the first real filing",
    }
    payload.update(overrides)
    return IcaapCycleCreate.model_validate(payload)


@pytest.fixture
def cycle(canonical_book: Session, access: IcaapAccess) -> IcaapCycleRead:
    return cycles.create_cycle(canonical_book, access, rehearsal_payload())


@pytest.fixture
def synthetic_registry(monkeypatch: pytest.MonkeyPatch):
    """Publish a made-up instrument alongside the real one.

    Ghana ships one version and its first reporting date is still ahead, so the
    off-cycle kinds and the rebase path would otherwise be untestable until a
    second version exists — which is exactly when they first have to work.
    """

    # Scoped to the fixture bank's own jurisdiction: applicability is
    # jurisdiction-first, so a framework from elsewhere is never offered.
    one, two = synthetic.version_one("GH"), synthetic.version_two("GH")
    published = (*registry.load_all(), one, two)
    monkeypatch.setattr(registry, "_default", lambda: published)
    monkeypatch.setattr(registry, "all_frameworks", lambda: published)
    monkeypatch.setenv("ICAAP_FRAMEWORKS_ENABLED", f"{GH_FRAMEWORK[0]},{one.code}")
    get_settings.cache_clear()
    yield one, two
    get_settings.cache_clear()
