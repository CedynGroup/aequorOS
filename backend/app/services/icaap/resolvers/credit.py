"""Credit concentration, computed from the canonical book as at the year end.

There is no sealed "concentration run" to bind, so the binding pins a digest of
the computed read instead: a refresh that produces the same digest is not a new
binding, and one that differs makes the block stale in exactly the way a new
engine run would. The vectors come from the canonical as-of snapshot at full
precision — not from the live plane, and not from a rounded top-N view (D-034).

The index is republished on the 0-1 scale the ICAAP work uses throughout, while
the underlying monitor keeps its 0-10,000 convention. One conversion, in one
place, rather than two modules quietly disagreeing about what HHI means.
"""

from __future__ import annotations

from decimal import Decimal

from app.core.errors import ModuleDataUnavailable
from app.domain.credit.concentration_monitor import MONITOR_DIMENSIONS
from app.domain.icaap.blocks import SourceProbe
from app.services import credit_concentration
from app.services.attestation.digests import digest_of
from app.services.icaap import resolvers
from app.services.icaap.resolvers import Resolution, ResolveContext, Unavailable

_VERSION = "1"
#: The monitor publishes HHI on the 0-10,000 basis; ICAAP is canonical on 0-1.
_HHI_SCALE = Decimal("10000")


def _read(rc: ResolveContext):
    try:
        return credit_concentration.concentration_read(
            rc.db, rc.access.ctx, rc.access.bank, rc.cycle.as_of_date
        )
    except ModuleDataUnavailable as exc:
        raise Unavailable(
            f"{exc.reason} Ingest the loan book as at {rc.cycle.as_of_date.isoformat()} "
            "through the Data Engine, then link this figure again."
        ) from exc


class ConcentrationResolver:
    block_type = "concentration"
    version = _VERSION

    def probe(self, rc: ResolveContext) -> SourceProbe:
        try:
            read = _read(rc)
        except Unavailable as exc:
            return SourceProbe(current_key=None, reason=exc.reason)
        return SourceProbe(current_key=f"computed:{digest_of(read.model_dump(mode='json'))}")

    def resolve(self, rc: ResolveContext) -> Resolution:
        read = _read(rc)
        spec = rc.spec
        inputs_digest = digest_of(read.model_dump(mode="json"))
        by_dimension = {entry.dimension: entry for entry in read.dimensions}

        table = resolvers.TableBuilder("dimensions", "Concentration by dimension")
        table.column("dimension", "Dimension", "text")
        table.column("hhi", "Concentration index", "text")
        table.column("buckets", "Buckets", "count")
        table.column("coverage_pct", "Book coverage", "ratio_pct")
        table.column("exposure", "Stated exposure", "amount")
        table.column("status", "Against limit", "text")
        facts = {}
        for dimension in MONITOR_DIMENSIONS:
            entry = by_dimension.get(dimension)
            hhi = None if entry is None else (entry.hhi / _HHI_SCALE)
            table.row(
                {
                    "dimension": dimension.replace("_", " ").capitalize(),
                    "hhi": hhi,
                    "buckets": None if entry is None else entry.bucket_count,
                    "coverage_pct": None if entry is None else entry.coverage_pct,
                    "exposure": None if entry is None else entry.stated_exposure_ghs,
                    "status": None if entry is None else entry.hhi_status,
                }
            )
            facts[f"hhi_{dimension}"] = resolvers.fact(spec, f"hhi_{dimension}", hhi)
            facts[f"coverage_{dimension}_pct"] = resolvers.fact(
                spec,
                f"coverage_{dimension}_pct",
                None if entry is None else entry.coverage_pct,
            )
        facts["breach_count"] = resolvers.fact(spec, "breach_count", len(read.breaches))
        facts["capital_basis"] = resolvers.fact(spec, "capital_basis", read.capital_basis)

        tables = [table.build()]
        if read.breaches:
            breaches = resolvers.TableBuilder("breaches", "Limits breached")
            breaches.column("bucket", "Bucket", "text")
            breaches.column("exposure", "Exposure", "amount")
            breaches.column("share_of_capital_pct", "Share of capital", "ratio_pct")
            breaches.column("limit_value", "Limit", "text")
            for bucket in read.breaches:
                breaches.row(
                    {
                        "bucket": bucket.key,
                        "exposure": bucket.exposure_ghs,
                        "share_of_capital_pct": bucket.share_of_capital_pct,
                        "limit_value": bucket.limit_value,
                    }
                )
            tables.append(breaches.build())

        body = resolvers.payload(
            title=spec.title,
            as_of=rc.cycle.as_of_date,
            source_label=f"Canonical loan book · {rc.cycle.as_of_date.isoformat()}",
            currency=rc.currency,
            tables=tables,
            notes=[
                "Concentration index reported on a 0-1 scale.",
                f"Capital basis: {read.capital_basis}.",
            ],
            raw={
                "total_book": str(read.total_book_ghs),
                "capital_base": (
                    None if read.capital_base_ghs is None else str(read.capital_base_ghs)
                ),
                "limit_count": read.limit_count,
            },
        )
        return Resolution(
            source_kind="computed",
            source_ref={"as_of": rc.cycle.as_of_date.isoformat(), "inputs_digest": inputs_digest},
            source_key=f"computed:{inputs_digest}",
            source_as_of=rc.cycle.as_of_date,
            payload=body,
            facts=facts,
        )


__all__ = ["ConcentrationResolver"]
