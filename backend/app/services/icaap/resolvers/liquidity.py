"""Liquidity adequacy, from the quarterly ILAAP snapshot for the year end.

The snapshot already captures the contingency-funding and early-warning posture
as at its own date, so nothing here re-evaluates a live early-warning state: an
ICAAP describes the position it assesses, not this morning's.
"""

from __future__ import annotations

from app.domain.icaap.blocks import SourceProbe
from app.schemas.capital_plan import IlaapSnapshotRead
from app.services import capital_plan as capital_plan_service
from app.services.icaap import resolvers
from app.services.icaap.resolvers import Resolution, ResolveContext, Unavailable

_VERSION = "1"


def _snapshot(rc: ResolveContext) -> IlaapSnapshotRead | None:
    if rc.period is None:
        return None
    listing = capital_plan_service.list_ilaap_snapshots(
        rc.db, rc.access.ctx, rc.access.bank.id, rc.period.id
    )
    return listing.snapshots[0] if listing.snapshots else None


class IlaapResolver:
    block_type = "ilaap"
    version = _VERSION

    def probe(self, rc: ResolveContext) -> SourceProbe:
        if rc.period is None:
            return SourceProbe(current_key=None, reason="no reporting period for the as-of date")
        snapshot = _snapshot(rc)
        if snapshot is None:
            return SourceProbe(current_key=None, reason="no ILAAP snapshot")
        return SourceProbe(current_key=f"snapshot:{snapshot.id}")

    def resolve(self, rc: ResolveContext) -> Resolution:
        if rc.period is None:
            raise resolvers.no_period(rc)
        snapshot = _snapshot(rc)
        if snapshot is None:
            raise Unavailable(
                "No ILAAP snapshot exists for this year end. Refresh the ILAAP "
                "component in Basel > Capital planning, then link this figure again."
            )
        spec = rc.spec
        table = resolvers.TableBuilder("ratios", "Liquidity ratios")
        table.column("metric", "Metric", "text")
        table.column("value", "Value", "ratio_pct")
        table.column("status", "Status", "text")
        table.row(
            {
                "metric": "Liquidity coverage ratio",
                "value": snapshot.lcr_pct,
                "status": snapshot.lcr_status,
            }
        )
        table.row(
            {
                "metric": "Net stable funding ratio",
                "value": snapshot.nsfr_pct,
                "status": snapshot.nsfr_status,
            }
        )
        table.row(
            {
                "metric": "Worst stressed liquidity coverage ratio",
                "value": snapshot.worst_stressed_lcr_pct,
                "status": None,
            }
        )
        facts = {
            "ilaap_adequate": resolvers.fact(spec, "ilaap_adequate", snapshot.adequate),
            "cfp_approved": resolvers.fact(spec, "cfp_approved", snapshot.cfp_approved),
            "cfp_active": resolvers.fact(spec, "cfp_active", snapshot.cfp_active),
            "lcr_pct": resolvers.fact(spec, "lcr_pct", snapshot.lcr_pct),
            "nsfr_pct": resolvers.fact(spec, "nsfr_pct", snapshot.nsfr_pct),
            "worst_stressed_lcr_pct": resolvers.fact(
                spec, "worst_stressed_lcr_pct", snapshot.worst_stressed_lcr_pct
            ),
            "ewi_escalation_state": resolvers.fact(
                spec, "ewi_escalation_state", snapshot.ewi_escalation_state
            ),
        }
        body = resolvers.payload(
            title=spec.title,
            as_of=snapshot.as_of_date,
            source_label=f"ILAAP snapshot · {snapshot.as_of_date.isoformat()}",
            currency=rc.currency,
            tables=[table.build()],
            notes=[snapshot.notes] if snapshot.notes else [],
        )
        return Resolution(
            source_kind="snapshot",
            source_ref={
                "snapshot_id": str(snapshot.id),
                "reporting_period_id": str(snapshot.reporting_period_id),
            },
            source_key=f"snapshot:{snapshot.id}",
            source_as_of=snapshot.as_of_date,
            payload=body,
            facts=facts,
        )


__all__ = ["IlaapResolver"]
