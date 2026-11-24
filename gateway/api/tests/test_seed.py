# pyright: standard
"""The seed: refuses production, and builds the design's world through the real paths."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.clock import SettableClock
from app.core.context import app_context
from app.core.settings import Settings
from app.db.models import ReturnVersionApproval, StaffUser
from app.idp.tokens import mint_id_token
from app.main import create_app
from app.seed.__main__ import main as seed_main
from app.seed.synthetic_staff import find_dev_staff
from tests.harness import migrate


def test_seed_refuses_production(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings = Settings(
        env="production", database_url=f"sqlite+pysqlite:///{tmp_path}/x.db", keys_dir=tmp_path
    )
    assert seed_main([], settings) == 2
    assert "refusing to seed" in capsys.readouterr().err


@pytest.fixture(scope="module")
def seeded(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    root = tmp_path_factory.mktemp("seed")
    url = f"sqlite+pysqlite:///{root}/seed.db"
    migrate(url)
    settings = Settings(
        env="local",
        database_url=url,
        keys_dir=root / "keys",
        base_url="http://testserver",
        scheduler_enabled=False,
    )
    assert seed_main(["--live-now", "2026-11-21T16:40:00Z"], settings) == 0
    assert seed_main([], settings) == 2  # a second run refuses an already-seeded database
    clock = SettableClock(datetime(2026, 11, 21, 16, 45, tzinfo=UTC))
    app = create_app(settings, clock=clock, run_scheduler=False)
    client = TestClient(app)
    staff = find_dev_staff("kowusu")
    assert staff is not None
    client.headers["Authorization"] = "Bearer " + mint_id_token(
        app_context(app), staff, groups=staff.groups, mfa=staff.mfa
    )
    return client


def test_seeded_world_matches_the_design_anchors(seeded: TestClient) -> None:
    latest = seeded.get("/portal/api/submissions?limit=14").json()["items"]
    assert latest[0]["receipt_id"] == "SRG-26-0114822"
    assert [(r["institution_name"], r["return_code"], r["status"]) for r in latest[:4]] == [
        ("Meridian Coast Bank", "BSD2", "validating"),
        ("Ridgeway Commercial Bank", "CAR-RWA", "accepted"),
        ("Palmgrove Bank", "BSD2", "in_review"),
        ("Harbourline Bank", "LMT", "rejected"),
    ]
    # The rejected LMT is missing a required section of the aligned definition.
    assert latest[3]["validation"]["failed"] == 1
    palmgrove = seeded.get(f"/portal/api/submissions/{latest[2]['receipt_id']}").json()
    assert palmgrove["row"]["version"] == 2
    # BSD2 is the official form: its checks are the workbook's own totals.
    assert palmgrove["validation"]["groups"] == [
        {"label": "Arithmetic", "passed": 535, "total": 535, "note": "Totals and subtotals"},
    ]
    assert {section["title"] for section in palmgrove["content"]["sections"]} >= {"BSD2"}
    changed = [line for line in palmgrove["content"]["lines"] if line["changed"]]
    moved = {
        line["code"]: Decimal(line["value"]) - Decimal(line["previous_version_value"])
        for line in changed
        if not line["is_total"]
    }
    assert sorted(moved.values()) == [Decimal("-4.2"), Decimal("4.2")]
    assert all(line["prior_period_value"] is not None for line in changed if not line["is_total"])
    assert palmgrove["evidence"]["signature"] == {
        "valid": True,
        "alg": "ES256",
        "kid": "pgb-sign-2026-01",
        "key_registered_at": "2026-02-02T10:30:00.000Z",
        "custody_note": palmgrove["evidence"]["signature"]["custody_note"],
    }
    assert palmgrove["evidence"]["certificate"]["not_after"].startswith("2027-03-14")
    queries = seeded.get("/portal/api/queries").json()
    riverbend = next(q for q in queries["items"] if q["institution_name"] == "Riverbend Rural Bank")
    assert riverbend["number"] == "QRY-2026-0318"
    assert riverbend["status"] == "awaiting_institution"
    assert queries["counts"]["closed"] == 48
    overview = seeded.get("/portal/api/overview").json()
    assert overview["totals"]["institutions"] == 48
    northgate = next(
        r
        for r in overview["heatmap"]["rows"]
        if r["institution_name"] == "Northgate Savings & Loans"
    )
    assert sum(1 for c in northgate["cells"] if c["state"] == "outstanding") == 2
    draft = seeded.get("/portal/api/returns/BSD5A").json()["draft"]
    assert draft["version"] == "2027.1"
    assert [a["step"] for a in draft["approvals"]] == ["drafted", "approved", "pending"]
    audit = seeded.post("/portal/api/audit/verify").json()
    assert audit["intact"] is True
    assert audit["entries"] > 10_000


def test_definitions_only_starts_clean(tmp_path: Path) -> None:
    """A clean start: the aligned definitions, and no demo institutions or filings."""
    url = f"sqlite+pysqlite:///{tmp_path}/clean.db"
    migrate(url)
    settings = Settings(
        env="local",
        database_url=url,
        keys_dir=tmp_path / "keys",
        base_url="http://testserver",
        scheduler_enabled=False,
    )
    assert seed_main(["--definitions-only"], settings) == 0
    app = create_app(
        settings, clock=SettableClock(datetime(2026, 11, 22, 9, tzinfo=UTC)), run_scheduler=False
    )
    client = TestClient(app)
    staff = find_dev_staff("kowusu")
    assert staff is not None
    client.headers["Authorization"] = "Bearer " + mint_id_token(
        app_context(app), staff, groups=staff.groups, mfa=staff.mfa
    )
    assert client.get("/portal/api/institutions").json()["items"] == []
    assert client.get("/portal/api/submissions").json()["items"] == []
    returns = client.get("/portal/api/returns").json()["items"]
    assert {item["code"] for item in returns} >= {"BSD2", "CAR-RWA", "LMT"}
    overview = client.get("/portal/api/overview")
    assert overview.status_code == 200, overview.text
    # The baseline is the catalogue's: no person is recorded as drafting or approving it,
    # and no synthetic staff member exists in a clean gateway.
    with app_context(app).db.session() as session:
        steps = session.scalars(select(ReturnVersionApproval)).all()
        assert steps
        assert {(a.step, a.actor_name) for a in steps} == {("drafted", "AequorOS return catalogue")}
        known = {row.subject for row in session.scalars(select(StaffUser))}
        assert known <= {"kowusu"}  # only the token this test minted to read the API
    audit = client.get("/portal/api/audit?category=configuration&limit=200").json()["items"]
    assert audit
    assert {item["actor_label"] for item in audit} == {"System"}
