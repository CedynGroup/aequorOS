"""FC-3 curve construction service: construction + QA, definition governance, publish.

Construction is a pure function of (quotes x definition version x calendar) — same
inputs, identical grid and value digest. Definition governance mirrors the
methodology register's Track-2 rules (immutable after approval, dual control).
Publication reuses the desk determination -> fan-out seam, so a constructed forward
curve lands in every bank's canonical store under its AEQ.* code.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.curves.calendars import GHANA
from app.models import (
    Bank,
    CanonicalYieldCurve,
    DeskCurveDefinition,
    DeskDetermination,
    DeskPublication,
)
from app.services.market_desk import curve_construction as cc
from app.services.market_desk import determinations, register
from tests.api.helpers import ORG_1, ORG_2
from tests.storage.inmemory import InMemoryStorageClient

COB = date(2026, 8, 7)
CURVE_CODE = "AEQ.GHS.SOV.FWD"
ANALYST = "analyst@aequoros.com"
LEAD = "lead@aequoros.com"

# A gently upward Ghana sovereign bill/bond set (rates as decimal fractions).
GHS_QUOTES: list[dict[str, object]] = [
    {"instrument": "deposit", "tenor": tenor, "quote": quote}
    for tenor, quote in [
        ("3M", 0.245),
        ("6M", 0.249),
        ("1Y", 0.254),
        ("2Y", 0.258),
        ("3Y", 0.262),
    ]
]

STORAGE_FACTORY_TARGETS = (
    "app.adapters.market_data.pull_runner.get_storage_client",
    "app.adapters.market_data.cache.get_storage_client",
)


def _spec(**overrides: object) -> cc.CurveDefinitionSpec:
    base: dict[str, object] = {
        "curve_code": CURVE_CODE,
        "currency": "GHS",
        "calendar_name": "GHANA",
        "curve_kind": "forward",
        "projection_index": "GHS_SOVEREIGN",
        "discount_curve_code": None,
        "instrument_set_ref": "GHS - Sovereign bills (money-market proxy)",
        "interpolation_method": "monotone_convex",
        "output_daycount": "ACT_360",
        "payment_frequency": "quarterly",
        "payment_interval_months": 3,
        "curve_frequency": "3M",
        "spot_lag_days": 2,
        "roll_convention": "modified_following",
        "extrapolation_rule": "flat_forward",
        "params": {"grid_periods": 8, "pillar_min_count": 3},
    }
    base.update(overrides)
    return cc.CurveDefinitionSpec(**base)  # type: ignore[arg-type]


def _definition(**overrides: object) -> DeskCurveDefinition:
    """An in-memory approved definition for the pure construction tests."""
    spec = _spec(**overrides)
    return DeskCurveDefinition(
        curve_code=spec.curve_code,
        version=1,
        status="approved",
        currency=spec.currency,
        calendar_name=spec.calendar_name,
        curve_kind=spec.curve_kind,
        projection_index=spec.projection_index,
        discount_curve_code=spec.discount_curve_code,
        instrument_set_ref=spec.instrument_set_ref,
        interpolation_method=spec.interpolation_method,
        output_daycount=spec.output_daycount,
        payment_frequency=spec.payment_frequency,
        payment_interval_months=spec.payment_interval_months,
        curve_frequency=spec.curve_frequency,
        spot_lag_days=spec.spot_lag_days,
        roll_convention=spec.roll_convention,
        extrapolation_rule=spec.extrapolation_rule,
        params=spec.params,
        change_rationale="test",
        proposed_by=ANALYST,
        approved_by=LEAD,
    )


# ---------------------------------------------------------------------------
# Construction + QA
# ---------------------------------------------------------------------------


def test_build_ghana_sovereign_forward_grid_has_stub_and_forward_rows() -> None:
    result = cc.build_forward_curve(_definition(), COB, GHS_QUOTES, calendar=GHANA)

    # Spot stub (row 0) + 8 forward periods (grid_periods).
    assert len(result.grid.rows) == 9
    stub = result.grid.rows[0]
    assert stub.start == COB
    assert stub.discount_factor == 1.0
    assert stub.yield_ == 0.0

    # Each forward row chains, discounts below 1, and carries a positive yield.
    for previous, row in zip(result.grid.rows, result.grid.rows[1:], strict=False):
        assert row.start == previous.end
        assert 0.0 < row.discount_factor <= 1.0
        assert row.yield_ > 0.0


def test_construction_passes_hard_qa_gates_and_reprices_to_input() -> None:
    result = cc.build_forward_curve(_definition(), COB, GHS_QUOTES, calendar=GHANA)
    qa = result.qa

    assert qa.passed
    # Reprice acid test (spec §2.3): every instrument reprices to its input.
    assert qa.reprice_pass
    assert qa.reprice_max_residual < 1e-8
    assert all(abs(pillar.reprice_residual) < 1e-8 for pillar in result.pillars)
    # Forward positivity + oscillation gate (qa_forwards) and monotone DF.
    assert qa.forward_qa.positivity_pass
    assert qa.forward_qa.oscillation_pass
    assert qa.monotone_df_pass
    assert qa.pillar_coverage_pass
    assert qa.pillar_count == len(GHS_QUOTES)


def test_construction_is_deterministic_and_value_digested() -> None:
    first = cc.build_forward_curve(_definition(), COB, GHS_QUOTES, calendar=GHANA)
    second = cc.build_forward_curve(_definition(), COB, GHS_QUOTES, calendar=GHANA)

    assert first.input_digest == second.input_digest
    assert len(first.input_digest) == 64
    assert first.grid == second.grid  # frozen dataclass value equality

    # A changed quote changes the digest and the grid.
    bumped = [dict(quote) for quote in GHS_QUOTES]
    bumped[0]["quote"] = 0.246
    changed = cc.build_forward_curve(_definition(), COB, bumped, calendar=GHANA)
    assert changed.input_digest != first.input_digest

    # A changed definition version changes the digest even for identical quotes.
    v2_defn = _definition()
    v2_defn.version = 2
    v2 = cc.build_forward_curve(v2_defn, COB, GHS_QUOTES, calendar=GHANA)
    assert v2.input_digest != first.input_digest


def test_derived_values_conform_to_aequor_desk_forward_contract() -> None:
    result = cc.build_forward_curve(_definition(), COB, GHS_QUOTES, calendar=GHANA)
    derived = cc.to_determination_derived_values(result, CURVE_CODE)

    assert derived["curves_qa_passed"] is True
    spec = derived["curves"][CURVE_CODE]
    assert spec["curve_type"] == "forward"
    points = spec["points"]
    # One point per forward row (stub dropped); tenor_months step = curve frequency.
    assert len(points) == len(result.grid.rows) - 1
    assert points[0]["tenor_months"] == 3
    assert points[1]["tenor_months"] == 6
    # Rates are PERCENT (the adapter divides by 100 to the canonical decimal store).
    assert all(isinstance(point["rate_pct"], float) for point in points)
    assert points[0]["rate_pct"] == pytest.approx(result.grid.rows[1].yield_ * 100.0)
    stored = derived["forward_grids"][CURVE_CODE]
    assert set(stored["grids"]) == {"1D", "1M", "3M", "6M", "1Y"}
    assert len(stored["grids"]["1D"]["rows"]) == 23
    assert len(stored["grids"]["1Y"]["rows"]) == 31
    assert stored["rows"][0] == {
        "start": COB.isoformat(),
        "end": result.grid.rows[0].end.isoformat(),
        "discount_factor": "1.000000000000",
        "forward_yield": "0.000000000000",
    }


def test_unknown_instrument_and_calendar_raise_construction_error() -> None:
    with pytest.raises(cc.CurveConstructionError):
        cc.build_forward_curve(
            _definition(),
            COB,
            [{"instrument": "future", "tenor": "3M", "quote": 0.2}],
            calendar=GHANA,
        )
    with pytest.raises(cc.CurveConstructionError):
        cc.resolve_calendar("ATLANTIS")


# ---------------------------------------------------------------------------
# Definition governance (Track-2, mirrors the register)
# ---------------------------------------------------------------------------


def _create_definition(db: Session, **overrides: object) -> DeskCurveDefinition:
    return cc.create_definition(
        db,
        spec=_spec(**overrides),
        change_rationale="initial AEQ.GHS.SOV.FWD definition",
        proposed_by=ANALYST,
    )


def test_create_definition_seeds_v1_draft(db_session: Session) -> None:
    row = _create_definition(db_session)
    assert row.version == 1
    assert row.status == "draft"
    assert row.curve_code == CURVE_CODE
    # Duplicate code is refused (propose a version instead).
    with pytest.raises(HTTPException) as excinfo:
        _create_definition(db_session)
    assert excinfo.value.status_code == 409


def test_definition_approval_requires_distinct_approver(db_session: Session) -> None:
    _create_definition(db_session)
    with pytest.raises(HTTPException) as excinfo:
        cc.approve_definition_version(
            db_session,
            curve_code=CURVE_CODE,
            version=1,
            approved_by=ANALYST,  # proposer approving own draft
            effective_from=date(2026, 1, 1),
        )
    assert excinfo.value.status_code == 422
    assert cc.get_definition_version(db_session, CURVE_CODE, 1).status == "draft"


def test_approved_definition_is_immutable_and_effective_dated(db_session: Session) -> None:
    _create_definition(db_session)
    cc.approve_definition_version(
        db_session,
        curve_code=CURVE_CODE,
        version=1,
        approved_by=LEAD,
        effective_from=date(2026, 6, 1),
    )
    # Re-approval refused (history is never rewritten).
    with pytest.raises(HTTPException) as excinfo:
        cc.approve_definition_version(
            db_session,
            curve_code=CURVE_CODE,
            version=1,
            approved_by="other@aequoros.com",
            effective_from=date(2026, 7, 1),
        )
    assert excinfo.value.status_code == 409

    # A parameter change mints version+1 under a rationale.
    v2 = cc.propose_definition_version(
        db_session,
        spec=_spec(interpolation_method="pchip"),
        change_rationale="switch to PCHIP on thin days",
        proposed_by=ANALYST,
    )
    assert v2.version == 2
    cc.approve_definition_version(
        db_session,
        curve_code=CURVE_CODE,
        version=2,
        approved_by=LEAD,
        effective_from=date(2026, 9, 1),
    )

    # get_active resolves by effective date.
    active_july = cc.get_active_definition(db_session, CURVE_CODE, date(2026, 7, 15))
    assert active_july is not None and active_july.version == 1
    active_sept = cc.get_active_definition(db_session, CURVE_CODE, date(2026, 9, 15))
    assert active_sept is not None and active_sept.version == 2
    # Before any version is effective, nothing is active.
    assert cc.get_active_definition(db_session, CURVE_CODE, date(2026, 1, 1)) is None


def test_propose_requires_existing_code_and_non_draft_latest(db_session: Session) -> None:
    with pytest.raises(HTTPException) as excinfo:
        cc.propose_definition_version(
            db_session,
            spec=_spec(),
            change_rationale="x",
            proposed_by=ANALYST,
        )
    assert excinfo.value.status_code == 404  # code does not exist yet

    _create_definition(db_session)  # v1 draft still open
    with pytest.raises(HTTPException) as excinfo:
        cc.propose_definition_version(
            db_session,
            spec=_spec(),
            change_rationale="second concurrent draft",
            proposed_by=ANALYST,
        )
    assert excinfo.value.status_code == 409


# ---------------------------------------------------------------------------
# Publication (construct -> desk fan-out)
# ---------------------------------------------------------------------------


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> InMemoryStorageClient:
    client = InMemoryStorageClient()
    for target in STORAGE_FACTORY_TARGETS:
        monkeypatch.setattr(target, lambda: client)
    return client


def _make_bank(db: Session, org_id: str, name: str) -> Bank:
    bank = Bank(
        organization_id=org_id,
        name=name,
        short_name=name.lower().replace(" ", "-"),
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="universal_bank",
    )
    db.add(bank)
    db.commit()
    return bank


@pytest.fixture
def banks(db_session: Session) -> tuple[Bank, Bank]:
    return (
        _make_bank(db_session, ORG_1, "Curve Pub Bank One"),
        _make_bank(db_session, ORG_2, "Curve Pub Bank Two"),
    )


def _current_forward_curves(db: Session, bank: Bank) -> list[CanonicalYieldCurve]:
    return list(
        db.scalars(
            select(CanonicalYieldCurve).where(
                CanonicalYieldCurve.organization_id == bank.organization_id,
                CanonicalYieldCurve.bank_id == bank.id,
                CanonicalYieldCurve.as_of_date == COB,
                CanonicalYieldCurve.curve_name == CURVE_CODE,
                CanonicalYieldCurve.superseded_by.is_(None),
            )
        )
    )


def _approved_setup(db: Session) -> DeskCurveDefinition:
    register.ensure_default_methodology(db)
    register.approve_version(
        db,
        methodology_code=register.DEFAULT_METHODOLOGY_CODE,
        version=1,
        approved_by=LEAD,
        effective_from=date(2026, 1, 1),
    )
    _create_definition(db)
    definition = cc.approve_definition_version(
        db,
        curve_code=CURVE_CODE,
        version=1,
        approved_by=LEAD,
        effective_from=date(2026, 1, 1),
    )
    db.commit()
    return definition


def _stage(db: Session, definition: DeskCurveDefinition, **overrides: object) -> DeskDetermination:
    kwargs: dict[str, object] = {
        "definition": definition,
        "as_of": COB,
        "quotes": GHS_QUOTES,
        "calendar": GHANA,
        "prepared_by": ANALYST,
    }
    kwargs.update(overrides)
    return cc.stage_curve_determination(db, **kwargs)  # type: ignore[arg-type]


def test_curve_lifecycle_stage_submit_approve_publish(
    db_session: Session, banks: tuple[Bank, Bank], storage: InMemoryStorageClient
) -> None:
    """Happy path: draft -> submit -> approve (distinct signer) -> publish."""
    _ = storage
    definition = _approved_setup(db_session)

    # 1. Stage a DRAFT — never approved, never published.
    draft = _stage(db_session, definition)
    db_session.commit()
    assert draft.status == "draft"
    assert draft.prepared_by == ANALYST
    assert draft.reviewed_by is None
    assert draft.derived_values["curves_qa_passed"] is True
    # No canonical rows and no publication yet.
    assert _current_forward_curves(db_session, banks[0]) == []

    # 2. Maker submits.
    submitted = cc.submit_curve_determination(db_session, draft.id)
    assert submitted.status == "pending_review"

    # 3. Checker (distinct from preparer) approves — four-eyes satisfied.
    approved = cc.approve_curve_determination(db_session, draft.id, reviewed_by=LEAD)
    db_session.commit()
    assert approved.status == "approved"
    assert approved.reviewed_by == LEAD

    # 4. Publish fans out; only now do canonical rows land for every bank.
    publication_row = cc.publish_curve_determination(db_session, draft.id, actor=LEAD)
    assert publication_row.status == "complete"
    assert {entry["bank_id"] for entry in publication_row.results} == {
        banks[0].id,
        banks[1].id,
    }
    for bank in banks:
        curves = _current_forward_curves(db_session, bank)
        assert len(curves) == 1
        curve = curves[0]
        assert curve.source_system == "AEQUOR_DESK"
        assert curve.curve_type == "forward"
        assert curve.curve_name == CURVE_CODE
    assert db_session.get(DeskPublication, publication_row.id) is not None
    assert determinations.get(db_session, draft.id).status == "published"


def test_curve_four_eyes_blocks_self_approval(
    db_session: Session, banks: tuple[Bank, Bank], storage: InMemoryStorageClient
) -> None:
    """The preparer can never approve their own curve determination (422)."""
    _ = banks, storage
    definition = _approved_setup(db_session)
    draft = _stage(db_session, definition)
    cc.submit_curve_determination(db_session, draft.id)
    db_session.commit()
    with pytest.raises(HTTPException) as excinfo:
        cc.approve_curve_determination(db_session, draft.id, reviewed_by=ANALYST)
    assert excinfo.value.status_code == 422
    assert determinations.get(db_session, draft.id).status == "pending_review"


def test_curve_publish_blocked_before_approval(
    db_session: Session, banks: tuple[Bank, Bank], storage: InMemoryStorageClient
) -> None:
    """A draft or pending_review determination cannot be published (409)."""
    _ = banks, storage
    definition = _approved_setup(db_session)
    draft = _stage(db_session, definition)
    db_session.commit()
    with pytest.raises(HTTPException) as excinfo:
        cc.publish_curve_determination(db_session, draft.id, actor=LEAD)
    assert excinfo.value.status_code == 409

    cc.submit_curve_determination(db_session, draft.id)
    db_session.commit()
    with pytest.raises(HTTPException) as excinfo:
        cc.publish_curve_determination(db_session, draft.id, actor=LEAD)
    assert excinfo.value.status_code == 409


def test_curve_qa_hard_gate_blocks_submit(
    db_session: Session, banks: tuple[Bank, Bank], storage: InMemoryStorageClient
) -> None:
    """A QA-failing curve stages (so the analyst sees why) but cannot be submitted."""
    _ = banks, storage
    definition = _approved_setup(db_session)
    # A single pillar fails the pillar-coverage gate (pillar_min_count=3).
    thin_quotes = [{"instrument": "deposit", "tenor": "3M", "quote": 0.245}]
    draft = _stage(db_session, definition, quotes=thin_quotes)
    db_session.commit()
    assert draft.qa_results["passed"] is False
    assert draft.qa_results["pillar_coverage_pass"] is False
    with pytest.raises(HTTPException) as excinfo:
        cc.submit_curve_determination(db_session, draft.id)
    assert excinfo.value.status_code == 422


def test_stage_refuses_unapproved_definition(
    db_session: Session, banks: tuple[Bank, Bank], storage: InMemoryStorageClient
) -> None:
    _ = banks, storage
    register.ensure_default_methodology(db_session)
    register.approve_version(
        db_session,
        methodology_code=register.DEFAULT_METHODOLOGY_CODE,
        version=1,
        approved_by=LEAD,
        effective_from=date(2026, 1, 1),
    )
    draft_definition = _create_definition(db_session)  # never approved
    db_session.commit()
    with pytest.raises(HTTPException) as excinfo:
        _stage(db_session, draft_definition)
    assert excinfo.value.status_code == 409
    assert not determinations.list_determinations(db_session)


def test_staged_determination_digest_matches_construction(
    db_session: Session, banks: tuple[Bank, Bank], storage: InMemoryStorageClient
) -> None:
    """Staging does not perturb the value-based digest (reproducibility)."""
    _ = banks, storage
    definition = _approved_setup(db_session)
    expected = cc.build_forward_curve(definition, COB, GHS_QUOTES, calendar=GHANA)
    draft = _stage(db_session, definition)
    assert draft.input_digest == expected.input_digest


def test_definition_carries_entitlement_tier(db_session: Session) -> None:
    """FC-6d: the definition CRUD round-trips the distribution tier (default standard)."""
    default_row = _create_definition(db_session)
    assert default_row.entitlement_tier == "standard"

    premium = cc.create_definition(
        db_session,
        spec=_spec(curve_code="AEQ.GHS.CORP", entitlement_tier="premium"),
        change_rationale="premium credit curve",
        proposed_by=ANALYST,
    )
    assert premium.entitlement_tier == "premium"
