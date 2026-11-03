"use client";

import { useState } from "react";
import type {
  ScenarioCatalogueRead,
  ScenarioRefIn,
  ScenarioResultRead,
} from "@aequoros/risk-service-api";
import { wholeInstitutionCapabilities } from "@/lib/api/dataScope";
import { useBankContext } from "@/components/shell/BankContext";
import SectionCard from "@/components/ui/SectionCard";
import QueryBoundary from "@/components/ui/QueryBoundary";
import { DisabledWithReason } from "@/components/ui/DisabledWithReason";
import { inputCls } from "@/components/institution/shared";
import { hasEffectiveCapability } from "@/lib/modules";
import { fmtDateUTC } from "@/lib/api/values";
import ScenarioMetrics, {
  scenarioLabel,
  scenarioRisk,
  scenarioValue,
} from "@/features/workbench/components/ScenarioMetrics";
import {
  useRunScenarioAnalysis,
  useSaveScenarioAnalysis,
  useSavedAnalyses,
  useSavedScenarioAnalysis,
} from "@/lib/api/hooks";

const FIELDS = [
  ["curve_shift_bp", "Curve shift (bp)"],
  ["funding_spread_add_bps", "Funding spread addition (bp)"],
  ["min_product_margin_pct", "Product margin floor (%)"],
  ["nmd_core_min_pct", "NMD core minimum (%)"],
  ["nmd_core_max_pct", "NMD core maximum (%)"],
] as const;

export function ScenarioResults({
  results,
}: {
  results: ScenarioResultRead[];
}) {
  return (
    <div className="space-y-4">
      {results.map((result, index) => (
        <article
          key={`${result.code}:${index}`}
          className="rounded border border-border-light p-4"
        >
          <h3 className="font-medium">
            {index === 0 ? "Baseline" : "What-if"} · {result.label} ·{" "}
            {result.status}
          </h3>
          {result.errorMessage && <p role="alert">{result.errorMessage}</p>}
          <p className="my-3 text-body text-navy">
            {scenarioRisk(result.metrics ?? {}, result.metricStatuses ?? {})}
          </p>
          <ScenarioMetrics
            metrics={result.metrics ?? {}}
            baseline={
              index > 0 && results[0]?.status === "succeeded"
                ? (results[0].metrics ?? undefined)
                : undefined
            }
            statuses={result.metricStatuses ?? {}}
            refusals={result.refusals ?? {}}
          />
          <details className="mt-2 text-caption">
            <summary>Applied inputs and validations</summary>
            <dl>
              {Object.entries(result.shocksApplied).map(([key, value]) => (
                <div key={key}>
                  <dt className="inline">{scenarioLabel(key)}: </dt>
                  <dd className="inline">{scenarioValue(key, value)}</dd>
                </div>
              ))}
            </dl>
            <ul>
              {(result.validations ?? []).map((validation) => (
                <li key={validation.ruleCode}>
                  {validation.message} ·{" "}
                  {validation.passed ? "Passed" : "Failed"}
                </li>
              ))}
            </ul>
          </details>
        </article>
      ))}
    </div>
  );
}

export default function FtpWhatIf({
  catalogue,
}: {
  catalogue: ScenarioCatalogueRead | undefined;
}) {
  const { bank, period, institutionCapabilities, authorityPending } =
    useBankContext();
  const run = useRunScenarioAnalysis(bank?.id, "ftp");
  const save = useSaveScenarioAnalysis(bank?.id, "ftp");
  const saved = useSavedAnalyses(bank?.id, "ftp");
  const [selectedAnalysis, setSelectedAnalysis] = useState<string>();
  const detail = useSavedScenarioAnalysis(bank?.id, "ftp", selectedAnalysis);
  const [selected, setSelected] = useState("system:baseline");
  const [values, setValues] = useState<Record<string, string>>({});
  const [name, setName] = useState("");
  const [message, setMessage] = useState("");
  const [preview, setPreview] = useState<{
    signature: string;
    results: ScenarioResultRead[];
  }>();
  const canRun =
    !authorityPending &&
    hasEffectiveCapability(
      wholeInstitutionCapabilities(institutionCapabilities),
      "ftp",
      "confidential",
      "run",
    );
  const canCreate =
    !authorityPending &&
    hasEffectiveCapability(
      wholeInstitutionCapabilities(institutionCapabilities),
      "ftp",
      "confidential",
      "create",
    );
  const entry = catalogue?.scenarios.find(
    (item) =>
      `${item.kind}:${item.kind === "system" ? item.code : item.scenarioId}` ===
      selected,
  );
  const overrides: Record<string, string> = Object.fromEntries(
    Object.entries(values).filter(([, value]) => value.trim() !== ""),
  );
  const validInputs = Object.values(overrides).every((value) =>
    /^-?\d+(\.\d+)?$/.test(value),
  );
  const reference: ScenarioRefIn =
    entry?.kind === "custom"
      ? { kind: "custom", scenarioId: entry.scenarioId, overrides }
      : { kind: "system", code: entry?.code ?? "baseline", overrides };
  const signature = JSON.stringify([period?.id, reference]);
  const busy = run.isPending || save.isPending;
  const ready = !!period && !!entry && entry.configured && validInputs;

  async function execute(action: "run" | "save") {
    if (!ready || !period || (action === "run" ? !canRun : !canCreate)) return;
    if (action !== "run" && !name.trim()) return;
    setMessage("");
    try {
      if (action === "run") {
        const result = await run.mutateAsync({
          reportingPeriodId: period.id,
          scenarios: [{ kind: "system", code: "baseline" }, reference],
        });
        setPreview({ signature, results: result.results });
      } else {
        const result = await save.mutateAsync({
          reportingPeriodId: period.id,
          name: name.trim(),
          scenarios: [{ kind: "system", code: "baseline" }, reference],
        });
        setSelectedAnalysis(result.id);
        setMessage("Analysis recomputed and saved with these inputs.");
      }
    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : "FTP analysis could not be completed.",
      );
    }
  }
  const control = (action: "run" | "save", text: string) => {
    const permitted = action === "run" ? canRun : canCreate;
    const disabled =
      !permitted || !ready || busy || (action !== "run" && !name.trim());
    const button = (id?: string) => (
      <button
        type="button"
        className="btn-primary min-h-11 rounded px-5 py-2 font-medium disabled:opacity-50"
        aria-describedby={id}
        disabled={disabled}
        onClick={() => void execute(action)}
      >
        {text}
      </button>
    );
    return permitted ? (
      button()
    ) : (
      <DisabledWithReason
        reason={`Requires FTP · Confidential · ${action === "run" ? "Run" : "Create"} authority.`}
      >
        {button}
      </DisabledWithReason>
    );
  };
  return (
    <SectionCard
      title="FTP curve and rules what-if"
      subtitle="Compare against baseline using the selected period's book. Blank overrides retain the scenario's values and governed rules."
    >
      <div className="space-y-4">
        <label className="block text-caption">
          Base FTP scenario
          <select
            className={inputCls}
            value={selected}
            onChange={(event) => setSelected(event.target.value)}
          >
            {catalogue?.scenarios
              .filter((item) => !item.isArchived)
              .map((item) => (
                <option
                  key={`${item.kind}:${item.code}`}
                  value={`${item.kind}:${item.kind === "system" ? item.code : item.scenarioId}`}
                >
                  {item.name}
                  {!item.configured ? " · Parameters missing" : ""}
                </option>
              ))}
          </select>
        </label>
        <div className="grid gap-3 sm:grid-cols-2">
          {FIELDS.map(([key, title]) => (
            <label key={key} className="text-caption">
              {title}
              <input
                className={inputCls}
                inputMode="decimal"
                value={values[key] ?? ""}
                onChange={(event) =>
                  setValues({ ...values, [key]: event.target.value })
                }
              />
            </label>
          ))}
        </div>
        <p className="text-caption text-slate">
          As of {period ? fmtDateUTC(period.periodEnd) : "no period selected"}.
          Preview writes no regulatory runs. Saving recomputes a separate
          analysis snapshot; governed curves and rules stay unchanged.
        </p>
        {!ready && (
          <p className="text-caption text-slate">
            {!period
              ? "Select a reporting period before previewing."
              : !entry?.configured
                ? "This scenario cannot run until its governed parameters are configured."
                : "Correct the override values before previewing."}
          </p>
        )}
        {!validInputs && (
          <p role="alert">Enter decimal numbers for each override.</p>
        )}
        <div>{control("run", busy ? "Computing…" : "Preview FTP what-if")}</div>
        {preview?.signature === signature && (
          <ScenarioResults results={preview.results} />
        )}
        {preview && preview.signature !== signature && (
          <p role="status">
            Inputs changed. Preview again to refresh the comparison.
          </p>
        )}
        <label className="block text-caption">
          Analysis name
          <input
            maxLength={200}
            className={inputCls}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <div className="flex flex-wrap gap-3">
          {control("save", "Save FTP analysis")}
        </div>
        {message && <p role="status">{message}</p>}
        <QueryBoundary
          isLoading={saved.isLoading}
          error={saved.error}
          onRetry={() => saved.refetch()}
        >
          <label className="block text-caption">
            Saved FTP analyses
            <select
              className={inputCls}
              value={selectedAnalysis ?? ""}
              onChange={(event) =>
                setSelectedAnalysis(event.target.value || undefined)
              }
            >
              <option value="">Select a saved analysis</option>
              {saved.data?.analyses
                .filter((item) => item.reportingPeriodId === period?.id)
                .map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name} · {fmtDateUTC(item.createdAt)}
                  </option>
                ))}
            </select>
          </label>
        </QueryBoundary>
        {selectedAnalysis && (
          <QueryBoundary
            isLoading={detail.isLoading}
            error={detail.error}
            onRetry={() => detail.refetch()}
          >
            {detail.data && (
              <>
                <h3 className="font-medium">Saved: {detail.data.name}</h3>
                <ScenarioResults results={detail.data.results} />
              </>
            )}
          </QueryBoundary>
        )}
      </div>
    </SectionCard>
  );
}
