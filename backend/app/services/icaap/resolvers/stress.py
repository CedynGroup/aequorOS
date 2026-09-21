"""Stress-test blocks: the attested Appendix II tables, the narratives, reverse stress.

All three bind ATTESTED state. An enterprise-stress run that no one has signed
off is a calculation, not a result a Board has stood behind, and the ICAAP is
where the Board says it has. That is why a run without an attested sign-off is
reported as unavailable rather than quietly used.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from app.domain.icaap.blocks import SourceProbe
from app.models import RegulatoryPackage, RegulatoryRun, User
from app.models.stress import EnterpriseStressSignoff
from app.services import enterprise_stress_signoff, reverse_stress
from app.services.icaap import resolvers
from app.services.icaap.resolvers import Resolution, ResolveContext, Unavailable

_VERSION = "1"
_APPENDIX_PACKAGE = "ICAAP-STRESS-APPENDIX2"


def _attested(rc: ResolveContext) -> tuple[RegulatoryRun, EnterpriseStressSignoff]:
    if rc.period is None:
        raise resolvers.no_period(rc)
    try:
        run = enterprise_stress_signoff.resolve_attested_run_for_period(
            rc.db, rc.access.ctx, rc.access.bank.id, rc.period.id
        )
    except HTTPException as exc:
        raise Unavailable(_refusal_text(exc)) from exc
    signoff = enterprise_stress_signoff.latest_signoff_for_run(rc.db, rc.access.ctx, run.id)
    if signoff is None:  # pragma: no cover - resolve_attested_run guarantees one
        raise Unavailable("The attested stress run has no sign-off record.")
    return run, signoff


def _refusal_text(exc: HTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, dict):
        message = detail.get("message")
        if isinstance(message, str):
            return message
    return str(detail)


def _attested_probe(rc: ResolveContext, *, key_prefix: str) -> SourceProbe:
    if rc.period is None:
        return SourceProbe(current_key=None, reason="no reporting period for the as-of date")
    try:
        run = enterprise_stress_signoff.resolve_attested_run_for_period(
            rc.db, rc.access.ctx, rc.access.bank.id, rc.period.id
        )
    except HTTPException:
        return SourceProbe(current_key=None, reason="no Board-attested stress run")
    signoff = enterprise_stress_signoff.latest_signoff_for_run(rc.db, rc.access.ctx, run.id)
    if signoff is None:  # pragma: no cover
        return SourceProbe(current_key=None, reason="no sign-off record")
    withdrawn = resolvers.run_withdrawn(rc.db, run)
    if key_prefix == "signoff":
        return SourceProbe(
            current_key=f"signoff:{signoff.id}:v{signoff.version}", withdrawn=withdrawn
        )
    package = _current_appendix_package(rc)
    return SourceProbe(current_key=_appendix_key(run, signoff, package), withdrawn=withdrawn)


def _current_appendix_package(rc: ResolveContext) -> RegulatoryPackage | None:
    return rc.db.scalar(
        select(RegulatoryPackage)
        .where(
            RegulatoryPackage.organization_id == rc.access.ctx.organization_id,
            RegulatoryPackage.bank_id == rc.access.bank.id,
            RegulatoryPackage.return_code == _APPENDIX_PACKAGE,
            RegulatoryPackage.reporting_date == rc.cycle.as_of_date,
            RegulatoryPackage.basis == rc.cycle.basis,
            RegulatoryPackage.status != "superseded",
        )
        .order_by(RegulatoryPackage.version.desc())
        .limit(1)
    )


def _appendix_key(
    run: RegulatoryRun, signoff: EnterpriseStressSignoff, package: RegulatoryPackage | None
) -> str:
    package_part = (
        "pkg:none"
        if package is None
        else f"pkg:{package.id}:v{package.version}:{package.content_digest or 'none'}"
    )
    return f"run:{run.id}|signoff:{signoff.id}:v{signoff.version}|{package_part}"


def _tables_from_appendix(appendix: dict[str, Any]) -> list[dict[str, Any]]:
    """Render whatever named tables the attested run carries, generically.

    The Appendix II shape is the stress engine's, not this module's: reshaping
    it here would create a second opinion about what BoG's tables say.
    """
    tables: list[dict[str, Any]] = []
    for key, value in sorted(appendix.items()):
        if not isinstance(value, list) or not value:
            continue
        if not all(isinstance(entry, dict) for entry in value):
            continue
        columns: list[str] = []
        for entry in value:
            for column in entry:
                if column not in columns:
                    columns.append(column)
        table = resolvers.TableBuilder(key, key.replace("_", " ").capitalize())
        for column in columns:
            table.column(column, column.replace("_", " ").capitalize(), "text")
        for entry in value:
            table.row({column: entry.get(column) for column in columns})
        tables.append(table.build())
    return tables


class AppendixIIResolver:
    block_type = "appendix_ii"
    version = _VERSION

    def probe(self, rc: ResolveContext) -> SourceProbe:
        return _attested_probe(rc, key_prefix="appendix")

    def resolve(self, rc: ResolveContext) -> Resolution:
        run, signoff = _attested(rc)
        appendix = run.metrics.get("appendix_ii")
        if not isinstance(appendix, dict) or "table1_summary" not in appendix:
            raise Unavailable(
                "The attested stress run carries no Appendix II tables. Re-run the "
                "enterprise stress test, then attest it again."
            )
        package = _current_appendix_package(rc)
        spec = rc.spec
        summary = appendix.get("table1_summary")
        summary_row = summary if isinstance(summary, dict) else {}
        facts = {
            "horizon_years": resolvers.fact(
                spec, "horizon_years", summary_row.get("horizon_years")
            ),
            "car_target_pct": resolvers.fact(
                spec, "car_target_pct", summary_row.get("car_target_pct")
            ),
            "with_management_actions": resolvers.fact(
                spec, "with_management_actions", summary_row.get("management_actions") is not None
            ),
            "stays_above_all_minima": resolvers.fact(
                spec, "stays_above_all_minima", signoff.stays_above_all_minima
            ),
            "scenario_code": resolvers.fact(spec, "scenario_code", run.scenario_code),
        }
        body = resolvers.payload(
            title=spec.title,
            as_of=rc.cycle.as_of_date,
            source_label=(
                f"Board-attested enterprise stress run · {rc.cycle.as_of_date.isoformat()}"
            ),
            currency=rc.currency,
            tables=_tables_from_appendix(appendix),
            raw={"raw_appendix_ii": appendix},
        )
        source_ref: dict[str, Any] = {
            "run_id": str(run.id),
            "input_hash": run.input_hash,
            "signoff_id": str(signoff.id),
            "signoff_version": signoff.version,
            "signoff_status": signoff.status,
        }
        if package is not None:
            source_ref |= {
                "package_id": str(package.id),
                "package_version": package.version,
                "package_content_digest": package.content_digest,
            }
        return Resolution(
            source_kind="run",
            source_ref=source_ref,
            source_key=_appendix_key(run, signoff, package),
            source_as_of=rc.cycle.as_of_date,
            payload=body,
            facts=facts,
            source_run_ids=(str(run.id),),
        )


def _attester(rc: ResolveContext, signoff: EnterpriseStressSignoff) -> tuple[str, str]:
    """The attester's name and title — never a raw user id in a Board report."""
    if signoff.attested_by is None:
        return "Name not recorded", ""
    user = rc.db.scalar(
        select(User).where(
            User.id == signoff.attested_by,
            User.organization_id == rc.access.ctx.organization_id,
        )
    )
    if user is None:
        return "Name not recorded", ""
    name = (getattr(user, "full_name", None) or getattr(user, "email", "")) or "Name not recorded"
    return str(name), str(getattr(user, "job_title", "") or "")


class StressNarrativesResolver:
    block_type = "stress_narratives"
    version = _VERSION

    def probe(self, rc: ResolveContext) -> SourceProbe:
        return _attested_probe(rc, key_prefix="signoff")

    def resolve(self, rc: ResolveContext) -> Resolution:
        run, signoff = _attested(rc)
        spec = rc.spec
        name, title = _attester(rc, signoff)
        narratives = {
            "scenario_narrative": signoff.scenario_narrative,
            "assumptions_rationale": signoff.assumptions_rationale,
            "methodology_summary": signoff.methodology_summary,
            "board_challenge": signoff.board_challenge,
            "credibility_rationale": signoff.credibility_rationale,
        }
        table = resolvers.TableBuilder("narratives", "Stress test narratives")
        table.column("topic", "Topic", "text").column("statement", "Statement", "text")
        for key, value in narratives.items():
            table.row({"topic": key.replace("_", " ").capitalize(), "statement": value})
        facts = {
            "attested_on": resolvers.fact(
                spec, "attested_on", signoff.attested_at.date() if signoff.attested_at else None
            ),
            "stays_above_all_minima": resolvers.fact(
                spec, "stays_above_all_minima", signoff.stays_above_all_minima
            ),
            "with_actions_stays_above_all_minima": resolvers.fact(
                spec,
                "with_actions_stays_above_all_minima",
                signoff.with_actions_stays_above_all_minima,
            ),
        }
        body = resolvers.payload(
            title=spec.title,
            as_of=rc.cycle.as_of_date,
            source_label=f"Stress sign-off version {signoff.version}, attested by {name}",
            currency=rc.currency,
            tables=[table.build()],
            notes=[f"Attested by {name}, {title}" if title else f"Attested by {name}"],
            raw={"narratives": narratives, "attester_name": name, "attester_title": title},
        )
        return Resolution(
            source_kind="signoff",
            source_ref={
                "signoff_id": str(signoff.id),
                "signoff_version": signoff.version,
                "signoff_status": signoff.status,
                "run_id": str(run.id),
                "input_hash": run.input_hash,
            },
            source_key=f"signoff:{signoff.id}:v{signoff.version}",
            source_as_of=rc.cycle.as_of_date,
            payload=body,
            facts=facts,
            source_run_ids=(str(run.id),),
        )


class ReverseStressResolver:
    block_type = "reverse_stress"
    version = _VERSION

    def probe(self, rc: ResolveContext) -> SourceProbe:
        if rc.period is None:
            return SourceProbe(current_key=None, reason="no reporting period for the as-of date")
        try:
            read = reverse_stress.get_latest_reverse_stress(
                rc.db, rc.access.ctx, rc.access.bank.id, rc.period.id
            )
        except HTTPException:
            return SourceProbe(current_key=None, reason="no reverse-stress run")
        run = rc.db.get(RegulatoryRun, read.run_id)
        withdrawn = run is not None and resolvers.run_withdrawn(rc.db, run)
        return SourceProbe(current_key=f"run:{read.run_id}", withdrawn=withdrawn)

    def resolve(self, rc: ResolveContext) -> Resolution:
        if rc.period is None:
            raise resolvers.no_period(rc)
        try:
            read = reverse_stress.get_latest_reverse_stress(
                rc.db, rc.access.ctx, rc.access.bank.id, rc.period.id
            )
        except HTTPException as exc:
            raise Unavailable(
                "No reverse stress test exists for this year end. Run it, then link "
                "this figure again."
            ) from exc
        spec = rc.spec
        capital_axis = read.capital_axis or {}
        liquidity_axis = read.liquidity_axis or {}
        table = resolvers.TableBuilder("axes", "Reverse stress axes")
        table.column("axis", "Axis", "text")
        table.column("breached", "Breach reached", "text")
        table.column("scenario", "Scenario", "text")
        table.column("multiplier", "Severity multiple", "text")
        for label, axis in (("Capital", capital_axis), ("Liquidity", liquidity_axis)):
            table.row(
                {
                    "axis": label,
                    "breached": axis.get("breached"),
                    "scenario": axis.get("scenario_code"),
                    "multiplier": axis.get("breach_multiplier"),
                }
            )
        facts = {
            "capital_breached": resolvers.fact(
                spec, "capital_breached", capital_axis.get("breached")
            ),
            "liquidity_breached": resolvers.fact(
                spec, "liquidity_breached", liquidity_axis.get("breached")
            ),
            "capital_breach_multiplier": resolvers.fact(
                spec, "capital_breach_multiplier", capital_axis.get("breach_multiplier")
            ),
            "liquidity_breach_multiplier": resolvers.fact(
                spec, "liquidity_breach_multiplier", liquidity_axis.get("breach_multiplier")
            ),
            "cet1_floor_pct": resolvers.fact(
                spec, "cet1_floor_pct", capital_axis.get("cet1_min_pct")
            ),
            "lcr_floor_pct": resolvers.fact(
                spec, "lcr_floor_pct", liquidity_axis.get("lcr_min_pct")
            ),
        }
        body = resolvers.payload(
            title=spec.title,
            as_of=rc.cycle.as_of_date,
            source_label=f"Reverse stress run · {rc.cycle.as_of_date.isoformat()}",
            currency=rc.currency,
            tables=[table.build()],
            notes=[read.narrative] if read.narrative else [],
        )
        return Resolution(
            source_kind="run",
            source_ref={
                "run_id": str(read.run_id),
                "input_hash": read.input_hash,
                "engine_version": read.engine_version,
            },
            source_key=f"run:{read.run_id}",
            source_as_of=rc.cycle.as_of_date,
            payload=body,
            facts=facts,
            source_run_ids=(str(read.run_id),),
        )


__all__ = ["AppendixIIResolver", "ReverseStressResolver", "StressNarrativesResolver"]
