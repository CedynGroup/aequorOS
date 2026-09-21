"""The full granularity adjustment, from a bank's canonical book to a figure.

The pure derivation and its P2 adapter were built first and are covered by
``tests/domain/credit`` and ``tests/domain/icaap/pillar2``. What is pinned here
is the half that makes them reachable: the exposure book this service assembles
from the canonical position snapshots, the order it resolves a PD and an ELGD
in, the rows it excludes and counts, and the refusals it returns instead of a
number it cannot honestly produce.

The end-to-end cases run through ``pillar2.compute_item`` — the same path the
dashboard uses — against the GOVERNED console rows as they are seeded, so the
gate they exercise is the real one. ``ga_min_effective_names = 50`` is
REPRESENTATIVE (D-059 F9) and decides whether the method engages at all, so both
sides of it are asserted: a book below it is declined with that number printed,
and the same book computes once the console lowers it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess, TenantContext
from app.domain.icaap.pillar2 import DEFERRED_METHODS
from app.domain.icaap.pillar2.granularity_method import (
    FULL_LABEL,
    METHOD_VARIANT,
    PARAM_COUNTERPARTY_SEGMENT_MAP,
    PARAM_DEFAULT_ELGD_PCT,
    PARAM_MIN_EFFECTIVE_NAMES,
    PARAM_PROXY_PD_BY_RW_CODE,
)
from app.domain.icaap.pillar2.types import MissingParameter
from app.models import (
    BankReportingPeriod,
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    IngestionBatch,
    LineageRecord,
    ParamEclAssumption,
    RegulatoryParameter,
    User,
)
from app.models.icaap import IcaapCycle
from app.models.icaap_risk_capital import IcaapPillar2Item
from app.schemas.icaap import IcaapCycleRead, IcaapDataBlockCreate
from app.schemas.icaap_risk_capital import (
    IcaapPillar2Approve,
    IcaapPillar2Compute,
    IcaapPillar2ItemCreate,
)
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.services import regulatory_capital
from app.services.icaap import blocks, pillar2
from app.services.icaap.pillar2_inputs import granularity as granularity_inputs
from tests.api.helpers import ORG_1
from tests.fixtures.canonical_bank_fixture import APPROVAL_TIMESTAMP, SAMPLE_BANK_ID
from tests.services.icaap.conftest import AS_OF

COMPONENT = "credit_concentration"
METHOD = "granularity_adjustment"

#: A generation later than every seeded row, so a console edit in a test wins.
CONSOLE_EDIT_FROM = date(2025, 7, 1)


# --- seeding a canonical book -----------------------------------------------


class _Book:
    """A minimal ingestion-traced canonical position book at the cycle's date."""

    def __init__(self, session: Session) -> None:
        self.session = session
        batch = IngestionBatch(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            source_system="EXCEL_CSV",
            adapter_version="1.0",
            extraction_mode="full",
            status="accepted",
            as_of_date=AS_OF,
        )
        session.add(batch)
        session.flush()
        lineage = LineageRecord(
            organization_id=ORG_1,
            ingestion_batch_id=batch.id,
            operation_type="ADAPTER_TRANSLATE",
            operation_ref="p5-granularity-fixture",
            input_lineage_ids=[],
        )
        session.add(lineage)
        session.flush()
        self.common: dict[str, Any] = {
            "organization_id": ORG_1,
            "bank_id": SAMPLE_BANK_ID,
            "as_of_date": AS_OF,
            "source_system": "EXCEL_CSV",
            "ingestion_batch_id": batch.id,
            "lineage_id": lineage.id,
            "validation_status": "accepted",
        }

    def counterparty(
        self, name: str, counterparty_type: str, *, group_reference: str | None = None
    ) -> CanonicalCounterparty:
        row = CanonicalCounterparty(
            **self.common,
            source_reference=f"CP/{name}",
            name=name,
            counterparty_type=counterparty_type,
            group_reference=group_reference,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def loan(  # noqa: PLR0913 - one keyword per canonical column under test
        self,
        reference: str,
        *,
        balance: str,
        currency: str = "GHS",
        counterparty: CanonicalCounterparty | None = None,
        stage: int | None = 1,
        attributes: dict[str, Any] | None = None,
        position_type: str = "LOAN",
    ) -> None:
        position = CanonicalPosition(
            **self.common,
            source_reference=reference,
            position_type=position_type,
            currency=currency,
        )
        self.session.add(position)
        self.session.flush()
        body: dict[str, Any] = {}
        if currency == "GHS":
            body["balance_ghs"] = balance
        body.update(attributes or {})
        self.session.add(
            CanonicalPositionSnapshot(
                **self.common,
                source_reference=reference,
                position_id=position.id,
                counterparty_id=None if counterparty is None else counterparty.id,
                balance=Decimal(balance),
                ifrs9_stage=stage,
                attributes=body,
            )
        )

    def commit(self) -> None:
        self.session.commit()


def _corporate_loans(  # noqa: PLR0913 - one keyword per exposure attribute
    book: _Book, *, count: int, balance: str, pd_pct: str, prefix: str, lgd_pct: str = "45"
) -> None:
    """``count`` separate corporate obligors, each with one facility."""
    for index in range(count):
        counterparty = book.counterparty(f"{prefix}-{index:03d}", "CORPORATE")
        book.loan(
            f"{prefix}/{index:03d}",
            balance=balance,
            counterparty=counterparty,
            attributes={"pd_pct": pd_pct, "lgd_pct": lgd_pct, "risk_weight_code": "RW100"},
        )


def _cycle_model(session: Session, cycle: IcaapCycleRead) -> IcaapCycle:
    model = session.get(IcaapCycle, cycle.id)
    assert model is not None
    return model


def _console_row(
    session: Session,
    code: str,
    *,
    value: str | None = None,
    value_json: dict[str, Any] | None = None,
) -> None:
    """What a console edit does: a NEWER approved generation, never a rewrite."""
    session.add(
        RegulatoryParameter(
            scope_type="institution_class",
            scope_key="bank",
            param_code=code,
            jurisdiction_code="GH",
            value_numeric=None if value is None else Decimal(value),
            value_json=value_json,
            unit="count",
            source_citation="REPRESENTATIVE: test — staff edited the console row.",
            confirmation_status="pending",
            effective_from=CONSOLE_EDIT_FROM,
            status="approved",
            proposed_by="test-suite",
            approved_by="test-suite",
        )
    )
    session.commit()


def _withdraw_row(session: Session, code: str) -> None:
    session.execute(delete(RegulatoryParameter).where(RegulatoryParameter.param_code == code))
    session.commit()


def _build(
    session: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> granularity_inputs.GaBook:
    return granularity_inputs.build_book(session, access, _cycle_model(session, cycle))


# --- the input precedence (P5-DESIGN §4.2) ----------------------------------


def test_an_exposures_own_pd_and_lgd_are_the_first_source(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    book = _Book(canonical_book)
    counterparty = book.counterparty("Volta Agro Ltd", "CORPORATE")
    book.loan(
        "LOAN/1",
        balance="1000000",
        counterparty=counterparty,
        attributes={"pd_pct": "2.5", "lgd_pct": "40"},
    )
    book.commit()

    result = _build(canonical_book, access, cycle)
    assert result.complete
    (exposure,) = result.exposures
    assert exposure.pd == Decimal("2.5") / Decimal(100)
    assert exposure.elgd == Decimal("40") / Decimal(100)
    assert exposure.pd_source == granularity_inputs.PD_SOURCE_EXPOSURE
    assert exposure.lgd_source == granularity_inputs.LGD_SOURCE_EXPOSURE
    assert exposure.group_key == "cp:Volta Agro Ltd"
    assert exposure.ead == Decimal("1000000")


def test_the_board_ecl_register_is_the_second_source(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    """Stage 1 only: a stage-2 row carries a LIFETIME PD, which this is not."""
    canonical_book.add_all(
        [
            ParamEclAssumption(
                organization_id=ORG_1,
                jurisdiction_code="GH",
                segment="ALL",
                stage=1,
                pd_pct=Decimal("3.25"),
                lgd_pct=Decimal("38"),
                effective_from=date(2025, 1, 1),
                approved_by="Board Credit Committee",
                approval_timestamp=APPROVAL_TIMESTAMP,
            ),
            ParamEclAssumption(
                organization_id=ORG_1,
                jurisdiction_code="GH",
                segment="ALL",
                stage=2,
                pd_pct=Decimal("40"),
                lgd_pct=Decimal("60"),
                effective_from=date(2025, 1, 1),
                approved_by="Board Credit Committee",
                approval_timestamp=APPROVAL_TIMESTAMP,
            ),
        ]
    )
    book = _Book(canonical_book)
    book.loan(
        "LOAN/1",
        balance="1000000",
        counterparty=book.counterparty("Volta Agro Ltd", "CORPORATE"),
    )
    book.commit()

    (exposure,) = _build(canonical_book, access, cycle).exposures
    assert exposure.pd == Decimal("3.25") / Decimal(100)
    assert exposure.elgd == Decimal("38") / Decimal(100)
    assert exposure.pd_source == granularity_inputs.PD_SOURCE_ECL_REGISTER
    assert exposure.lgd_source == granularity_inputs.LGD_SOURCE_ECL_REGISTER


def test_the_governed_proxy_table_is_the_last_source_for_a_pd(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    book = _Book(canonical_book)
    book.loan(
        "LOAN/1",
        balance="1000000",
        counterparty=book.counterparty("Volta Agro Ltd", "CORPORATE"),
        attributes={"risk_weight_code": "RW100"},
    )
    book.commit()

    result = _build(canonical_book, access, cycle)
    (exposure,) = result.exposures
    # The seeded RW100 proxy is 2%, and the ELGD falls to the governed default.
    assert exposure.pd == Decimal("2") / Decimal(100)
    assert exposure.pd_source == granularity_inputs.PD_SOURCE_PROXY
    assert exposure.lgd_source == granularity_inputs.LGD_SOURCE_GOVERNED_DEFAULT
    codes = {use.code for use in result.parameters_used}
    assert PARAM_PROXY_PD_BY_RW_CODE in codes
    assert PARAM_DEFAULT_ELGD_PCT in codes


def test_a_risk_weight_code_the_proxy_table_does_not_carry_is_never_substituted(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    """No nearest-code match: RW60 does not quietly become RW50."""
    book = _Book(canonical_book)
    book.loan(
        "LOAN/1",
        balance="1000000",
        counterparty=book.counterparty("Volta Agro Ltd", "CORPORATE"),
        attributes={"risk_weight_code": "RW60"},
    )
    book.commit()

    result = _build(canonical_book, access, cycle)
    assert result.exposures == ()
    assert result.refusals[0].startswith(granularity_inputs.REFUSAL_PD_UNRESOLVED)
    assert "LOAN/1" in result.refusals[0]


def test_one_unresolvable_pd_declines_the_whole_book(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    """A book where most names have a PD and the rest have a plug is not a
    granularity measurement — so the exposures are not handed on at all."""
    book = _Book(canonical_book)
    _corporate_loans(book, count=3, balance="1000000", pd_pct="2", prefix="GOOD")
    book.loan(
        "LOAN/NOPD",
        balance="1000000",
        counterparty=book.counterparty("No Estimate Ltd", "CORPORATE"),
    )
    book.commit()

    result = _build(canonical_book, access, cycle)
    assert result.exposures == ()
    assert not result.complete
    refusal = result.refusals[0]
    assert refusal.startswith(f"{granularity_inputs.REFUSAL_PD_UNRESOLVED}:1:")
    assert "LOAN/NOPD" in refusal


def test_a_counterparty_type_the_governed_map_does_not_carry_declines(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    """The canonical vocabulary is closed and the seeded map covers all of it,
    so this fires when a console edit narrows the map — or when a new
    counterparty type ships before the row that says how to correlate it. Either
    way the answer is a refusal naming the exposures, never a guessed segment."""
    book = _Book(canonical_book)
    book.loan(
        "LOAN/ODD",
        balance="1000000",
        counterparty=book.counterparty("Volta Agro Ltd", "CORPORATE"),
        attributes={"pd_pct": "2", "lgd_pct": "45"},
    )
    book.commit()
    _console_row(
        canonical_book,
        PARAM_COUNTERPARTY_SEGMENT_MAP,
        value_json={"schema": "ga-segment-map-v1", "RETAIL_INDIVIDUAL": "retail_other"},
    )

    result = _build(canonical_book, access, cycle)
    assert result.exposures == ()
    assert result.refusals[0].startswith(granularity_inputs.REFUSAL_SEGMENT_UNMAPPED)
    assert "LOAN/ODD" in result.refusals[0]


def test_a_stated_counterparty_type_of_none_takes_the_governed_unstated_row(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    book = _Book(canonical_book)
    book.loan("LOAN/1", balance="1000000", attributes={"pd_pct": "2", "lgd_pct": "45"})
    book.commit()

    (exposure,) = _build(canonical_book, access, cycle).exposures
    assert exposure.segment == "corporate"  # the seeded UNSTATED row
    assert exposure.group_key == "pos:LOAN/1"


# --- what the book excludes, and says it excluded ---------------------------


def test_every_excluded_row_is_counted_and_named(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    book = _Book(canonical_book)
    priced = {"pd_pct": "2", "lgd_pct": "45"}
    book.loan(
        "LOAN/KEEP",
        balance="1000000",
        counterparty=book.counterparty("Volta Agro Ltd", "CORPORATE"),
        attributes=priced,
    )
    book.loan(
        "LOAN/STAGE3",
        balance="1000000",
        counterparty=book.counterparty("Impaired Ltd", "CORPORATE"),
        stage=3,
        attributes=priced,
    )
    book.loan(
        "SEC/GOG",
        balance="1000000",
        counterparty=book.counterparty("Government of Ghana", "SOVEREIGN"),
        attributes=priced,
        position_type="SECURITY_HOLDING",
    )
    book.loan(
        "LOAN/RW0",
        balance="1000000",
        counterparty=book.counterparty("Zero Weighted Ltd", "CORPORATE"),
        attributes={**priced, "risk_weight_code": "RW0"},
    )
    book.loan(
        "LOAN/FX",
        balance="500000",
        currency="USD",
        counterparty=book.counterparty("Offshore Ltd", "CORPORATE"),
        attributes=priced,
    )
    book.loan(
        "LOAN/EMPTY",
        balance="0",
        counterparty=book.counterparty("Repaid Ltd", "CORPORATE"),
        attributes=priced,
    )
    book.commit()

    result = _build(canonical_book, access, cycle)
    assert [exposure.ref for exposure in result.exposures] == ["LOAN/KEEP"]
    assert result.excluded == {
        granularity_inputs.EXCLUDED_NON_POSITIVE_EAD: 1,
        granularity_inputs.EXCLUDED_SOVEREIGN_SEGMENT: 1,
        granularity_inputs.EXCLUDED_STAGE_3: 1,
        granularity_inputs.EXCLUDED_UNCONVERTED: 1,
        granularity_inputs.EXCLUDED_ZERO_RISK_WEIGHT: 1,
    }
    assert result.rows_read == 6


def test_two_facilities_to_one_connected_group_are_one_obligor(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    book = _Book(canonical_book)
    priced = {"pd_pct": "2", "lgd_pct": "45"}
    parent = book.counterparty("Volta Holdings", "CORPORATE", group_reference="GRP-1")
    child = book.counterparty("Volta Agro", "CORPORATE", group_reference="GRP-1")
    book.loan("LOAN/A", balance="1000000", counterparty=parent, attributes=priced)
    book.loan("LOAN/B", balance="2000000", counterparty=child, attributes=priced)
    book.commit()

    exposures = _build(canonical_book, access, cycle).exposures
    assert {exposure.group_key for exposure in exposures} == {"group:GRP-1"}


# --- governed rows: a refusal names the console row -------------------------


def test_a_missing_segment_map_is_missing_parameter_by_code(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    _withdraw_row(canonical_book, PARAM_COUNTERPARTY_SEGMENT_MAP)
    book = _Book(canonical_book)
    book.loan("LOAN/1", balance="1000000", attributes={"pd_pct": "2", "lgd_pct": "45"})
    book.commit()

    with pytest.raises(MissingParameter) as caught:
        _build(canonical_book, access, cycle)
    assert caught.value.param_code == PARAM_COUNTERPARTY_SEGMENT_MAP


def test_the_default_elgd_row_is_required_only_when_a_row_falls_to_it(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    _withdraw_row(canonical_book, PARAM_DEFAULT_ELGD_PCT)
    book = _Book(canonical_book)
    book.loan("LOAN/PRICED", balance="1000000", attributes={"pd_pct": "2", "lgd_pct": "45"})
    book.commit()
    # Every exposure states its own LGD, so the absent row is never read.
    assert _build(canonical_book, access, cycle).complete

    book.loan("LOAN/BARE", balance="1000000", attributes={"pd_pct": "2"})
    book.commit()
    with pytest.raises(MissingParameter) as caught:
        _build(canonical_book, access, cycle)
    assert caught.value.param_code == PARAM_DEFAULT_ELGD_PCT


def test_a_missing_proxy_table_is_only_needed_by_a_row_that_reaches_it(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    _withdraw_row(canonical_book, PARAM_PROXY_PD_BY_RW_CODE)
    book = _Book(canonical_book)
    book.loan("LOAN/PRICED", balance="1000000", attributes={"pd_pct": "2", "lgd_pct": "45"})
    book.commit()
    assert _build(canonical_book, access, cycle).complete

    book.loan("LOAN/BARE", balance="1000000", attributes={"risk_weight_code": "RW100"})
    book.commit()
    with pytest.raises(MissingParameter) as caught:
        _build(canonical_book, access, cycle)
    assert caught.value.param_code == PARAM_PROXY_PD_BY_RW_CODE


def test_the_maturity_switch_decides_whether_a_maturity_is_required(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    """Seeded off, a maturity is not read. Switched on, an absent one refuses."""
    book = _Book(canonical_book)
    book.loan("LOAN/NOM", balance="1000000", attributes={"pd_pct": "2", "lgd_pct": "45"})
    book.commit()
    assert _build(canonical_book, access, cycle).complete

    _console_row(
        canonical_book,
        "ga_maturity_adjustment",
        value_json={
            "schema": "ga-maturity-adjustment-v1",
            "apply": True,
            "b_intercept": "0.11852",
            "b_slope": "0.05478",
            "m_centre": "2.5",
            "m_scale": "1.5",
        },
    )
    result = _build(canonical_book, access, cycle)
    assert result.exposures == ()
    assert result.refusals[0].startswith(granularity_inputs.REFUSAL_MATURITY_UNRESOLVED)

    book.loan(
        "LOAN/WITHM",
        balance="1000000",
        attributes={"pd_pct": "2", "lgd_pct": "45", "maturity_years": "2.5"},
    )
    book.commit()
    stated = {
        exposure.ref: exposure.maturity_years
        for exposure in _build(canonical_book, access, cycle).exposures
    }
    # Still refused overall — LOAN/NOM has no maturity — but the stated one read.
    assert stated == {}
    canonical_book.execute(
        delete(CanonicalPositionSnapshot).where(
            CanonicalPositionSnapshot.source_reference == "LOAN/NOM"
        )
    )
    canonical_book.commit()
    (exposure,) = _build(canonical_book, access, cycle).exposures
    assert exposure.maturity_years == Decimal("2.5")


# --- end to end through the Pillar 2 register -------------------------------


@pytest.fixture
def pillar1_bound(canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead) -> None:
    """The Pillar 1 RWA block this cycle reads beside every concentration figure.

    The add-on itself is an absolute amount and needs no denominator, but the
    register prints it against the Pillar 1 charge it corrects, so the block has
    to be bound before any concentration method computes.
    """
    period = canonical_book.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.bank_id == access.bank.id,
            BankReportingPeriod.period_end == AS_OF,
        )
    )
    assert period is not None
    regulatory_capital.create_capital_run(
        canonical_book,
        access.ctx,
        access.bank.id,
        RegulatoryRunCreate(
            module="capital", reporting_period_id=period.id, scenario_code="baseline"
        ),
    )
    canonical_book.commit()
    blocks.create_block(
        canonical_book, access, cycle.id, IcaapDataBlockCreate(block_type="pillar1_rwa")
    )


def _create_item(session: Session, access: IcaapAccess, cycle: IcaapCycleRead) -> UUID:
    item = pillar2.create_item(
        session,
        access,
        cycle.id,
        IcaapPillar2ItemCreate(
            component_key=COMPONENT,
            method=METHOD,
            input_mode="bound_blocks",
            source="icaap_method",
            rationale="Name concentration measured by the full granularity adjustment.",
            reason="P5 granularity adjustment.",
        ),
    )
    return item.id


def _computation(read: Any) -> dict[str, Any]:
    """The stored computation of a read, which is present after a compute."""
    body = read.computation
    assert isinstance(body, dict)
    return body


def _compute(session: Session, access: IcaapAccess, cycle: IcaapCycleRead, item_id: UUID):
    item = session.get(IcaapPillar2Item, item_id)
    assert item is not None
    return pillar2.compute_item(
        session,
        access,
        cycle.id,
        item_id,
        IcaapPillar2Compute(
            base_revision_no=item.current_revision_no,
            reason="Compute the granularity adjustment.",
        ),
    )


def test_the_method_is_no_longer_answered_as_not_built() -> None:
    assert METHOD not in DEFERRED_METHODS


@pytest.mark.usefixtures("pillar1_bound")
def test_a_book_below_the_governed_floor_is_declined_with_the_number_that_decided(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    book = _Book(canonical_book)
    _corporate_loans(book, count=5, balance="1000000", pd_pct="2", prefix="SMALL")
    book.commit()

    item_id = _create_item(canonical_book, access, cycle)
    read = _compute(canonical_book, access, cycle, item_id)

    assert read.method_status == "not_computable"
    assert read.baseline_amount is None
    assert "ga_below_min_effective_names" in _computation(read)["reasons"]
    detail = _computation(read)["detail"]
    # F9: the floor that decided is printed beside the refusal, and the label
    # that says which adjustment this is travels with it.
    assert detail["min_effective_names"] == "50.000000"
    assert detail["n_eff"] == "5.000000"
    assert detail["label"] == FULL_LABEL
    assert detail["method_variant"] == METHOD_VARIANT
    assert PARAM_MIN_EFFECTIVE_NAMES in {
        row["param_code"] for row in _computation(read)["parameters_used"]
    }


@pytest.mark.usefixtures("pillar1_bound")
def test_a_granular_book_reaches_the_method_and_produces_a_figure(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    """The design's primary service-level golden (P5-DESIGN §4.6, last row).

    20 names of 50 at PD 1% plus 100 names of 10 at PD 3%, ELGD 45%, corporate
    correlation, no maturity adjustment: N_eff = 66.67, which clears the seeded
    floor of 50, GA = 0.016372 of a 2,000 book and the add-on is 32.744634.
    """
    book = _Book(canonical_book)
    _corporate_loans(book, count=20, balance="50", pd_pct="1", prefix="BIG")
    _corporate_loans(book, count=100, balance="10", pd_pct="3", prefix="SMALL")
    book.commit()

    item_id = _create_item(canonical_book, access, cycle)
    read = _compute(canonical_book, access, cycle, item_id)

    assert read.method_status == "computed"
    # The stored amount carries the engine's money quantum; the diagnostics
    # carry the figure the formula produced, so both seams are pinned.
    assert read.baseline_amount == Decimal("32.7446")
    assert read.basis == "absolute"
    detail = _computation(read)["detail"]
    assert detail["addon_amount"] == "32.744634"
    assert detail["ga"] == "0.0163723170"  # 0.016372 at the design's six places
    assert detail["k_star"] == "0.0732515932"  # 0.073252 at six places
    assert detail["n_eff"] == "66.666667"
    assert detail["total_ead"] == "2000.000000"
    assert detail["n_obligors"] == "120"
    assert detail["label"] == FULL_LABEL
    assert detail["method_variant"] == METHOD_VARIANT
    assert detail["inputs_digest"]
    # The stressed side is an absolute amount, so it is declared, not inferred.
    assert read.stressed_amount is None
    assert "stressed_book_not_assessed" in _computation(read)["reasons"]


@pytest.mark.usefixtures("pillar1_bound")
def test_lowering_the_governed_floor_in_the_console_lets_the_same_book_compute(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    """F9 again, from the other side: the floor is an editable console row."""
    book = _Book(canonical_book)
    _corporate_loans(book, count=5, balance="1000000", pd_pct="2", prefix="SMALL")
    book.commit()

    item_id = _create_item(canonical_book, access, cycle)
    assert _compute(canonical_book, access, cycle, item_id).method_status == "not_computable"

    _console_row(canonical_book, PARAM_MIN_EFFECTIVE_NAMES, value="4")
    read = _compute(canonical_book, access, cycle, item_id)
    assert read.method_status == "computed"
    assert read.baseline_amount is not None
    assert read.baseline_amount > Decimal(0)
    assert _computation(read)["detail"]["min_effective_names"] == "4.000000"


@pytest.mark.usefixtures("pillar1_bound")
def test_a_book_the_service_cannot_assemble_never_becomes_a_smaller_figure(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    """The granular book computes; add ONE priceless obligor and it declines."""
    book = _Book(canonical_book)
    _corporate_loans(book, count=20, balance="50", pd_pct="1", prefix="BIG")
    _corporate_loans(book, count=100, balance="10", pd_pct="3", prefix="SMALL")
    book.loan(
        "LOAN/NOPD",
        balance="10",
        counterparty=book.counterparty("No Estimate Ltd", "CORPORATE"),
    )
    book.commit()

    item_id = _create_item(canonical_book, access, cycle)
    read = _compute(canonical_book, access, cycle, item_id)
    assert read.method_status == "not_computable"
    assert read.baseline_amount is None
    assert any(
        reason.startswith(granularity_inputs.REFUSAL_PD_UNRESOLVED)
        for reason in _computation(read)["reasons"]
    )
    assert _computation(read)["detail"]["label"] == FULL_LABEL


@pytest.mark.usefixtures("pillar1_bound")
@pytest.mark.usefixtures("pillar1_bound")
def test_a_cycle_that_enters_its_figures_by_hand_is_told_it_cannot_use_this_method(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    """The adjustment needs an obligor book, which nobody can type in."""
    item = pillar2.create_item(
        canonical_book,
        access,
        cycle.id,
        IcaapPillar2ItemCreate(
            component_key=COMPONENT,
            method=METHOD,
            input_mode="manual_with_evidence",
            source="icaap_method",
            rationale="Entered by hand.",
            reason="P5 granularity adjustment, manual.",
        ),
    )
    read = _compute(canonical_book, access, cycle, item.id)
    assert read.method_status == "not_computable"
    assert granularity_inputs.REFUSAL_BOOK_NOT_SUPPLIED in _computation(read)["reasons"]
    assert _computation(read)["detail"]["label"] == FULL_LABEL


@pytest.mark.usefixtures("pillar1_bound")
def test_a_missing_governed_row_refuses_by_code_through_the_register(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    _withdraw_row(canonical_book, PARAM_COUNTERPARTY_SEGMENT_MAP)
    book = _Book(canonical_book)
    _corporate_loans(book, count=5, balance="1000000", pd_pct="2", prefix="SMALL")
    book.commit()

    item_id = _create_item(canonical_book, access, cycle)
    with pytest.raises(HTTPException) as caught:
        _compute(canonical_book, access, cycle, item_id)
    refusal: Any = caught.value.detail
    assert isinstance(refusal, dict)
    assert refusal["error_code"] == "missing_parameter"
    assert refusal["param_code"] == PARAM_COUNTERPARTY_SEGMENT_MAP


@pytest.mark.usefixtures("pillar1_bound")
@pytest.mark.usefixtures("pillar1_bound")
def test_the_register_shows_the_figure_and_a_second_person_can_approve_it(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    """The surface a bank actually uses: the method is offered, the figure lands
    on the register with its representative calibration disclosed, and
    maker-checker approval works on it like any other Pillar 2 figure."""
    book = _Book(canonical_book)
    _corporate_loans(book, count=20, balance="50", pd_pct="1", prefix="BIG")
    _corporate_loans(book, count=100, balance="10", pd_pct="3", prefix="SMALL")
    book.commit()

    register = pillar2.get_register(canonical_book, access, cycle.id)
    slot = next(row for row in register.components if row.component_key == COMPONENT)
    assert METHOD in slot.allowed_methods
    assert METHOD not in slot.deferred_methods

    item_id = _create_item(canonical_book, access, cycle)
    computed = _compute(canonical_book, access, cycle, item_id)
    assert computed.method_status == "computed"
    # D-039: the floor and the proxy calibration are REPRESENTATIVE and pending,
    # and the register says so rather than printing a bare number.
    assert PARAM_MIN_EFFECTIVE_NAMES in computed.representative_parameters
    assert PARAM_MIN_EFFECTIVE_NAMES in computed.pending_parameters
    assert computed.method_label == "Granularity adjustment on the single-risk-factor model"
    assert computed.approvable is True

    checker_id = uuid4()
    canonical_book.add(
        User(
            id=checker_id,
            organization_id=ORG_1,
            email="p5-checker@example.test",
            display_name="Second Person",
        )
    )
    canonical_book.commit()
    checker = IcaapAccess(
        ctx=TenantContext(organization_id=ORG_1, actor_user_id=checker_id, authorization_version=1),
        bank=access.bank,
    )
    approved = pillar2.approve_item(
        canonical_book,
        checker,
        cycle.id,
        item_id,
        IcaapPillar2Approve(revision_no=computed.current_revision_no, note="Reviewed."),
    )
    assert approved.approval_current is True
    assert approved.baseline_amount == Decimal("32.7446")

    listed = next(
        row
        for row in pillar2.get_register(canonical_book, access, cycle.id).items
        if row.item_key == COMPONENT
    )
    assert listed.method == METHOD
    assert listed.baseline_amount == Decimal("32.7446")
    assert listed.stale is False


@pytest.mark.usefixtures("pillar1_bound")
def test_the_book_rides_the_items_value_based_digest(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    """A book that changes changes the digest — and a rerun of the same book
    does not, because the digest is over values rather than snapshot ids."""
    book = _Book(canonical_book)
    _corporate_loans(book, count=20, balance="50", pd_pct="1", prefix="BIG")
    _corporate_loans(book, count=100, balance="10", pd_pct="3", prefix="SMALL")
    book.commit()

    item_id = _create_item(canonical_book, access, cycle)
    first = _compute(canonical_book, access, cycle, item_id).inputs_digest
    assert first == _compute(canonical_book, access, cycle, item_id).inputs_digest

    snapshot = canonical_book.scalar(
        select(CanonicalPositionSnapshot).where(
            CanonicalPositionSnapshot.source_reference == "BIG/000"
        )
    )
    assert snapshot is not None
    snapshot.attributes = {**snapshot.attributes, "pd_pct": "1.5"}
    canonical_book.commit()
    assert _compute(canonical_book, access, cycle, item_id).inputs_digest != first


# ---------------------------------------------------------------------------
# What the product SAYS about a bank under the floor (GAP-4 item 4)
# ---------------------------------------------------------------------------
#
# The design's §4.5 aggregation rule — take the higher of this adjustment and a
# benchmark charge — is not built, and no benchmark calibration was invented to
# build it. What the product owes a preparer is therefore the truth: the method
# declines, nothing is substituted, the component stays unquantified until an
# officer chooses another method, and the floor that decided is a
# REPRESENTATIVE platform value. Before this, the screen read
# "This figure cannot be worked out yet: ga below min effective names. Link the
# missing figures to this ICAAP, then compute it again." — a raw enum and an
# instruction that would not have helped, because nothing was unlinked.


@pytest.mark.usefixtures("pillar1_bound")
def test_a_book_under_the_floor_is_told_what_happens_to_it(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    book = _Book(canonical_book)
    _corporate_loans(book, count=5, balance="1000000", pd_pct="2", prefix="SMALL")
    book.commit()

    item_id = _create_item(canonical_book, access, cycle)
    read = _compute(canonical_book, access, cycle, item_id)
    detail = read.status_detail or ""

    # No raw machine reason, and none of the wrong advice.
    assert "ga_below_min_effective_names" not in detail
    assert "ga below min effective names" not in detail
    assert "Link the missing figures" not in detail
    # The number that decided, on both sides of the gate.
    assert "5.000000 effective names" in detail
    assert "a floor of 50.000000" in detail
    # D-024: the floor is labelled as what it is, everywhere it surfaces.
    assert "REPRESENTATIVE AequorOS calibration" in detail
    assert "pending confirmation with the supervisor" in detail
    # What actually happens next — including the absence of an arithmetic floor.
    assert "no benchmark charge is applied as an arithmetic floor" in detail
    assert "stays unquantified until an officer chooses another method" in detail
    # The part that reads badly and is said anyway.
    assert "carries MORE name concentration risk, not less" in detail


@pytest.mark.usefixtures("pillar1_bound")
def test_an_unpriceable_obligor_is_reported_as_a_refusal_not_as_a_token(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    """D-070 rule 1, in the words a preparer reads."""
    book = _Book(canonical_book)
    _corporate_loans(book, count=20, balance="50", pd_pct="1", prefix="BIG")
    _corporate_loans(book, count=100, balance="10", pd_pct="3", prefix="SMALL")
    book.loan(
        "LOAN/NOPD",
        balance="10",
        counterparty=book.counterparty("No Estimate Ltd", "CORPORATE"),
    )
    book.commit()

    item_id = _create_item(canonical_book, access, cycle)
    detail = _compute(canonical_book, access, cycle, item_id).status_detail or ""

    assert granularity_inputs.REFUSAL_PD_UNRESOLVED not in detail
    # One row reads as one row: "1 exposures carry …" is the sentence that tells
    # a preparer the screen was assembled rather than written.
    assert "one exposure carries no probability of default" in detail
    assert "1 exposures" not in detail
    assert "smaller figure that looks exactly like a correct one" in detail
    assert "First references: LOAN/NOPD." in detail


@pytest.mark.usefixtures("pillar1_bound")
def test_a_manual_item_is_told_why_this_method_cannot_take_a_typed_amount(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    item = pillar2.create_item(
        canonical_book,
        access,
        cycle.id,
        IcaapPillar2ItemCreate(
            component_key=COMPONENT,
            method=METHOD,
            input_mode="manual_with_evidence",
            source="icaap_method",
            rationale="Entered by hand.",
            reason="P5 granularity adjustment, manual.",
        ),
    )
    detail = _compute(canonical_book, access, cycle, item.id).status_detail or ""

    assert granularity_inputs.REFUSAL_BOOK_NOT_SUPPLIED not in detail
    assert "needs the institution's own obligor book" in detail
