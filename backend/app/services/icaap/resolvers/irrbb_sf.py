"""The IRRBB Standardised Framework, from a sealed ``irr_sf`` run (P5-DESIGN §1.6).

This is the block the Pillar 2 standardised-framework method reads, so the
economic-value measure a bank capitalises and the Table 8 grid printed beside
it in section (h) come from the SAME sealed run. The legacy ``irrbb`` block
stays exactly as it was: it reports the interim engine's deltas and draws no
outlier conclusion (D-013), and the two blocks never share a source.

Three properties are load-bearing.

**The refusal is carried, not smoothed.** A book holding interest-rate options
refuses the whole measurement under one name — ``irrbb_sf_options_unsupported``
(D-061 / DV-010) — because a standardised ΔEVE computed without valuing those
options would be understated, and an understated capital number that looks
complete is worse than none. When the newest attempt for this date refused,
this resolver reports that refusal in words instead of binding a partial run or
offering a zero.

**Representative calibrations and modelling defaults stay counted.** The run
tallies every APPLICATION of a default, not merely whether one was used: a
profile assumed for forty positions is a different exposure from one assumed
for a single position. The counts become facts, the per-marker breakdown is a
payload table, and neither is flattened on its way into the register.

**Nothing here resolves a governed parameter.** The mandate the block reports
is the one the RUN recorded, read straight off ``run.metrics``. Resolving it
again on a read path would leave the row in the session's consumption ledger
for whatever run is sealed next, which is the ambient-ledger defect D-078
closed.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.icaap.blocks import SourceProbe
from app.domain.irr import standardised as sf
from app.domain.irr import standardised_params as sfp
from app.models import RegulatoryRun
from app.services import regulatory_irr_sf
from app.services.icaap import resolvers
from app.services.icaap.resolvers import Resolution, ResolveContext, Unavailable

_VERSION = "1"

BLOCK_TYPE = "irrbb_sf"

#: The two shapes the disclosure grid names explicitly; every other shape is
#: reported in the Table 8 payload and folded into the ``_max`` figures.
_HEADLINE_SCENARIOS: tuple[str, ...] = ("parallel_up", "parallel_down")

_SIGN_CONVENTION = (
    "Changes in economic value and in net interest income are reported as LOSSES: a "
    "positive figure is a reduction under that scenario, measured against the base case."
)

#: Plain language for each typed refusal a Standardised Framework run can carry.
#: Keyed by the ONE canonical code (D-061); an unknown code falls back to the
#: run's own message rather than to a guess.
#: Re-exported, not restated. The sentence a preparer reads for a refusal now
#: lives beside the engine that raises it (``regulatory_irr_sf``), so the IRRBB
#: workspace, the attempt history and this ICAAP block cannot describe one
#: refusal three different ways. The names stay here because callers import
#: them from here.
REFUSAL_COPY: Mapping[str, str] = regulatory_irr_sf.REFUSAL_COPY


def _decimal(raw: Any) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


def _run(rc: ResolveContext) -> RegulatoryRun | None:
    if rc.period is None:
        return None
    return regulatory_irr_sf.latest_sf_run(rc.db, rc.access.ctx, rc.access.bank, rc.period)


def latest_refusal(rc: ResolveContext) -> tuple[str, str] | None:
    """The newest attempt's refusal, when it is newer than the newest result.

    A bank that ran the framework and was refused must be told WHY on the card,
    not handed "no run exists" — which reads as "nobody has tried".
    """
    if rc.period is None:
        return None
    attempt = regulatory_irr_sf.latest_sf_attempt(rc.db, rc.access.ctx, rc.access.bank, rc.period)
    if attempt is None or attempt.status != "failed" or not attempt.error_code:
        return None
    return attempt.error_code, attempt.error_message or ""


def refusal_sentence(code: str, message: str) -> str:
    """One sentence a preparer can act on, whatever the refusal was."""
    return regulatory_irr_sf.refusal_sentence(code, message)


def _scenario_label(code: str | None) -> str | None:
    if code is None:
        return None
    return sfp.SCENARIO_LABELS.get(code, code)


def _table8_by_code(metrics: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows: dict[str, Mapping[str, Any]] = {}
    for row in metrics.get("table8") or []:
        if isinstance(row, Mapping) and row.get("code") is not None:
            rows[str(row["code"])] = row
    return rows


def _worst(rows: Mapping[str, Mapping[str, Any]], key: str) -> Decimal | None:
    """The largest reported loss across the six prescribed shapes."""
    values = [
        value
        for code in sfp.SCENARIOS
        if (value := _decimal((rows.get(code) or {}).get(key))) is not None
    ]
    return max(values) if values else None


class IrrbbSfResolver:
    block_type = BLOCK_TYPE
    version = _VERSION

    def probe(self, rc: ResolveContext) -> SourceProbe:
        if rc.period is None:
            return SourceProbe(current_key=None, reason="no reporting period for the as-of date")
        run = _run(rc)
        if run is None:
            refused = latest_refusal(rc)
            reason = (
                "no succeeded standardised framework run"
                if refused is None
                else refusal_sentence(*refused)
            )
            return SourceProbe(current_key=None, reason=reason)
        return SourceProbe(
            current_key=f"run:{run.id}", withdrawn=resolvers.run_withdrawn(rc.db, run)
        )

    def resolve(self, rc: ResolveContext) -> Resolution:  # noqa: PLR0915 - one payload, named
        if rc.period is None:
            raise resolvers.no_period(rc)
        run = _run(rc)
        if run is None:
            refused = latest_refusal(rc)
            if refused is not None:
                raise Unavailable(
                    refusal_sentence(*refused)
                    + " Quantify this risk another way, or agree the treatment of those "
                    "positions with the supervisor."
                )
            raise Unavailable(
                "No standardised framework result exists for "
                f"{rc.cycle.as_of_date.isoformat()}. Run the framework for that reporting "
                "period, then link this figure again."
            )
        spec = rc.spec
        metrics: Mapping[str, Any] = run.metrics or {}
        measures: Mapping[str, Any] = metrics.get("measures") or {}
        outlier_set: Mapping[str, Any] = measures.get(sf.MEASURE_OUTLIER_SET) or {}
        table8 = _table8_by_code(metrics)
        mandate: Mapping[str, Any] = metrics.get("mandate") or {}
        table7: Mapping[str, Any] = metrics.get("table7_quantitative") or {}
        tallies: Mapping[str, Any] = metrics.get("assumption_tallies") or {}
        representative = [str(code) for code in metrics.get("representative_parameters") or []]
        pending = [str(code) for code in metrics.get("parameters_pending_confirmation") or []]
        in_scope = [
            str(entry.get("currency"))
            for entry in metrics.get("currencies") or []
            if isinstance(entry, Mapping) and entry.get("material")
        ]

        tables = [
            self._table8_table(metrics),
            self._currency_table(metrics),
            self._nmd_table(metrics),
            self._assumption_table(tallies, metrics),
            self._parameter_table(run),
        ]

        facts: dict[str, dict[str, Any]] = {
            "eve_risk_measure": resolvers.fact(
                spec, "eve_risk_measure", outlier_set.get("measure"), currency=rc.currency
            ),
            "eve_risk_measure_pct_tier1": resolvers.fact(
                spec, "eve_risk_measure_pct_tier1", metrics.get("pct_tier1")
            ),
            "outlier_threshold_pct": resolvers.fact(
                spec, "outlier_threshold_pct", metrics.get("outlier_threshold_pct")
            ),
            "outlier": resolvers.fact(spec, "outlier", metrics.get("outlier")),
            "tier1": resolvers.fact(spec, "tier1", metrics.get("tier1"), currency=rc.currency),
            "worst_scenario": resolvers.fact(
                spec, "worst_scenario", _scenario_label(outlier_set.get("worst_scenario"))
            ),
            "sf_mandatory": resolvers.fact(spec, "sf_mandatory", mandate.get("mandatory")),
            "currencies_in_scope": resolvers.fact(
                spec, "currencies_in_scope", ", ".join(in_scope) or None
            ),
            "nmd_avg_repricing_maturity_years": resolvers.fact(
                spec,
                "nmd_avg_repricing_maturity_years",
                table7.get("average_repricing_maturity_years"),
            ),
            "nmd_longest_repricing_maturity_years": resolvers.fact(
                spec,
                "nmd_longest_repricing_maturity_years",
                table7.get("longest_repricing_maturity_years"),
            ),
            "parameters_pending_confirmation": resolvers.fact(
                spec, "parameters_pending_confirmation", len(pending)
            ),
            "representative_parameters": resolvers.fact(
                spec, "representative_parameters", len(representative)
            ),
            "assumption_defaults_applied": resolvers.fact(
                spec,
                "assumption_defaults_applied",
                sum(int(count) for count in tallies.values()),
            ),
        }
        for measure_key, column in (("delta_eve", "delta_eve"), ("delta_nii", "delta_nii")):
            for scenario in _HEADLINE_SCENARIOS:
                row = table8.get(scenario) or {}
                facts[f"{measure_key}_{scenario}"] = resolvers.fact(
                    spec, f"{measure_key}_{scenario}", row.get(column), currency=rc.currency
                )
                facts[f"{measure_key}_{scenario}_prior"] = resolvers.fact(
                    spec,
                    f"{measure_key}_{scenario}_prior",
                    row.get(f"{column}_prior"),
                    currency=rc.currency,
                )
            facts[f"{measure_key}_max"] = resolvers.fact(
                spec, f"{measure_key}_max", _worst(table8, column), currency=rc.currency
            )
            facts[f"{measure_key}_max_prior"] = resolvers.fact(
                spec,
                f"{measure_key}_max_prior",
                _worst(table8, f"{column}_prior"),
                currency=rc.currency,
            )

        body = resolvers.payload(
            title=spec.title,
            as_of=rc.cycle.as_of_date,
            source_label=(
                f"Official IRRBB standardised framework run · {rc.cycle.as_of_date.isoformat()}"
            ),
            currency=rc.currency,
            tables=tables,
            notes=self._notes(metrics, mandate, outlier_set, representative),
            # The machine-readable half the Pillar 2 method reads. The tables
            # above are for a reader, and a reader's table has labels in it;
            # the method needs the MARKERS and the parameter CODES, and it must
            # get them from the binding this cycle pinned rather than by
            # reaching back into the engine for a run that may have moved on.
            raw={
                "irrbb_sf": {
                    "run_id": str(run.id),
                    "input_hash": run.input_hash,
                    "measure_set": sf.MEASURE_OUTLIER_SET,
                    "scenarios_in_measure": [
                        str(code) for code in outlier_set.get("scenarios") or []
                    ],
                    "worst_scenario": outlier_set.get("worst_scenario"),
                    "assumption_tallies": {
                        str(marker): int(count)
                        for marker, count in sorted(tallies.items())
                        if int(count) > 0
                    },
                    "representative_parameters": sorted(representative),
                    "parameters_pending_confirmation": sorted(pending),
                }
            },
        )
        return Resolution(
            source_kind="run",
            source_ref={
                "run_id": str(run.id),
                "input_hash": run.input_hash,
                "engine_version": run.engine_version,
                "scenario_code": run.scenario_code,
                "measure_set": sf.MEASURE_OUTLIER_SET,
            },
            source_key=f"run:{run.id}",
            source_as_of=rc.cycle.as_of_date,
            payload=body,
            facts=facts,
            source_run_ids=(str(run.id),),
        )

    # --- payload tables ---------------------------------------------------

    def _table8_table(self, metrics: Mapping[str, Any]) -> dict[str, Any]:
        table = resolvers.TableBuilder(
            "table8", "Changes in economic value and earnings, by scenario"
        )
        table.column("scenario", "Scenario", "text")
        table.column("delta_eve", "Change in economic value", "amount")
        table.column("delta_eve_prior", "Prior period", "amount")
        table.column("delta_nii", "Change in net interest income", "amount")
        table.column("delta_nii_prior", "Prior period", "amount")
        for row in metrics.get("table8") or []:
            if not isinstance(row, Mapping):
                continue
            table.row(
                {
                    "scenario": row.get("label"),
                    "delta_eve": row.get("delta_eve"),
                    "delta_eve_prior": row.get("delta_eve_prior"),
                    "delta_nii": row.get("delta_nii"),
                    "delta_nii_prior": row.get("delta_nii_prior"),
                }
            )
        return table.build()

    def _currency_table(self, metrics: Mapping[str, Any]) -> dict[str, Any]:
        table = resolvers.TableBuilder(
            "delta_eve_by_currency", "Change in economic value by currency and scenario"
        )
        table.column("currency", "Currency", "text")
        table.column("scenario", "Scenario", "text")
        table.column("delta_eve", "Change in economic value", "amount")
        for scenario in metrics.get("scenarios") or []:
            if not isinstance(scenario, Mapping):
                continue
            for row in scenario.get("by_currency") or []:
                if not isinstance(row, Mapping):
                    continue
                table.row(
                    {
                        "currency": row.get("currency"),
                        "scenario": scenario.get("label"),
                        "delta_eve": row.get("delta_eve_reporting"),
                    }
                )
        return table.build()

    def _nmd_table(self, metrics: Mapping[str, Any]) -> dict[str, Any]:
        table = resolvers.TableBuilder("nmd_categories", "Non-maturing deposits")
        table.column("currency", "Currency", "text")
        table.column("category", "Category", "text")
        table.column("balance", "Balance", "amount")
        table.column("core", "Core", "amount")
        table.column("non_core", "Non-core", "amount")
        table.column("core_cap_pct", "Supervisory core cap", "ratio_pct")
        table.column("average_core_maturity_years", "Average core maturity", "years")
        table.column("longest_core_maturity_years", "Longest core maturity", "years")
        for row in metrics.get("nmd_disclosure") or []:
            if not isinstance(row, Mapping):
                continue
            category = str(row.get("category"))
            table.row(
                {
                    "currency": row.get("currency"),
                    "category": regulatory_irr_sf.NMD_CATEGORY_LABELS.get(category, category),
                    "balance": row.get("balance"),
                    "core": row.get("core"),
                    "non_core": row.get("non_core"),
                    "core_cap_pct": row.get("core_cap_pct"),
                    "average_core_maturity_years": row.get("average_core_maturity_years"),
                    "longest_core_maturity_years": row.get("longest_core_maturity_years"),
                },
                emphasis="warning" if row.get("cap_binding") else None,
            )
        return table.build()

    def _assumption_table(
        self, tallies: Mapping[str, Any], metrics: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Every default and every exclusion, with the COUNT of applications.

        The count is the point: it is what tells a reader how much of the book
        rests on an assumption rather than on stated terms, and it pre-fills
        the framework's own Table 7(f) assumptions disclosure.
        """
        table = resolvers.TableBuilder(
            "assumption_tallies", "Modelling assumptions and excluded positions"
        )
        table.column("kind", "Kind", "text")
        table.column("assumption", "What was assumed", "text")
        table.column("positions", "Positions", "count")
        table.column("amount", "Amount", "amount")
        for marker, count in sorted(tallies.items()):
            if int(count) <= 0:
                continue
            table.row(
                {
                    "kind": "Modelling default",
                    "assumption": regulatory_irr_sf.ASSUMPTION_LABELS.get(
                        str(marker), str(marker)
                    ),
                    "positions": count,
                    "amount": None,
                }
            )
        exclusions = metrics.get("exclusions") or {}
        for marker, entry in sorted(exclusions.items()):
            if not isinstance(entry, Mapping):
                continue
            table.row(
                {
                    "kind": "Excluded position",
                    "assumption": regulatory_irr_sf.EXCLUSION_LABELS.get(
                        str(marker), str(marker)
                    ),
                    "positions": entry.get("count"),
                    "amount": entry.get("amount_reporting"),
                }
            )
        return table.build()

    def _parameter_table(self, run: RegulatoryRun) -> dict[str, Any]:
        """The governed rows the run consumed, from the run's own provenance."""
        table = resolvers.TableBuilder("parameter_provenance", "Governed figures used")
        table.column("parameter", "Figure", "text")
        table.column("value", "Value", "text")
        table.column("status", "Status", "text")
        table.column("source", "Source", "text")
        values = (run.inputs or {}).get("parameters") or {}
        for entry in sorted(
            run.parameter_provenance or [], key=lambda row: str(row.get("param_code"))
        ):
            code = str(entry.get("param_code"))
            if code not in sfp.REQUIRED_CODES and code != regulatory_irr_sf.CODE_MANDATORY_FROM:
                continue
            citation = str(entry.get("source_citation") or "")
            representative = (
                code in sfp.REPRESENTATIVE_CODES
                or sfp.REPRESENTATIVE_MARKER in citation.upper()
            )
            statuses: list[str] = []
            if representative:
                statuses.append("Representative calibration, not a published benchmark")
            if str(entry.get("confirmation_status") or "") == "pending":
                statuses.append("Awaiting stakeholder confirmation")
            raw = values.get(code, entry.get("value"))
            table.row(
                {
                    "parameter": sfp.PARAMETER_LABELS.get(code, code),
                    "value": regulatory_irr_sf.parameter_value_text(raw),
                    "status": "; ".join(statuses) or "Approved",
                    "source": citation,
                },
                emphasis="warning" if representative else None,
            )
        return table.build()

    def _notes(
        self,
        metrics: Mapping[str, Any],
        mandate: Mapping[str, Any],
        outlier_set: Mapping[str, Any],
        representative: list[str],
    ) -> list[str]:
        notes = [_SIGN_CONVENTION]
        scenarios = [
            sfp.SCENARIO_LABELS.get(str(code), str(code))
            for code in outlier_set.get("scenarios") or []
        ]
        if scenarios:
            notes.append(
                "The economic value risk measure is the largest loss across "
                f"{', '.join(scenarios)}, measured against Tier 1 capital."
            )
        statement = str(mandate.get("statement") or "")
        if statement:
            notes.append(statement)
        options = str(metrics.get("automatic_option_statement") or "")
        if options:
            notes.append(options)
        floor = regulatory_irr_sf.post_shock_floor_statement(
            str(metrics.get("post_shock_floor") or "")
        )
        if floor:
            notes.append(floor)
        notes.extend(str(text) for text in metrics.get("statements") or [])
        if representative:
            labels = ", ".join(
                sfp.PARAMETER_LABELS.get(code, code) for code in sorted(representative)
            )
            notes.append(
                "These figures rest on a representative calibration rather than a published "
                f"supervisory benchmark: {labels}."
            )
        return notes


__all__ = ["BLOCK_TYPE", "REFUSAL_COPY", "IrrbbSfResolver", "latest_refusal", "refusal_sentence"]
