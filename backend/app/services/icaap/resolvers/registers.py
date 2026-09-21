"""Blocks that publish the ICAAP's own registers into its prose.

These have no sealed run to bind. The binding pins a value-based DIGEST of the
register's content instead, so a refresh that changes nothing writes nothing,
and a changed figure makes every sentence quoting it stale — the same
behaviour a new engine run gives the capital blocks, from a different source.

Each resolver reads through the SERVICE rather than the tables, so the figure
in the report is the one the workspace shows, computed by the same code. The
alternative — a second aggregation for the report — is how two numbers for one
thing appear in a filing.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.icaap.blocks import SourceProbe
from app.domain.icaap.pillar2 import table5 as table5_domain
from app.services.icaap import digests, resolvers
from app.services.icaap.resolvers import Resolution, ResolveContext, Unavailable

_VERSION = "1"


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _resolution(  # noqa: PLR0913 - a register resolution is its six named parts
    rc: ResolveContext,
    register: str,
    body: dict[str, Any],
    facts: dict[str, dict[str, Any]],
    tables: list[dict[str, Any]],
    *,
    notes: list[str] | None = None,
) -> Resolution:
    digest = digests.register_digest(body)
    return Resolution(
        source_kind="computed",
        source_ref={"register": register, "digest": digest},
        source_key=digests.source_key(register, digest),
        source_as_of=None,
        payload=resolvers.payload(
            title=rc.spec.title,
            as_of=rc.cycle.as_of_date,
            source_label=f"ICAAP {register.replace('_', ' ')} · this cycle",
            currency=rc.currency,
            tables=tables,
            notes=notes,
            raw={"register": body},
        ),
        facts=facts,
    )


def _probe(rc: ResolveContext, register: str, body: dict[str, Any]) -> SourceProbe:
    return SourceProbe(current_key=digests.source_key(register, digests.register_digest(body)))


class RiskRegisterResolver:
    block_type = "risk_register"
    version = _VERSION

    def _body(self, rc: ResolveContext):
        from app.services.icaap import risks as risks_service  # noqa: PLC0415

        read = risks_service.get_register(rc.db, rc.access, rc.cycle.id)
        body = {
            "risks": [
                {
                    "risk_key": risk.risk_key,
                    "title": risk.title,
                    "likelihood_score": risk.likelihood_score,
                    "impact_score": risk.impact_score,
                    "materiality_score": risk.materiality_score,
                    "rating_key": risk.rating_key,
                    "verdict": risk.verdict,
                    "pillar1_coverage": risk.pillar1_coverage,
                    "pillar2_treatment": risk.pillar2_treatment,
                }
                for risk in read.risks
                if risk.retired_at is None
            ],
            "summary": read.summary.model_dump(mode="json"),
        }
        return read, body

    def probe(self, rc: ResolveContext) -> SourceProbe:
        _read, body = self._body(rc)
        return _probe(rc, self.block_type, body)

    def resolve(self, rc: ResolveContext) -> Resolution:
        read, body = self._body(rc)
        spec = rc.spec
        table = resolvers.TableBuilder("risks", "Risk register")
        table.column("title", "Risk", "text")
        table.column("likelihood", "Likelihood", "count")
        table.column("impact", "Impact", "count")
        table.column("score", "Score", "count")
        table.column("rating", "Rating", "text")
        table.column("verdict", "Material", "text")
        table.column("treatment", "Treatment", "text")
        table.column("pillar1_coverage", "Covered by Pillar 1", "text")
        for risk in read.risks:
            if risk.retired_at is not None:
                continue
            table.row(
                {
                    "title": risk.title,
                    "likelihood": risk.likelihood_score,
                    "impact": risk.impact_score,
                    "score": risk.materiality_score,
                    "rating": risk.rating_key,
                    "verdict": risk.verdict,
                    "treatment": risk.pillar2_treatment,
                    "pillar1_coverage": risk.pillar1_coverage,
                }
            )
        facts = {
            "category_count": resolvers.fact(spec, "category_count", read.summary.category_count),
            "assessed_risk_count": resolvers.fact(
                spec, "assessed_risk_count", read.summary.assessed_risk_count
            ),
            "material_risk_count": resolvers.fact(
                spec, "material_risk_count", read.summary.material_risk_count
            ),
        }
        return _resolution(rc, self.block_type, body, facts, [table.build()])


class RiskAppetiteResolver:
    block_type = "risk_appetite"
    version = _VERSION

    def _read(self, rc: ResolveContext):
        from app.services.icaap import appetite as appetite_service  # noqa: PLC0415

        return appetite_service.get_appetite(rc.db, rc.access, rc.cycle.id)

    def _body(self, read) -> dict[str, Any]:
        return {
            "metrics": [
                {
                    "metric_key": metric.metric_key,
                    "label": metric.label,
                    "direction": metric.direction,
                    "appetite": _text(metric.appetite_value),
                    "tolerance": _text(metric.tolerance_value),
                    "capacity": _text(metric.capacity_value),
                    "value": None
                    if metric.evaluation is None
                    else _text(metric.evaluation.current_value),
                    "status": None if metric.evaluation is None else metric.evaluation.status,
                    "reference_missing": metric.reference_missing,
                }
                for metric in read.metrics
            ],
            "summary": read.summary.model_dump(mode="json"),
        }

    def probe(self, rc: ResolveContext) -> SourceProbe:
        return _probe(rc, self.block_type, self._body(self._read(rc)))

    def resolve(self, rc: ResolveContext) -> Resolution:
        read = self._read(rc)
        spec = rc.spec
        body = self._body(read)
        table = resolvers.TableBuilder("metrics", "Risk appetite")
        table.column("label", "Metric", "text")
        table.column("direction", "Direction", "text")
        table.column("appetite", "Appetite", "text")
        table.column("tolerance", "Tolerance", "text")
        table.column("capacity", "Capacity", "text")
        table.column("value", "Current", "text")
        table.column("status", "Status", "text")
        table.column("reference", "Regulatory reference", "text")
        facts: dict[str, dict[str, Any]] = {
            "ras_metric_count": resolvers.fact(spec, "ras_metric_count", read.summary.metric_count),
            "ras_breach_count": resolvers.fact(spec, "ras_breach_count", read.summary.breach_count),
            "ras_amber_count": resolvers.fact(spec, "ras_amber_count", read.summary.amber_count),
        }
        for metric in read.metrics:
            table.row(
                {
                    "label": metric.label,
                    "direction": metric.direction,
                    "appetite": metric.appetite_value,
                    "tolerance": metric.tolerance_value,
                    "capacity": metric.capacity_value,
                    "value": None if metric.evaluation is None else metric.evaluation.current_value,
                    "status": None if metric.evaluation is None else metric.evaluation.status,
                    "reference": None
                    if metric.regulatory_reference is None
                    else metric.regulatory_reference.value,
                }
            )
            for suffix, value in (
                ("value", None if metric.evaluation is None else metric.evaluation.current_value),
                ("appetite", metric.appetite_value),
                ("tolerance", metric.tolerance_value),
                ("capacity", metric.capacity_value),
            ):
                facts[f"{metric.metric_key}_{suffix}"] = resolvers.dynamic_fact(
                    f"{metric.metric_key}_{suffix}",
                    f"{metric.label} — {suffix}",
                    "ratio_pct" if metric.unit == "percent" else "text",
                    value,
                    None,
                )
        return _resolution(rc, self.block_type, body, facts, [table.build()])


class Pillar2SummaryResolver:
    block_type = "pillar2_summary"
    version = _VERSION

    def _read(self, rc: ResolveContext):
        from app.services.icaap import pillar2 as pillar2_service  # noqa: PLC0415

        return pillar2_service.get_register(rc.db, rc.access, rc.cycle.id)

    def _body(self, read) -> dict[str, Any]:
        return {
            "items": [
                {
                    "item_key": item.item_key,
                    "method": item.method,
                    "source": item.source,
                    "status": item.method_status,
                    "basis": item.basis,
                    "baseline": _text(item.baseline_amount),
                    "stressed": _text(item.stressed_amount),
                    "approved": item.approval_current,
                }
                for item in read.items
            ],
            "totals": read.table5_totals.model_dump(mode="json"),
        }

    def probe(self, rc: ResolveContext) -> SourceProbe:
        return _probe(rc, self.block_type, self._body(self._read(rc)))

    def resolve(self, rc: ResolveContext) -> Resolution:
        read = self._read(rc)
        spec = rc.spec
        body = self._body(read)
        table = resolvers.TableBuilder("pillar2_items", "Pillar 2 capital")
        table.column("component", "Risk", "text")
        table.column("method", "Method", "text")
        table.column("source", "Source", "text")
        table.column("basis", "Basis", "text")
        table.column("baseline", "Amount", "amount")
        table.column("stressed", "Stressed", "amount")
        table.column("status", "Status", "text")
        table.column("approved", "Approved", "text")
        for item in read.items:
            table.row(
                {
                    "component": item.component_key,
                    "method": item.method_label,
                    "source": item.source,
                    "basis": item.basis,
                    "baseline": item.baseline_amount,
                    "stressed": item.stressed_amount,
                    "status": item.method_status,
                    "approved": "yes" if item.approval_current else "no",
                }
            )
        irrbb = next((item for item in read.items if item.component_key == "irrbb"), None)
        measure = None
        outlier = None
        if irrbb is not None and irrbb.computation is not None:
            detail = irrbb.computation.get("detail") or {}
            measure = detail.get("irrbb_outlier_measure_pct")
            outlier = detail.get("outlier")
        facts: dict[str, dict[str, Any]] = {
            "pillar2_total_baseline": resolvers.fact(
                spec,
                "pillar2_total_baseline",
                read.table5_totals.total_baseline,
                currency=rc.currency,
            ),
            "pillar2_total_stressed": resolvers.fact(
                spec,
                "pillar2_total_stressed",
                read.table5_totals.total_stressed,
                currency=rc.currency,
            ),
            "pillar2_item_count": resolvers.fact(spec, "pillar2_item_count", len(read.items)),
            "pillar2_all_approved": resolvers.fact(
                spec,
                "pillar2_all_approved",
                all(item.approval_current for item in read.items) if read.items else False,
            ),
            "irrbb_outlier_measure_pct": resolvers.fact(spec, "irrbb_outlier_measure_pct", measure),
            "irrbb_outlier": resolvers.fact(spec, "irrbb_outlier", outlier),
        }
        for row in read.table5_totals.rows:
            facts[f"pillar2_{row.row}_baseline"] = resolvers.dynamic_fact(
                f"pillar2_{row.row}_baseline",
                f"{row.label} — Pillar 2 capital",
                "amount",
                row.baseline,
                rc.currency,
            )
            facts[f"pillar2_{row.row}_stressed"] = resolvers.dynamic_fact(
                f"pillar2_{row.row}_stressed",
                f"{row.label} — Pillar 2 capital, stressed",
                "amount",
                row.stressed,
                rc.currency,
            )
        return _resolution(rc, self.block_type, body, facts, [table.build()])


class Table5Pillar2Resolver:
    """The BoG Table 5 grid — Pillar 1 from the attested run, Pillar 2 from here.

    This is the block P3's ICAAP-REPORT generator reads (B3), so it composes
    STRICTLY: a row whose current and stress sides rest on different items is
    refused rather than published.
    """

    block_type = "table5_pillar2"
    version = _VERSION

    def _grid(self, rc: ResolveContext):
        from app.services.icaap import pillar2 as pillar2_service  # noqa: PLC0415

        return pillar2_service.build_table5(rc.db, rc.access, rc.cycle, strict=True)

    def probe(self, rc: ResolveContext) -> SourceProbe:
        try:
            grid, _appendix = self._grid(rc)
        except Unavailable as exc:
            return SourceProbe(current_key=None, reason=exc.reason)
        return SourceProbe(
            current_key=digests.source_key(self.block_type, digests.register_digest(_body(grid)))
        )

    def resolve(self, rc: ResolveContext) -> Resolution:
        grid, appendix_ref = self._grid(rc)
        spec = rc.spec
        body = _body(grid)
        table = resolvers.TableBuilder("table5", "Appendix II Table 5")
        table.column("line", "Line", "text")
        for column in grid.columns:
            table.column(column.key, column.label, "amount")
        for row in grid.rows:
            cells: dict[str, Any] = {"line": row.label}
            for column in grid.columns:
                cells[column.key] = row.cells.get(column.key)
            table.row(cells)
        current = next((column.key for column in grid.columns if column.basis == "baseline"), None)
        stress = next((column.key for column in grid.columns if column.basis == "stressed"), None)
        facts = {
            "pillar2_total_current": resolvers.fact(
                spec,
                "pillar2_total_current",
                None if current is None else table5_domain.pillar2_total_for(grid, current),
                currency=rc.currency,
            ),
            "pillar2_total_stress_y1": resolvers.fact(
                spec,
                "pillar2_total_stress_y1",
                None if stress is None else table5_domain.pillar2_total_for(grid, stress),
                currency=rc.currency,
            ),
            "total_capital_requirement_current": resolvers.fact(
                spec,
                "total_capital_requirement_current",
                _row_cell(grid, table5_domain.ROW_TOTAL_REQUIREMENT, current),
                currency=rc.currency,
            ),
            "total_capital_requirement_stress_y1": resolvers.fact(
                spec,
                "total_capital_requirement_stress_y1",
                _row_cell(grid, table5_domain.ROW_TOTAL_REQUIREMENT, stress),
                currency=rc.currency,
            ),
            "table5_partial": resolvers.fact(spec, "table5_partial", bool(grid.partial_rows)),
        }
        digest = digests.register_digest(body)
        return Resolution(
            source_kind="computed",
            source_ref={
                "register": self.block_type,
                "digest": digest,
                "appendix_binding": appendix_ref,
            },
            source_key=digests.source_key(self.block_type, digest),
            source_as_of=None,
            payload=resolvers.payload(
                title=spec.title,
                as_of=rc.cycle.as_of_date,
                source_label="ICAAP Pillar 2 register and the attested stress run",
                currency=rc.currency,
                tables=[table.build()],
                notes=[
                    "Amounts are in thousands, the unit the attested stress run reports.",
                    "An empty cell is not modelled, not zero.",
                ],
                raw={"register": body},
            ),
            facts=facts,
        )


def _row_cell(grid, row_key: str, column_key: str | None) -> Decimal | None:
    if column_key is None:
        return None
    for row in grid.rows:
        if row.key == row_key:
            return row.cells.get(column_key)
    return None


def _body(grid) -> dict[str, Any]:
    return {
        "columns": [
            {"key": column.key, "label": column.label, "basis": column.basis}
            for column in grid.columns
        ],
        "rows": [
            {
                "key": row.key,
                "group": row.group,
                "cells": {key: _text(value) for key, value in sorted(row.cells.items())},
            }
            for row in grid.rows
        ],
        "partial_rows": list(grid.partial_rows),
    }


class CapitalReconciliationResolver:
    block_type = "capital_reconciliation"
    version = _VERSION

    def _read(self, rc: ResolveContext):
        from app.services.icaap import reconciliation as service  # noqa: PLC0415

        return service.get_reconciliation(rc.db, rc.access, rc.cycle.id)

    def probe(self, rc: ResolveContext) -> SourceProbe:
        from app.services.icaap import reconciliation as service  # noqa: PLC0415

        return _probe(rc, self.block_type, service.reconciliation_payload(self._read(rc)))

    def resolve(self, rc: ResolveContext) -> Resolution:
        from app.services.icaap import reconciliation as service  # noqa: PLC0415

        read = self._read(rc)
        if not read.requirement.lines:
            raise Unavailable(
                "The capital requirement has not been reconciled yet. Compute it on the "
                "Pillar 2 tab, then link this figure again."
            )
        spec = rc.spec
        body = service.reconciliation_payload(read)
        requirement = resolvers.TableBuilder("requirement", "Capital requirement")
        requirement.column("label", "Risk", "text")
        requirement.column("internal", "Internal", "amount")
        requirement.column("regulatory", "Regulatory", "amount")
        requirement.column("difference", "Difference", "amount")
        for line in read.requirement.lines:
            requirement.row(
                {
                    "label": line.label,
                    "internal": line.internal_amount,
                    "regulatory": line.regulatory_amount,
                    "difference": line.difference,
                }
            )
        resources = resolvers.TableBuilder("resources", "Capital resources")
        resources.column("label", "Component", "text")
        resources.column("tier", "Tier", "text")
        resources.column("internal", "Internal", "amount")
        resources.column("regulatory", "Regulatory", "amount")
        resources.column("recognised", "Recognised", "amount")
        for line in read.resources.lines:
            resources.row(
                {
                    "label": line.label,
                    "tier": line.tier,
                    "internal": line.internal_amount,
                    "regulatory": line.regulatory_amount,
                    "recognised": line.recognised_amount,
                }
            )
        totals = read.resources.totals
        facts = {
            "total_internal_requirement": resolvers.fact(
                spec,
                "total_internal_requirement",
                read.requirement.totals.total_internal_requirement,
                currency=rc.currency,
            ),
            "total_regulatory_requirement": resolvers.fact(
                spec,
                "total_regulatory_requirement",
                read.requirement.totals.total_regulatory_requirement,
                currency=rc.currency,
            ),
            "available_internal_capital": resolvers.fact(
                spec,
                "available_internal_capital",
                totals.available_internal_capital,
                currency=rc.currency,
            ),
            "recognised_regulatory_capital": resolvers.fact(
                spec,
                "recognised_regulatory_capital",
                totals.recognised_regulatory_capital,
                currency=rc.currency,
            ),
            "internal_capital_surplus": resolvers.fact(
                spec,
                "internal_capital_surplus",
                totals.internal_capital_surplus,
                currency=rc.currency,
            ),
            "internal_capital_coverage_pct": resolvers.fact(
                spec, "internal_capital_coverage_pct", totals.internal_capital_coverage_pct
            ),
        }
        return _resolution(
            rc, self.block_type, body, facts, [requirement.build(), resources.build()]
        )


class CapitalAllocationResolver:
    block_type = "capital_allocation"
    version = _VERSION

    def _read(self, rc: ResolveContext):
        from app.services.icaap import allocation as service  # noqa: PLC0415

        return service.get_allocation(rc.db, rc.access, rc.cycle.id)

    def probe(self, rc: ResolveContext) -> SourceProbe:
        from app.services.icaap import allocation as service  # noqa: PLC0415

        return _probe(rc, self.block_type, service.allocation_payload(self._read(rc)))

    def resolve(self, rc: ResolveContext) -> Resolution:
        from app.services.icaap import allocation as service  # noqa: PLC0415

        read = self._read(rc)
        if not read.cells:
            raise Unavailable(
                "Internal capital has not been allocated yet. Set the allocation drivers, "
                "then link this figure again."
            )
        spec = rc.spec
        body = service.allocation_payload(read)
        table = resolvers.TableBuilder("allocation", "Internal capital allocation")
        table.column("unit", "Unit", "text")
        table.column("line", "Risk line", "text")
        table.column("driver", "Driver", "text")
        table.column("amount", "Allocated", "amount")
        for cell in read.cells:
            table.row(
                {
                    "unit": cell.unit_key,
                    "line": cell.risk_line_key,
                    "driver": f"{cell.driver_kind} {cell.driver_value}",
                    "amount": cell.allocated_amount,
                }
            )
        facts = {
            "allocation_unit_count": resolvers.fact(spec, "allocation_unit_count", len(read.units))
        }
        return _resolution(rc, self.block_type, body, facts, [table.build()])


class CapitalTriggersResolver:
    block_type = "capital_triggers"
    version = _VERSION

    def _read(self, rc: ResolveContext):
        from app.services.icaap import capital_triggers as service  # noqa: PLC0415

        return service.evaluate(rc.db, rc.access, rc.cycle.id)

    def probe(self, rc: ResolveContext) -> SourceProbe:
        from app.services.icaap import capital_triggers as service  # noqa: PLC0415

        return _probe(rc, self.block_type, service.trigger_payload(self._read(rc)))

    def resolve(self, rc: ResolveContext) -> Resolution:
        from app.services.icaap import capital_triggers as service  # noqa: PLC0415

        read = self._read(rc)
        if read.unavailable is not None:
            raise Unavailable(str(read.unavailable.get("message", "No capital plan is linked.")))
        spec = rc.spec
        body = service.trigger_payload(read)
        table = resolvers.TableBuilder("triggers", "Capital plan triggers")
        table.column("metric", "Metric", "text")
        table.column("early_warning", "Early warning", "ratio_pct")
        table.column("action", "Action", "ratio_pct")
        table.column("current", "Current", "ratio_pct")
        table.column("status", "Status", "text")
        for entry in read.results:
            table.row(
                {
                    "metric": entry.metric_key or entry.metric_code,
                    "early_warning": entry.early_warning_level,
                    "action": entry.action_level,
                    "current": entry.current_value,
                    "status": entry.current_status,
                }
            )
        facts = {
            "trigger_count": resolvers.fact(spec, "trigger_count", read.trigger_count),
            "triggers_breached_now": resolvers.fact(
                spec, "triggers_breached_now", read.triggers_breached_now
            ),
            "first_action_year": resolvers.fact(spec, "first_action_year", read.first_action_year),
        }
        return _resolution(rc, self.block_type, body, facts, [table.build()])


class AuditReviewResolver:
    block_type = "audit_review"
    version = _VERSION

    def _read(self, rc: ResolveContext):
        from app.services.icaap import audit_reviews as service  # noqa: PLC0415

        return service.list_reviews(rc.db, rc.access, rc.cycle.id)

    def probe(self, rc: ResolveContext) -> SourceProbe:
        from app.services.icaap import audit_reviews as service  # noqa: PLC0415

        return _probe(rc, self.block_type, service.review_payload(self._read(rc)))

    def resolve(self, rc: ResolveContext) -> Resolution:
        from app.services.icaap import audit_reviews as service  # noqa: PLC0415

        read = self._read(rc)
        spec = rc.spec
        body = service.review_payload(read)
        table = resolvers.TableBuilder("reviews", "Independent review")
        table.column("kind", "Review", "text")
        table.column("function", "Performed by", "text")
        table.column("performed_on", "Completed", "date")
        table.column("opinion", "Opinion", "text")
        table.column("open_findings", "Open findings", "count")
        for review in read.reviews:
            if review.status != "finalised":
                continue
            table.row(
                {
                    "kind": review.review_kind,
                    "function": review.reviewer_function,
                    "performed_on": review.performed_on,
                    "opinion": review.overall_opinion,
                    "open_findings": review.open_findings_count,
                }
            )
        facts = {
            "latest_review_date": resolvers.fact(
                spec, "latest_review_date", read.latest_review_date
            ),
            "latest_review_opinion": resolvers.fact(
                spec, "latest_review_opinion", read.latest_review_opinion
            ),
            "open_findings_count": resolvers.fact(
                spec, "open_findings_count", read.open_findings_count
            ),
        }
        return _resolution(rc, self.block_type, body, facts, [table.build()])


class ChallengeLogResolver:
    block_type = "challenge_log"
    version = _VERSION

    def _read(self, rc: ResolveContext):
        from app.services.icaap import challenges as service  # noqa: PLC0415

        return service.list_challenges(rc.db, rc.access, rc.cycle.id)

    def probe(self, rc: ResolveContext) -> SourceProbe:
        from app.services.icaap import challenges as service  # noqa: PLC0415

        return _probe(rc, self.block_type, service.challenge_payload(self._read(rc)))

    def resolve(self, rc: ResolveContext) -> Resolution:
        from app.services.icaap import challenges as service  # noqa: PLC0415

        read = self._read(rc)
        spec = rc.spec
        body = service.challenge_payload(read)
        table = resolvers.TableBuilder("challenges", "Challenge and adoption log")
        table.column("no", "No.", "count")
        table.column("forum", "Raised in", "text")
        table.column("raised_on", "Date", "date")
        table.column("challenge", "Challenge", "text")
        table.column("outcome", "Outcome", "text")
        table.column("response", "Response", "text")
        for challenge in read.challenges:
            latest = challenge.responses[-1] if challenge.responses else None
            table.row(
                {
                    "no": challenge.challenge_no,
                    "forum": challenge.raised_in,
                    "raised_on": challenge.raised_on,
                    "challenge": challenge.challenge_text,
                    "outcome": None if latest is None else latest.outcome,
                    "response": None if latest is None else latest.response_text,
                }
            )
        facts = {
            "challenge_count": resolvers.fact(spec, "challenge_count", read.challenge_count),
            "open_challenge_count": resolvers.fact(
                spec, "open_challenge_count", read.open_challenge_count
            ),
            "board_challenge_count": resolvers.fact(
                spec, "board_challenge_count", read.board_challenge_count
            ),
        }
        return _resolution(rc, self.block_type, body, facts, [table.build()])


class SupervisoryAddonsResolver:
    """Never public. The regulator's instruction to one bank is not disclosure."""

    block_type = "supervisory_addons"
    version = _VERSION

    def _body(self, rc: ResolveContext):
        from app.services.icaap import supervisory_addons as service  # noqa: PLC0415

        return service.addons_payload(rc.db, rc.access, rc.cycle)

    def probe(self, rc: ResolveContext) -> SourceProbe:
        body, _total = self._body(rc)
        return _probe(rc, self.block_type, body)

    def resolve(self, rc: ResolveContext) -> Resolution:
        body, total = self._body(rc)
        spec = rc.spec
        table = resolvers.TableBuilder("addons", "Supervisory capital add-ons")
        table.column("letter", "Letter", "text")
        table.column("effective_from", "In force from", "date")
        table.column("row", "Risk", "text")
        table.column("basis", "Basis", "text")
        table.column("amount", "Amount", "amount")
        for entry in body["addons"]:
            table.row(
                {
                    "letter": entry["letter_reference"],
                    "effective_from": entry["effective_from"],
                    "row": entry["table5_row"],
                    "basis": f"{entry['basis_value']} {entry['basis']}",
                    "amount": entry["amount"],
                }
            )
        facts = {
            "supervisory_addon_total": resolvers.fact(
                spec, "supervisory_addon_total", total, currency=rc.currency
            )
        }
        return _resolution(
            rc,
            self.block_type,
            body,
            facts,
            [table.build()],
            notes=["Imposed by the supervisor. Never published."],
        )


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


__all__ = [
    "AuditReviewResolver",
    "CapitalAllocationResolver",
    "CapitalReconciliationResolver",
    "CapitalTriggersResolver",
    "ChallengeLogResolver",
    "Pillar2SummaryResolver",
    "RiskAppetiteResolver",
    "RiskRegisterResolver",
    "SupervisoryAddonsResolver",
    "Table5Pillar2Resolver",
]
