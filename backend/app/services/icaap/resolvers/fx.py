"""The net open foreign-exchange position, from the sealed baseline FX run.

The per-currency table is republished under NEUTRAL keys — ``net_reporting``
rather than a cedi-specific one — because the same block serves a Nigerian or
Kenyan bank and a column called ``net_ghs`` in a Naira report is wrong in a way
that is hard to see and easy to file.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.icaap.blocks import SourceProbe
from app.models import RegulatoryRun
from app.services.icaap import resolvers, sources
from app.services.icaap.resolvers import Resolution, ResolveContext, Unavailable

_VERSION = "1"
_MODULE = "fx"
_BASELINE = "baseline"


def _run(rc: ResolveContext) -> RegulatoryRun | None:
    if rc.period is None:
        return None
    return sources.latest_succeeded_run(rc.db, rc.access, rc.period, _MODULE, _BASELINE)


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


_POSITION_KEYS = (
    ("currency", ("currency", "currency_code", "ccy")),
    ("net", ("net_position_ghs", "net_position", "net_open_position_ghs", "net")),
    ("long", ("long_position_ghs", "long_position", "long")),
    ("short", ("short_position_ghs", "short_position", "short")),
)


def _pick(entry: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in entry:
            return entry[name]
    return None


class FxPositionResolver:
    block_type = "fx_position"
    version = _VERSION

    def probe(self, rc: ResolveContext) -> SourceProbe:
        if rc.period is None:
            return SourceProbe(current_key=None, reason="no reporting period for the as-of date")
        run = _run(rc)
        if run is None:
            return SourceProbe(current_key=None, reason="no succeeded FX run")
        return SourceProbe(
            current_key=f"run:{run.id}", withdrawn=resolvers.run_withdrawn(rc.db, run)
        )

    def resolve(self, rc: ResolveContext) -> Resolution:
        if rc.period is None:
            raise resolvers.no_period(rc)
        run = _run(rc)
        if run is None:
            raise Unavailable(
                f"No FX run exists for {rc.cycle.as_of_date.isoformat()}. Run the FX "
                "engine for that reporting period, then link this figure again."
            )
        spec = rc.spec
        metrics: dict[str, Any] = run.metrics or {}
        rows = metrics.get("positions") or metrics.get("by_currency") or []
        table = resolvers.TableBuilder("positions", "Net open position by currency")
        table.column("currency", "Currency", "text")
        table.column("long", "Long", "amount")
        table.column("short", "Short", "amount")
        table.column("net_reporting", "Net, reporting currency", "amount")
        if isinstance(rows, list):
            for entry in rows:
                if not isinstance(entry, dict):
                    continue
                values = {key: _pick(entry, names) for key, names in _POSITION_KEYS}
                if values["currency"] is None:
                    continue
                table.row(
                    {
                        "currency": values["currency"],
                        "long": values["long"],
                        "short": values["short"],
                        "net_reporting": values["net"],
                    }
                )
        facts = {
            "nop": resolvers.fact(
                spec,
                "nop",
                metrics.get("nop_ghs") or metrics.get("net_open_position_ghs"),
                currency=rc.currency,
            ),
            "nop_pct_tier1": resolvers.fact(
                spec,
                "nop_pct_tier1",
                metrics.get("nop_pct_tier1") or metrics.get("nop_ratio_pct"),
            ),
            "tier1": resolvers.fact(spec, "tier1", metrics.get("tier1_ghs"), currency=rc.currency),
        }
        body = resolvers.payload(
            title=spec.title,
            as_of=rc.cycle.as_of_date,
            source_label=f"Official FX run (baseline) · {rc.cycle.as_of_date.isoformat()}",
            currency=rc.currency,
            tables=[table.build()],
        )
        return Resolution(
            source_kind="run",
            source_ref={
                "run_id": str(run.id),
                "input_hash": run.input_hash,
                "engine_version": run.engine_version,
                "scenario_code": run.scenario_code,
            },
            source_key=f"run:{run.id}",
            source_as_of=rc.cycle.as_of_date,
            payload=body,
            facts=facts,
            source_run_ids=(str(run.id),),
        )


__all__ = ["FxPositionResolver"]
