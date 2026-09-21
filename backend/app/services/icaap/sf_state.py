"""What the IRRBB Standardised Framework says about one ICAAP cycle.

Readiness, the freeze preflight and the Pillar 2 inputs all need the same three
answers — is the framework mandatory for this cycle's as-of date, is there a
sealed result for it, and did the last attempt refuse — and gathering them in
one place is what keeps the checklist a preparer works through and the list
that blocks a filing from drifting apart.

**Every read here is DISPATCH-plane, and records no provenance.** These
functions decide what to show and what to refuse; they seal no run. A governed
row resolved here must therefore never enter the session's consumption ledger,
because the next run sealed on that session would be credited with resolving it
and its ``content_digest`` — the value an officer signs — would change under a
readiness check. That is D-078, and the mandate seam takes ``record=False`` for
exactly this caller.

**The mandate is read from the framework, never from a constant.** A component
declares its own ``method_mandates`` (A6): which method becomes mandatory, the
governed code carrying the date, and which method it replaces. Ghana's ICAAP
framework carries one; Nigeria's and Kenya's carry none, so nothing is resolved
for them and a missing Ghanaian row cannot block a Nigerian cycle.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.domain.icaap.blocks import BlockStatus
from app.domain.icaap.frameworks.schema import Framework
from app.domain.icaap.pillar2 import irrbb_sf_method as irrbb_sf_domain
from app.domain.icaap.readiness import BlockState, IrrbbSfState
from app.models import Bank, BankReportingPeriod, RegulatoryRun
from app.models.icaap import IcaapCycle
from app.models.icaap_risk_capital import IcaapPillar2Item
from app.services import regulatory_irr_sf, regulatory_parameters
from app.services.icaap import sources
from app.services.icaap.resolvers import irrbb_sf as sf_resolver

BLOCK_TYPE = sf_resolver.BLOCK_TYPE

#: A block in one of these states is a figure the report may rely on. ``PINNED``
#: counts: a pin is a recorded judgement that the earlier figures are the right
#: ones, and it already carries its own warning.
_USABLE: frozenset[BlockStatus] = frozenset({BlockStatus.FRESH, BlockStatus.PINNED})


@dataclass(frozen=True)
class MandateDecision:
    """One framework-declared method mandate, resolved for a cycle."""

    method: str
    param_code: str
    replaces: str | None
    mandatory_from: date | None
    mandatory: bool
    confirmation_status: str
    component_keys: tuple[str, ...] = field(default_factory=tuple)


def _mandate_rows(
    db: Session, bank: Bank, codes: Sequence[str], *, today: date
) -> dict[str, tuple[date | None, str]]:
    """Resolve each commencement code at TODAY, without touching the ledger.

    The row is resolved at today rather than at the reporting date because it
    is a rule ABOUT reporting dates: back-dating the rule itself would let a
    console edit rewrite whether a filing that has already happened was ever
    required.
    """
    if not codes:
        return {}
    resolver = regulatory_parameters.PrefetchedParameterResolver.load(
        db, bank, as_of_dates=[today], record=False
    )
    out: dict[str, tuple[date | None, str]] = {}
    for code in sorted(set(codes)):
        row = resolver.try_resolve(code, as_of=today)
        if row is None:
            out[code] = (None, "")
            continue
        raw = (row.value_json or {}).get("date")
        try:
            out[code] = (date.fromisoformat(str(raw)), row.confirmation_status)
        except (TypeError, ValueError):
            out[code] = (None, row.confirmation_status)
    return out


def method_mandates(
    db: Session, bank: Bank, framework: Framework, *, as_of: date, today: date
) -> tuple[MandateDecision, ...]:
    """Every method mandate this framework declares, resolved for ``as_of``."""
    declared: dict[tuple[str, str, str | None], list[str]] = {}
    for category in framework.risk_categories:
        for component in category.components:
            for mandate in component.method_mandates:
                key = (mandate.method, mandate.mandatory_from_param, mandate.replaces)
                declared.setdefault(key, []).append(component.key)
    rows = _mandate_rows(
        db, bank, [param for _method, param, _replaces in declared], today=today
    )
    decisions: list[MandateDecision] = []
    for (method, param_code, replaces), components in sorted(declared.items()):
        mandatory_from, confirmation = rows.get(param_code, (None, ""))
        decisions.append(
            MandateDecision(
                method=method,
                param_code=param_code,
                replaces=replaces,
                mandatory_from=mandatory_from,
                mandatory=mandatory_from is not None and as_of >= mandatory_from,
                confirmation_status=confirmation,
                component_keys=tuple(sorted(components)),
            )
        )
    return tuple(decisions)


def refusal(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> tuple[str, str] | None:
    """The newest framework attempt's typed refusal for this exact as-of date."""
    period = sources.period_for(db, access, cycle.as_of_date)
    return None if period is None else _refusal_for(db, access, period)


def _refusal_for(
    db: Session, access: IcaapAccess, period: BankReportingPeriod
) -> tuple[str, str] | None:
    attempt = regulatory_irr_sf.latest_sf_attempt(db, access.ctx, access.bank, period)
    if attempt is None or attempt.status != "failed" or not attempt.error_code:
        return None
    return attempt.error_code, attempt.error_message or ""


def sealed_run(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> RegulatoryRun | None:
    period = sources.period_for(db, access, cycle.as_of_date)
    if period is None:
        return None
    return regulatory_irr_sf.latest_sf_run(db, access.ctx, access.bank, period)


def item_methods(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> Mapping[str, str]:
    """``component_key -> method`` for every live Pillar 2 item in the cycle."""
    return {
        item.component_key: item.method
        for item in db.scalars(
            select(IcaapPillar2Item).where(
                IcaapPillar2Item.organization_id == access.ctx.organization_id,
                IcaapPillar2Item.cycle_id == cycle.id,
                IcaapPillar2Item.retired_at.is_(None),
            )
        )
    }


def build(  # noqa: PLR0913 - the state is assembled from its named sources
    db: Session,
    access: IcaapAccess,
    cycle: IcaapCycle,
    framework: Framework,
    blocks: Sequence[BlockState],
    *,
    today: date,
) -> IrrbbSfState:
    """The framework's state for this cycle, for the readiness rules to read.

    The commencement row is resolved ONCE, by :func:`method_mandates`, and the
    mandate seam is not called again for the same code: two resolutions of one
    governed row on a read path is two chances for them to disagree, and this
    one decides whether a filing is refused.
    """
    decisions = method_mandates(
        db, access.bank, framework, as_of=cycle.as_of_date, today=today
    )
    decision = next(
        (entry for entry in decisions if entry.method == irrbb_sf_domain.METHOD), None
    )
    methods = item_methods(db, access, cycle)
    superseded = tuple(
        sorted(
            {
                methods[component]
                for entry in decisions
                if entry.mandatory and entry.replaces is not None
                for component in entry.component_keys
                if methods.get(component) == entry.replaces
            }
        )
    )
    block_usable = any(
        block.block_type == BLOCK_TYPE and not block.retired and block.status in _USABLE
        for block in blocks
    )
    period = sources.period_for(db, access, cycle.as_of_date)
    refused = None if period is None else _refusal_for(db, access, period)
    run = (
        None
        if period is None
        else regulatory_irr_sf.latest_sf_run(db, access.ctx, access.bank, period)
    )
    metrics = (run.metrics if run is not None else None) or {}
    tallies = metrics.get("assumption_tallies") or {}
    statement = (
        ""
        if decision is None
        else regulatory_irr_sf.SfMandate(
            mandatory=decision.mandatory,
            mandatory_from=decision.mandatory_from,
            as_of=cycle.as_of_date,
            confirmation_status=decision.confirmation_status,
            source_citation="",
        ).statement
    )
    return IrrbbSfState(
        mandatory=decision is not None and decision.mandatory,
        mandatory_from=None if decision is None else decision.mandatory_from,
        mandate_statement=statement,
        declared=decision is not None,
        run_present=run is not None,
        run_refusal_code=None if refused is None else refused[0],
        run_refusal_message=None if refused is None else refused[1],
        block_usable=block_usable,
        superseded_methods_in_use=superseded,
        assumption_defaults_applied=sum(int(count) for count in tallies.values()),
        representative_parameters=tuple(
            sorted(str(code) for code in metrics.get("representative_parameters") or [])
        ),
        outlier=bool(metrics.get("outlier", False)),
    )


__all__ = [
    "BLOCK_TYPE",
    "MandateDecision",
    "build",
    "item_methods",
    "method_mandates",
    "refusal",
    "sealed_run",
]
