"""Who may open a regulator-requested correction, and who may answer the regulator.

What is being protected: both acts are decided ON THE RETURN the query is about,
exactly as its transmission is. The live defect this pins (2026-11-23): the
Validator who had transmitted CAR-RWA, and its preparer, were both refused with
"This action requires an active scoped binding", because the SUBMIT evaluation was
handed no maker/checker verdict. ``Permission.SUBMIT`` declares that condition
required, so an evaluation without one denies every binding, the Validator's
included. The authority kernel itself is covered elsewhere; here the two family
gates are stubbed so each case shows which gate decided and what it was handed.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.core.authorization import ConditionKind, Permission
from app.core.tenancy import TenantContext
from app.identity.public import Bank
from app.reporting.models.packages import RegulatoryPackage
from app.reporting.service import gateway_queries

ORG = "OR-TEST0001"
PREPARER = UUID("00000000-0000-0000-0000-00000000a001")
VALIDATOR = UUID("00000000-0000-0000-0000-00000000a002")
PACKAGE = RegulatoryPackage(
    id=uuid4(),
    organization_id=ORG,
    bank_id="BK-TEST0001",
    return_code="CAR-RWA",
    reporting_date=date(2026, 6, 30),
    generated_by=PREPARER,
)
BANK = Bank(id="BK-TEST0001", organization_id=ORG)


def _ctx(actor: UUID) -> TenantContext:
    return TenantContext(organization_id=ORG, actor_user_id=actor, authorization_version=1)


class _Gates:
    """The two family gates, answering as the officer's bindings would."""

    def __init__(self, *, validator: set[UUID], preparer: set[UUID]) -> None:
        self.validator = validator
        self.preparer = preparer
        self.submit_calls: list[dict[str, Any]] = []
        self.edit_calls: list[Permission] = []

    def transmission(self, _db: object, ctx: TenantContext, *args: Any, **kwargs: Any) -> None:
        self.submit_calls.append({"permission": args[2], **kwargs})
        conditions = kwargs.get("conditions", ())
        four_eyes = [c for c in conditions if c.kind is ConditionKind.MAKER_CHECKER]
        # The kernel's rule: SUBMIT without a maker/checker verdict denies.
        if ctx.actor_user_id not in self.validator or not four_eyes or not four_eyes[0].passed:
            raise HTTPException(status_code=403, detail="Validator authority required.")

    def edit(self, _db: object, ctx: TenantContext, *args: Any, **_kwargs: Any) -> None:
        self.edit_calls.append(args[2])
        if ctx.actor_user_id not in self.preparer:
            raise HTTPException(status_code=403, detail="Preparer authority required.")


@pytest.fixture
def gates(monkeypatch: pytest.MonkeyPatch) -> _Gates:
    stub = _Gates(validator={VALIDATOR}, preparer={PREPARER})
    monkeypatch.setattr(gateway_queries, "get_package_or_404", lambda *_a: PACKAGE)
    monkeypatch.setattr(
        gateway_queries.family_access, "require_transmission_authority", stub.transmission
    )
    monkeypatch.setattr(gateway_queries.family_access, "require_permission", stub.edit)
    return stub


def _decide(actor: UUID, *, preparer_may: bool) -> None:
    gateway_queries._require_on_return(  # pyright: ignore[reportPrivateUsage]
        None,  # pyright: ignore[reportArgumentType]
        _ctx(actor),
        BANK,
        PACKAGE.id,
        surface="regulator_query_correction",
        preparer_may=preparer_may,
    )


@pytest.mark.parametrize("preparer_may", [True, False])
def test_the_validator_may_correct_and_answer_with_the_four_eyes_verdict(
    gates: _Gates, preparer_may: bool
) -> None:
    _decide(VALIDATOR, preparer_may=preparer_may)

    (call,) = gates.submit_calls
    assert call["permission"] is Permission.SUBMIT
    (verdict,) = call["conditions"]
    assert verdict.kind is ConditionKind.MAKER_CHECKER
    assert verdict.passed
    assert gates.edit_calls == []


def test_the_preparer_may_open_the_correction_of_their_return(gates: _Gates) -> None:
    _decide(PREPARER, preparer_may=True)

    assert gates.edit_calls == [Permission.EDIT]


def test_the_preparer_may_not_answer_the_regulator(gates: _Gates) -> None:
    with pytest.raises(HTTPException) as refused:
        _decide(PREPARER, preparer_may=False)

    assert refused.value.status_code == 403
    detail = str(refused.value.detail)
    assert "generated this CAR-RWA return" in detail
    assert "Transmitting" not in detail
    assert gates.edit_calls == []


def test_an_officer_with_neither_authority_is_told_so(gates: _Gates) -> None:
    stranger = uuid4()
    with pytest.raises(HTTPException) as refused:
        _decide(stranger, preparer_may=True)
    assert refused.value.status_code == 403
    assert "holds neither" in str(refused.value.detail)

    with pytest.raises(HTTPException) as reply_refused:
        _decide(stranger, preparer_may=False)
    assert "is the Validator's act" in str(reply_refused.value.detail)


def test_the_officer_who_generated_the_return_cannot_answer_for_it() -> None:
    (verdict,) = gateway_queries._four_eyes(  # pyright: ignore[reportPrivateUsage]
        _ctx(PREPARER), PACKAGE
    )
    assert verdict.kind is ConditionKind.MAKER_CHECKER
    assert not verdict.passed


def test_a_return_the_caller_cannot_see_stays_hidden(
    gates: _Gates, monkeypatch: pytest.MonkeyPatch
) -> None:
    def hidden(*_args: object, **_kwargs: object) -> None:
        raise HTTPException(status_code=404, detail="Regulatory package not found.")

    monkeypatch.setattr(gateway_queries.family_access, "require_transmission_authority", hidden)
    with pytest.raises(HTTPException) as refused:
        _decide(PREPARER, preparer_may=True)
    assert refused.value.status_code == 404
    assert gates.edit_calls == []
