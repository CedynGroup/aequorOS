"""Capital position and Pillar 1 RWA, from the sealed baseline capital run."""

from __future__ import annotations

from app.domain.icaap.blocks import SourceProbe
from app.services.icaap import resolvers
from app.services.icaap.resolvers import Resolution, ResolveContext, Unavailable
from app.services.regulatory_capital import OfficialCapitalPosition, official_capital_position

_VERSION = "1"
_NO_RUN = (
    "No official capital run exists for {as_of}. Run the capital engine for that "
    "reporting period, then link this figure again."
)


def _position(rc: ResolveContext) -> OfficialCapitalPosition:
    if rc.period is None:
        raise resolvers.no_period(rc)
    position = official_capital_position(rc.db, rc.access.ctx, rc.access.bank, rc.period)
    if position is None:
        raise Unavailable(_NO_RUN.format(as_of=rc.cycle.as_of_date.isoformat()))
    return position


def _probe(rc: ResolveContext) -> SourceProbe:
    if rc.period is None:
        return SourceProbe(current_key=None, reason="no reporting period for the as-of date")
    position = official_capital_position(rc.db, rc.access.ctx, rc.access.bank, rc.period)
    if position is None:
        return SourceProbe(current_key=None, reason="no official capital run")
    return SourceProbe(
        current_key=f"run:{position.run.id}",
        withdrawn=resolvers.run_withdrawn(rc.db, position.run),
    )


def _source_ref(position: OfficialCapitalPosition) -> dict[str, object]:
    run = position.run
    return {
        "run_id": str(run.id),
        "input_hash": run.input_hash,
        "engine_version": run.engine_version,
        "scenario_code": run.scenario_code,
        "reporting_period_id": str(run.reporting_period_id),
    }


def _source_label(rc: ResolveContext) -> str:
    return f"Official capital run (baseline) · {rc.cycle.as_of_date.isoformat()}"


class CapitalPositionResolver:
    block_type = "capital_position"
    version = _VERSION

    def probe(self, rc: ResolveContext) -> SourceProbe:
        return _probe(rc)

    def resolve(self, rc: ResolveContext) -> Resolution:
        position = _position(rc)
        spec = rc.spec
        metrics = position.metrics
        table = resolvers.TableBuilder("capital_components", "Capital components")
        table.column("label", "Component", "text").column("amount", "Amount", "amount")
        for line in position.sections.get("capital_component", []):
            table.row({"label": line.description, "amount": line.weighted_amount})
        facts = {
            "car_pct": resolvers.fact(spec, "car_pct", metrics.car_pct),
            "cet1_ratio_pct": resolvers.fact(spec, "cet1_ratio_pct", metrics.cet1_ratio_pct),
            "tier1_ratio_pct": resolvers.fact(spec, "tier1_ratio_pct", metrics.tier1_ratio_pct),
            "leverage_ratio_pct": resolvers.fact(
                spec, "leverage_ratio_pct", metrics.leverage_ratio_pct
            ),
            "total_capital": resolvers.fact(
                spec, "total_capital", metrics.total_capital_ghs, currency=rc.currency
            ),
            "total_rwa": resolvers.fact(
                spec, "total_rwa", metrics.total_rwa_ghs, currency=rc.currency
            ),
        }
        body = resolvers.payload(
            title=spec.title,
            as_of=rc.cycle.as_of_date,
            source_label=_source_label(rc),
            currency=rc.currency,
            tables=[table.build()],
        )
        return Resolution(
            source_kind="run",
            source_ref=_source_ref(position),
            source_key=f"run:{position.run.id}",
            source_as_of=rc.cycle.as_of_date,
            payload=body,
            facts=facts,
            source_run_ids=(str(position.run.id),),
        )


class Pillar1RwaResolver:
    block_type = "pillar1_rwa"
    version = _VERSION

    def probe(self, rc: ResolveContext) -> SourceProbe:
        return _probe(rc)

    def resolve(self, rc: ResolveContext) -> Resolution:
        position = _position(rc)
        spec = rc.spec
        metrics = position.metrics
        tables = []
        for key, title in (
            ("credit_rwa", "Credit risk-weighted assets"),
            ("market_rwa", "Market risk-weighted assets"),
            ("operational_rwa", "Operational risk-weighted assets"),
        ):
            lines = position.sections.get(key, [])
            if not lines:
                continue
            table = resolvers.TableBuilder(key, title)
            table.column("label", "Line", "text")
            table.column("exposure", "Exposure", "amount")
            table.column("rate_pct", "Weight", "ratio_pct")
            table.column("weighted", "Risk-weighted", "amount")
            for line in lines:
                table.row(
                    {
                        "label": line.description,
                        "exposure": line.exposure_amount,
                        "rate_pct": line.rate_pct,
                        "weighted": line.weighted_amount,
                    }
                )
            tables.append(table.build())
        facts = {
            "credit_rwa": resolvers.fact(
                spec, "credit_rwa", metrics.credit_rwa_ghs, currency=rc.currency
            ),
            "market_rwa": resolvers.fact(
                spec, "market_rwa", metrics.market_rwa_ghs, currency=rc.currency
            ),
            "operational_rwa": resolvers.fact(
                spec, "operational_rwa", metrics.operational_rwa_ghs, currency=rc.currency
            ),
            "total_rwa": resolvers.fact(
                spec, "total_rwa", metrics.total_rwa_ghs, currency=rc.currency
            ),
        }
        body = resolvers.payload(
            title=spec.title,
            as_of=rc.cycle.as_of_date,
            source_label=_source_label(rc),
            currency=rc.currency,
            tables=tables,
        )
        return Resolution(
            source_kind="run",
            source_ref=_source_ref(position),
            source_key=f"run:{position.run.id}",
            source_as_of=rc.cycle.as_of_date,
            payload=body,
            facts=facts,
            source_run_ids=(str(position.run.id),),
        )


__all__ = ["CapitalPositionResolver", "Pillar1RwaResolver"]
