"use client";

import { scenarioRisk } from "./ScenarioMetrics";
import { isApiError } from "@/lib/api/client";
import { useState } from "react";
import type {
  RegulatoryRunBatchRead,
  RegulatoryRunRead,
} from "@aequoros/risk-service-api";
import { useQueryClient } from "@tanstack/react-query";
import { wholeInstitutionCapabilities } from "@/lib/api/dataScope";
import { useBankContext } from "@/components/shell/BankContext";
import { DisabledWithReason } from "@/components/ui/DisabledWithReason";
import { hasEffectiveCapability } from "@/lib/modules";
import { fmtDateUTC, labelize } from "@/lib/api/values";
import {
  useRunAllLiquidityScenarios,
  useRunAllCapitalScenarios,
  useRunAllFxScenarios,
  useRunAllCreditScenarios,
} from "@/lib/api/hooks";
import { invalidateScopedPrefixes } from "@/lib/api/queryPolicy";
import { useQueryAuthorityScope } from "@/lib/api/useQueryScope";

type Module = "liquidity" | "capital" | "fx" | "credit";
const POLICY = {
  liquidity: { authority: "liq", label: "Liquidity" },
  capital: { authority: "cap", label: "Capital" },
  fx: { authority: "fx", label: "FX" },
  credit: { authority: "credit", label: "Credit" },
} as const;

/** Explicit command: never run on mount, reads, or reporting-period selection. */
export default function RunModuleScenarios({ module }: { module: Module }) {
  const {
    bank,
    period,
    periods,
    setPeriodId,
    institutionCapabilities,
    authorityPending,
  } = useBankContext();
  const scope = useQueryAuthorityScope();
  const client = useQueryClient();
  const liquidity = useRunAllLiquidityScenarios(bank?.id);
  const capital = useRunAllCapitalScenarios(bank?.id);
  const fx = useRunAllFxScenarios(bank?.id);
  const credit = useRunAllCreditScenarios(bank?.id);
  const mutation = { liquidity, capital, fx, credit }[module];
  const policy = POLICY[module];
  const [pending, setPending] = useState(false);
  const [outcome, setOutcome] = useState<{
    signature: string;
    label: string;
    runs: RegulatoryRunRead[];
    error?: string;
  }>();
  const signature = `${bank?.id}:${period?.id}:${module}`;
  const canRun =
    !authorityPending &&
    hasEffectiveCapability(
      wholeInstitutionCapabilities(institutionCapabilities),
      policy.authority,
      "confidential",
      "run",
    );
  const reason = !canRun
    ? `Requires ${policy.label} · Confidential · Run authority for the whole institution. Ask your organization owner or admin to grant it.`
    : !bank || !period
      ? "Select an institution and an as-of reporting period."
      : undefined;
  const busy = pending || mutation.isPending;
  async function runAll() {
    if (reason || busy || !bank || !period) return;
    const label = `${period.label} · ${fmtDateUTC(period.periodEnd)}`;
    const bankId = bank.id;
    setPending(true);
    setOutcome(undefined);
    try {
      const result: RegulatoryRunBatchRead = await mutation.mutateAsync({
        reportingPeriodId: period.id,
      });
      setOutcome({ signature, label, runs: result.runs });
    } catch (error) {
      setOutcome({
        signature,
        label,
        runs: [],
        error:
          isApiError(error) && error.status === 403
            ? "The server refused this run. Refresh your scoped Run authority."
            : error instanceof Error
              ? error.message
              : `${policy.label} scenarios could not be run.`,
      });
    } finally {
      await invalidateScopedPrefixes(
        client,
        ["reg-runs", "reg-run", "scenario-catalogue"],
        scope,
        bankId,
      );
      setPending(false);
    }
  }
  const button = (descriptionId?: string) => (
    <button
      type="button"
      className="btn-primary min-h-11 rounded px-5 py-2.5 font-medium disabled:cursor-not-allowed disabled:opacity-50"
      disabled={!!reason || busy}
      aria-describedby={descriptionId}
      onClick={() => void runAll()}
    >
      {busy
        ? `Running ${policy.label} scenarios…`
        : `Run all ${policy.label} scenarios`}
    </button>
  );
  return (
    <div
      className="space-y-3"
      aria-label={`${policy.label} regulatory scenario controls`}
    >
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex flex-wrap items-center gap-2 text-caption text-slate">
          As-of reporting period
          <select
            className="rounded border border-border-light bg-surface px-3 py-2 text-navy"
            value={period?.id ?? ""}
            onChange={(event) => setPeriodId(event.target.value)}
            disabled={busy || !bank || periods.length === 0}
          >
            {!period && (
              <option value="">No reporting periods available</option>
            )}
            {periods.map((entry) => (
              <option key={entry.id} value={entry.id}>
                {entry.label} · {fmtDateUTC(entry.periodEnd)}
              </option>
            ))}
          </select>
        </label>
        {reason ? (
          <DisabledWithReason reason={reason}>{button}</DisabledWithReason>
        ) : (
          button()
        )}
        <span className="text-caption text-slate">
          Creates stored regulatory runs; current live results remain separate.
        </span>
      </div>
      {reason && <p className="text-caption text-slate">{reason}</p>}
      <p className="text-caption text-slate">
        Scenario data date:{" "}
        {period ? fmtDateUTC(period.periodEnd) : "no period selected"}. This
        selection applies to scenario runs. Overview cards show their own
        dashboard reporting date; a calculation timestamp is not a data date.
        {module === "credit" &&
          " Credit supports the baseline scenario only; no credit stress overlays are run."}
      </p>
      {outcome?.signature === signature && (
        <div
          aria-live="polite"
          className="rounded border border-border-light p-3 text-caption"
        >
          <p className="font-medium">
            {policy.label} scenario outcomes · {outcome.label}
          </p>
          {outcome.error ? (
            <p role="alert">{outcome.error}</p>
          ) : outcome.runs.length === 0 ? (
            <p>
              No scenario outcomes were returned. Reload the scenario registry
              before retrying.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left">
                <caption className="sr-only">
                  Scenario execution and risk outcomes
                </caption>
                <thead>
                  <tr>
                    <th scope="col" className="p-2">
                      Scenario
                    </th>
                    <th scope="col" className="p-2">
                      Execution
                    </th>
                    <th scope="col" className="p-2">
                      Risk assessment
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {outcome.runs.map((run) => (
                    <tr key={run.id} className="border-t border-border-light">
                      <th scope="row" className="p-2 font-medium">
                        {labelize(run.scenarioCode)}
                      </th>
                      <td className="p-2">
                        {labelize(run.status)}
                        {run.error && (
                          <p className="mt-1 text-critical">
                            {run.error.message}
                          </p>
                        )}
                      </td>
                      <td className="p-2">
                        {run.status === "succeeded"
                          ? scenarioRisk(
                              run.metrics,
                              Object.fromEntries(
                                (run.metricResults ?? []).map((metric) => [
                                  metric.metricCode,
                                  metric.status,
                                ]),
                              ),
                            )
                          : "Not assessed. Execution did not complete successfully."}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
