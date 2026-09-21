"""Sovereign exposures, derived from the sealed capital run's own lines.

Which lines count as sovereign is a governed decision, not a guess: the code
list ``sov_p2_exposure_categories`` names them, and with no approved list the
block reports that rather than picking lines by their wording.

The capital run states exposure and risk weight but not tenor or currency of
issue, so a derived grid cannot place a holding in the haircut grid's cells.
The block says so, and the method's own conservative fill applies the worst
cell — which is why a bank with real sovereign concentration should supply the
grid by hand instead. That trade-off is visible in the payload's notes rather
than buried in the number.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.icaap.blocks import SourceProbe
from app.services.icaap import resolvers
from app.services.icaap.resolvers import Resolution, ResolveContext, Unavailable
from app.services.regulatory_capital import OfficialCapitalPosition, official_capital_position

_VERSION = "1"
PARAM_CATEGORIES = "sov_p2_exposure_categories"
#: The haircut grid's key for "not stated" on either dimension.
UNKNOWN = "unknown"
_CREDIT_SECTION = "credit_rwa"


def _position(rc: ResolveContext) -> OfficialCapitalPosition:
    if rc.period is None:
        raise resolvers.no_period(rc)
    position = official_capital_position(rc.db, rc.access.ctx, rc.access.bank, rc.period)
    if position is None:
        raise Unavailable(
            f"No official capital run exists for {rc.cycle.as_of_date.isoformat()}, so "
            "the sovereign lines cannot be read."
        )
    return position


def _categories(rc: ResolveContext) -> tuple[str, ...]:
    from app.services.icaap import params  # noqa: PLC0415 - avoid an import cycle

    resolved = params.resolve_p2(
        rc.db, rc.access.bank, as_of=rc.cycle.as_of_date, codes=(PARAM_CATEGORIES,)
    )
    body = resolved.optional_body(PARAM_CATEGORIES)
    if body is None:
        raise Unavailable(
            "No approved list of sovereign exposure categories is configured, so the "
            "sovereign lines cannot be identified. Staff set it in the operator console."
        )
    codes = body.get("codes")
    if not isinstance(codes, list) or not codes:
        raise Unavailable(
            "The approved list of sovereign exposure categories is empty, so no lines "
            "can be identified as sovereign."
        )
    return tuple(str(code) for code in codes)


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _lines(position: OfficialCapitalPosition, categories: tuple[str, ...]):
    wanted = set(categories)
    return [line for line in position.sections.get(_CREDIT_SECTION, []) if line.line_code in wanted]


class SovereignExposuresResolver:
    block_type = "sovereign_exposures"
    version = _VERSION

    def probe(self, rc: ResolveContext) -> SourceProbe:
        if rc.period is None:
            return SourceProbe(current_key=None, reason="no reporting period for the as-of date")
        position = official_capital_position(rc.db, rc.access.ctx, rc.access.bank, rc.period)
        if position is None:
            return SourceProbe(current_key=None, reason="no official capital run")
        try:
            _categories(rc)
        except Unavailable as exc:
            return SourceProbe(current_key=None, reason=exc.reason)
        return SourceProbe(
            current_key=f"run:{position.run.id}",
            withdrawn=resolvers.run_withdrawn(rc.db, position.run),
        )

    def resolve(self, rc: ResolveContext) -> Resolution:
        position = _position(rc)
        categories = _categories(rc)
        spec = rc.spec
        lines = _lines(position, categories)
        table = resolvers.TableBuilder("holdings", "Sovereign exposures")
        table.column("key", "Line", "text")
        table.column("currency_kind", "Currency of issue", "text")
        table.column("tenor_bucket", "Tenor", "text")
        table.column("exposure", "Exposure", "amount")
        table.column("pillar1_rwa", "Risk-weighted", "amount")
        exposure_total = Decimal(0)
        rwa_total = Decimal(0)
        for line in lines:
            exposure = _decimal(line.exposure_amount) or Decimal(0)
            weighted = _decimal(line.weighted_amount) or Decimal(0)
            exposure_total += exposure
            rwa_total += weighted
            table.row(
                {
                    "key": line.line_code,
                    "currency_kind": UNKNOWN,
                    "tenor_bucket": UNKNOWN,
                    "exposure": exposure,
                    "pillar1_rwa": weighted,
                }
            )
        facts = {
            "sovereign_exposure_total": resolvers.fact(
                spec,
                "sovereign_exposure_total",
                exposure_total if lines else None,
                currency=rc.currency,
            ),
            "sovereign_rwa_total": resolvers.fact(
                spec, "sovereign_rwa_total", rwa_total if lines else None, currency=rc.currency
            ),
        }
        body = resolvers.payload(
            title=spec.title,
            as_of=rc.cycle.as_of_date,
            source_label=(f"Official capital run (baseline) · {rc.cycle.as_of_date.isoformat()}"),
            currency=rc.currency,
            tables=[table.build()],
            notes=[
                "Derived from the capital run's own lines, using the approved list of "
                "sovereign exposure categories.",
                "The run states neither currency of issue nor tenor, so the haircut "
                "applied is the most conservative cell of the governed grid.",
            ],
            raw={"categories": list(categories)},
        )
        return Resolution(
            source_kind="computed",
            source_ref={
                "run_id": str(position.run.id),
                "input_hash": position.run.input_hash,
                "categories": list(categories),
            },
            source_key=f"run:{position.run.id}",
            source_as_of=rc.cycle.as_of_date,
            payload=body,
            facts=facts,
            source_run_ids=(str(position.run.id),),
        )


__all__ = ["PARAM_CATEGORIES", "UNKNOWN", "SovereignExposuresResolver"]
