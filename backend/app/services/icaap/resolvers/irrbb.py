"""Interest rate risk in the banking book, from the sealed baseline IRR run.

This block reports the engine's economic-value figures and states its sign
convention; it draws NO outlier conclusion. The existing engine's outlier test
counts economic-value GAINS as breaches and ignores the +/-450bp scenarios,
which is a defect recorded as D-013. Publishing a conclusion from it inside a
Board report would launder that defect into a filed document, so the ICAAP
states the measured deltas and leaves the outlier test to the losses-only
measure P2 builds.
"""

from __future__ import annotations

from typing import Any

from app.domain.icaap.blocks import SourceProbe
from app.models import RegulatoryRun
from app.services import regulatory_irr
from app.services.icaap import resolvers, sources
from app.services.icaap.resolvers import Resolution, ResolveContext, Unavailable

_VERSION = "1"
_SIGN_CONVENTION = (
    "A negative change in economic value of equity is a loss of value under that "
    "scenario, measured against the base case."
)


def _run(rc: ResolveContext) -> RegulatoryRun | None:
    if rc.period is None:
        return None
    return sources.latest_succeeded_run(
        rc.db,
        rc.access,
        rc.period,
        regulatory_irr.MODULE_IRR,
        regulatory_irr.BASELINE_SCENARIO,
    )


class IrrbbResolver:
    block_type = "irrbb"
    version = _VERSION

    def probe(self, rc: ResolveContext) -> SourceProbe:
        if rc.period is None:
            return SourceProbe(current_key=None, reason="no reporting period for the as-of date")
        run = _run(rc)
        if run is None:
            return SourceProbe(current_key=None, reason="no succeeded IRRBB run")
        return SourceProbe(
            current_key=f"run:{run.id}", withdrawn=resolvers.run_withdrawn(rc.db, run)
        )

    def resolve(self, rc: ResolveContext) -> Resolution:
        if rc.period is None:
            raise resolvers.no_period(rc)
        run = _run(rc)
        if run is None:
            raise Unavailable(
                f"No IRRBB run exists for {rc.cycle.as_of_date.isoformat()}. Run the "
                "IRRBB engine for that reporting period, then link this figure again."
            )
        spec = rc.spec
        metrics: dict[str, Any] = run.metrics or {}
        scenarios = metrics.get("eve_by_scenario")
        table = resolvers.TableBuilder("eve_by_scenario", "Economic value of equity by scenario")
        table.column("scenario_code", "Scenario", "text")
        table.column("eve", "Economic value of equity", "amount")
        table.column("delta_eve", "Change", "amount")
        table.column("delta_eve_pct_tier1", "Change as share of Tier 1", "ratio_pct")
        if isinstance(scenarios, list):
            for entry in scenarios:
                if not isinstance(entry, dict):
                    continue
                table.row(
                    {
                        "scenario_code": entry.get("scenario_code"),
                        "eve": entry.get("eve_ghs"),
                        "delta_eve": entry.get("delta_eve_ghs"),
                        "delta_eve_pct_tier1": entry.get("delta_eve_pct_tier1"),
                    }
                )
        facts = {
            "eve_base": resolvers.fact(
                spec, "eve_base", metrics.get("eve_base_ghs"), currency=rc.currency
            ),
            "tier1": resolvers.fact(spec, "tier1", metrics.get("tier1_ghs"), currency=rc.currency),
            "nii_base": resolvers.fact(
                spec, "nii_base", metrics.get("nii_base_ghs"), currency=rc.currency
            ),
            "worst_scenario": resolvers.fact(spec, "worst_scenario", metrics.get("worst_scenario")),
        }
        body = resolvers.payload(
            title=spec.title,
            as_of=rc.cycle.as_of_date,
            source_label=f"Official IRRBB run (baseline) · {rc.cycle.as_of_date.isoformat()}",
            currency=rc.currency,
            tables=[table.build()],
            notes=[_SIGN_CONVENTION],
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


__all__ = ["IrrbbResolver"]
