"""The rule that decides whether a sealed run's inputs still stand (D-12).

The audit finding is not hypothetical. A governed withdrawal was applied to the
primary database on 2026-08-22: ``DB_DIRECT`` positions at 2026-06-30 for
``BK-0PMD7Z5M``, 150,314 snapshots, approved by a second officer. 145 sealed,
succeeded ``RegulatoryRun`` rows for that period had already been computed over
those rows and carried nothing to say so.

Retaining those runs is correct — they are immutable evidence of what the book
said. Letting them present as CURRENT is not. These tests pin the rule that
tells the two apart, clause by clause, with no database and no framework: every
clause mirrors a real predicate in ``app/services/fact_derivation.py``, and
every unknown resolves toward *affected*.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.domain.authority.evidence import (
    EVIDENCE_METRIC_ID,
    EvidenceStatus,
    WithdrawalRecord,
    assess_run_evidence,
)
from app.domain.authority.outcomes import OutcomeState

ORG = "OR-QVXE0FQV"
BANK = "BK-0PMD7Z5M"
AS_OF = date(2026, 6, 30)

#: The real shape of the 2026-08-22 act: rows ingested well before the runs,
#: retired well after them.
INGESTED = datetime(2026, 7, 15, 22, 0, tzinfo=UTC)
SEALED = datetime(2026, 8, 12, 6, 6, 9, tzinfo=UTC)
APPROVED = datetime(2026, 8, 22, 16, 10, 41, tzinfo=UTC)


def _withdrawal(**overrides: object) -> WithdrawalRecord:
    defaults: dict[str, object] = {
        "withdrawal_id": "01a029c1-e120-71f0-bf35-3e55d8cb47f3",
        "organization_id": ORG,
        "bank_id": BANK,
        "entity": "position",
        "source_system": "DB_DIRECT",
        "as_of_date": AS_OF,
        "status": "applied",
        "approved_at": APPROVED,
        "first_ingested_at": INGESTED,
        "position_type": None,
        "rows_withdrawn": 150_314,
        "reason": "Duplicate source book.",
    }
    defaults.update(overrides)
    return WithdrawalRecord(**defaults)  # type: ignore[arg-type]


def _assess(*records: WithdrawalRecord, sealed_at: datetime | None = SEALED, as_of: object = AS_OF):
    return assess_run_evidence(
        run_id="run-1",
        organization_id=ORG,
        bank_id=BANK,
        as_of_date=as_of,  # type: ignore[arg-type]
        sealed_at=sealed_at,
        withdrawals=records,
    )


# ---------------------------------------------------------------------------
# The live case
# ---------------------------------------------------------------------------


def test_the_2026_08_22_withdrawal_orphans_a_run_sealed_before_it() -> None:
    """The measured production case: sealed 2026-08-12, withdrawn 2026-08-22."""
    evidence = _assess(_withdrawal())
    assert evidence.status is EvidenceStatus.INPUTS_WITHDRAWN
    assert evidence.is_current is False
    assert evidence.blocks_filing is True
    assert evidence.rows_withdrawn == 150_314
    assert len(evidence.impacts) == 1


def test_no_withdrawal_leaves_the_run_current() -> None:
    evidence = _assess()
    assert evidence.status is EvidenceStatus.CURRENT
    assert evidence.blocks_filing is False
    assert evidence.reason() is None
    assert evidence.as_outcome() is None


def test_a_run_sealed_after_the_withdrawal_is_current() -> None:
    """Re-running is the remedy: the new run excluded the withdrawn rows."""
    after = APPROVED.replace(hour=18)
    assert _assess(_withdrawal(), sealed_at=after).status is EvidenceStatus.CURRENT


def test_a_run_sealed_before_the_rows_ever_existed_is_current() -> None:
    """Without ``first_ingested_at`` every historical run would be over-refused."""
    before = INGESTED.replace(day=1)
    assert _assess(_withdrawal(), sealed_at=before).status is EvidenceStatus.CURRENT


# ---------------------------------------------------------------------------
# Withdrawal lifecycle — why the answer is derived and never stored
# ---------------------------------------------------------------------------


def test_a_pending_withdrawal_stamps_nothing_and_orphans_nothing() -> None:
    assert _assess(_withdrawal(status="pending", approved_at=None)).is_current


def test_a_reversed_withdrawal_makes_the_run_current_again_with_no_write() -> None:
    """The whole argument for deriving rather than storing.

    A stored flag would have to be un-stamped on reversal — a SECOND write to
    sealed evidence, and a permanently mis-marked run if it were ever missed.
    Derivation simply stops matching.
    """
    assert _assess(_withdrawal(status="reversed")).is_current


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def test_another_tenants_withdrawal_is_ignored() -> None:
    assert _assess(_withdrawal(organization_id="OR-OTHER00")).is_current


def test_another_banks_withdrawal_is_ignored() -> None:
    assert _assess(_withdrawal(bank_id="BK-OTHER00")).is_current


def test_a_position_withdrawal_binds_to_the_exact_business_date() -> None:
    """``_load_position_rows`` filters ``as_of_date == as_of``, so ``<=`` would
    refuse filings that are genuinely unaffected."""
    assert _assess(_withdrawal(as_of_date=date(2026, 5, 31))).is_current
    assert _assess(_withdrawal(as_of_date=date(2026, 7, 31))).is_current


def test_a_gl_account_withdrawal_reaches_forward_because_the_chart_carries_forward() -> None:
    """``_load_canonical`` filters ``CanonicalGlAccount.as_of_date <= as_of``."""
    earlier = _withdrawal(entity="gl_account", as_of_date=date(2026, 5, 31))
    assert _assess(earlier).status is EvidenceStatus.INPUTS_WITHDRAWN
    later = _withdrawal(entity="gl_account", as_of_date=date(2026, 7, 31))
    assert _assess(later).is_current


def test_counterparty_and_product_take_the_conservative_reading() -> None:
    for entity in ("counterparty", "product"):
        record = _withdrawal(entity=entity, as_of_date=date(2026, 5, 31))
        assert _assess(record).status is EvidenceStatus.INPUTS_WITHDRAWN, entity


# ---------------------------------------------------------------------------
# Fail-closed on every unknown
# ---------------------------------------------------------------------------


def test_a_run_with_no_timestamp_is_treated_as_affected() -> None:
    assert _assess(_withdrawal(), sealed_at=None).status is EvidenceStatus.INPUTS_WITHDRAWN


def test_a_withdrawal_with_no_approval_time_is_treated_as_affecting() -> None:
    assert _assess(_withdrawal(approved_at=None)).status is EvidenceStatus.INPUTS_WITHDRAWN


def test_an_unmeasurable_first_ingestion_is_treated_as_affecting() -> None:
    assert _assess(_withdrawal(first_ingested_at=None)).status is EvidenceStatus.INPUTS_WITHDRAWN


def test_an_unreadable_business_date_is_treated_as_affected() -> None:
    """A run whose as-of cannot be read cannot be certified clean."""
    assert _assess(_withdrawal(), as_of="not-a-date").status is EvidenceStatus.INPUTS_WITHDRAWN
    assert _assess(_withdrawal(), as_of=None).status is EvidenceStatus.INPUTS_WITHDRAWN


def test_the_iso_string_the_snapshot_carries_is_accepted() -> None:
    """``inputs['as_of_date']`` is JSON, so it arrives as a string."""
    assert _assess(_withdrawal(), as_of="2026-06-30").status is EvidenceStatus.INPUTS_WITHDRAWN
    assert _assess(_withdrawal(), as_of="2026-05-31").is_current


# ---------------------------------------------------------------------------
# What the refusal says
# ---------------------------------------------------------------------------


def test_the_refusal_is_a_declared_outcome_state_naming_every_withdrawal() -> None:
    detail = _assess(_withdrawal()).as_outcome()
    assert detail is not None
    assert detail.state is OutcomeState.DATA_QUALITY_BLOCK
    assert detail.metric_id == EVIDENCE_METRIC_ID
    assert detail.blocks_filing is True
    assert detail.items == (
        "withdrawal:01a029c1-e120-71f0-bf35-3e55d8cb47f3:DB_DIRECT/position@2026-06-30",
    )
    assert detail.context["run_id"] == "run-1"
    assert detail.context["rows_withdrawn"] == 150_314


def test_the_reason_says_the_run_is_retained_and_must_not_be_filed() -> None:
    reason = _assess(_withdrawal()).reason()
    assert reason is not None
    assert "retained" in reason
    assert "must not be filed" in reason
    assert "150,314 rows" in reason
    assert "DB_DIRECT" in reason
    assert "2026-06-30" in reason


def test_several_withdrawals_are_all_reported_in_a_stable_order() -> None:
    evidence = _assess(
        _withdrawal(withdrawal_id="w-2", source_system="EXCEL_CSV", rows_withdrawn=5),
        _withdrawal(withdrawal_id="w-1", source_system="API_PUSH", rows_withdrawn=7),
    )
    assert [impact.source_system for impact in evidence.impacts] == ["API_PUSH", "EXCEL_CSV"]
    assert evidence.rows_withdrawn == 12


def test_the_wire_shape_is_json_ready() -> None:
    payload = _assess(_withdrawal()).to_dict()
    assert payload["status"] == "inputs_withdrawn"
    assert payload["is_current"] is False
    assert payload["blocks_filing"] is True
    assert payload["withdrawals"][0]["as_of_date"] == "2026-06-30"
    assert payload["withdrawals"][0]["approved_at"] == APPROVED.isoformat()
