"use client";

/**
 * NII Forecast — net-interest-income trajectory from a persisted projection
 * path (per-year `nii` field), the full earnings bridge (NII + fees − opex −
 * credit losses → net income), and scenario sensitivity built from the latest
 * succeeded run per preset scenario.
 *
 * The tab reads one saved run, named on the page: the run picked here or
 * deep-linked with `?run=` (an edited `custom` run included), else the latest
 * base-case run. Every year axis and label follows that run's own horizon.
 */

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import PageContainer from "@/components/ui/PageContainer";
import Link from "next/link";
import { ArrowRight, TrendingUp } from "lucide-react";
import type {
  ForecastRunRead,
  ForecastRunSummaryRead,
} from "@aequoros/risk-service-api";
import PageHeader from "@/components/ui/PageHeader";
import KpiStat from "@/components/ui/KpiStat";
import Sparkline from "@/components/ui/Sparkline";
import StatusPill from "@/components/ui/StatusPill";
import EmptyState from "@/components/ui/EmptyState";
import SectionCard from "@/components/ui/SectionCard";
import ChartFrame from "@/components/ui/ChartFrame";
import DeltaBadge from "@/components/ui/DeltaBadge";
import QueryBoundary from "@/components/ui/QueryBoundary";
import EarningsChart, {
  type EarningsPoint,
} from "@/components/forecasting/charts/EarningsChart";
import ScenarioLinesChart, {
  type ScenarioPoint,
  type ScenarioSeries,
} from "@/components/forecasting/charts/ScenarioLinesChart";
import { useScenarioRunSet } from "@/components/forecasting/hooks";
import { provenanceLabel } from "@/components/forecasting/AssumptionRegister";
import { scenarioLabel, yoyPct } from "@/components/forecasting/lib";
import { SkeletonChart } from "@/components/ui/Skeleton";
import { useForecastRun, useForecastRuns } from "@/lib/api/hooks";
import { useBankContext } from "@/components/shell/BankContext";
import { num, shortId } from "@/lib/api/values";
import { fmtCurrency, fmtPct, fmtPctSigned } from "@/lib/format";
import { cssSeriesColor } from "@/lib/svgChartPalette";

const SCENARIO_ORDER = ["base", "adverse", "severely_adverse"] as const;

/** One NII path the chart and the sensitivity table compare. */
type Comparison = { key: string; label: string; run: ForecastRunRead };

function niiAt(run: ForecastRunRead, year: number): number | null {
  const point = run.path.find((x) => x.year === year);
  return point ? num(point.nii) : null;
}

export default function NiiForecastPage() {
  return (
    <Suspense
      fallback={
        <PageContainer className="py-6">
          <SkeletonChart height={320} />
        </PageContainer>
      }
    >
      <NiiForecastWorkspace />
    </Suspense>
  );
}

/** "Custom · 2026-06 · 1a2b3c4d · assumptions v2" — how a saved run is named here. */
function runOptionLabel(run: ForecastRunSummaryRead | ForecastRunRead): string {
  const periodLabel =
    "periodLabel" in run ? run.periodLabel : run.path[0]?.periodLabel;
  const version = run.assumptionVersion
    ? ` · assumptions v${run.assumptionVersion.versionNumber}`
    : "";
  return `${scenarioLabel(run.scenarioCode)}${periodLabel ? ` · ${periodLabel}` : ""} · ${shortId(run.id)}${version}`;
}

function NiiForecastWorkspace() {
  const { bank, moduleScope } = useBankContext();
  const bankId = bank?.id;
  const forecastingBankId = moduleScope.forecastingAggregatedView
    ? bankId
    : undefined;
  const canViewRuns = moduleScope.forecastingConfidentialView === true;
  const requestedRunId = useSearchParams().get("run");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

  // The route guard already requires confidential view for this tab; the
  // hooks take the same projection so nothing is requested without it.
  const scenarioSet = useScenarioRunSet(forecastingBankId, canViewRuns);
  const runsQuery = useForecastRuns(forecastingBankId, { limit: 50 });
  const succeeded = (runsQuery.data?.runs ?? []).filter(
    (run) => run.status === "succeeded",
  );
  const chosenRunId = selectedRunId ?? requestedRunId;
  const chosenQuery = useForecastRun(
    canViewRuns ? forecastingBankId : undefined,
    chosenRunId,
  );
  const runsByScenario: Record<string, ForecastRunRead | undefined> = {
    base: scenarioSet.base,
    adverse: scenarioSet.adverse,
    severely_adverse: scenarioSet.severelyAdverse,
  };
  // The run the trajectory and bridge read: the chosen saved run, else base
  // if present, else the first scenario with a succeeded run.
  const primary = chosenRunId
    ? chosenQuery.data
    : (scenarioSet.base ??
      scenarioSet.adverse ??
      scenarioSet.severelyAdverse ??
      undefined);
  const primaryId = primary?.id ?? chosenRunId ?? "";
  const selectableRuns =
    primary && !succeeded.some((run) => run.id === primary.id)
      ? [primary, ...succeeded]
      : succeeded;

  return (
    <>
      <PageHeader
        eyebrow="Forecasting"
        title="Net Interest Income Forecast"
        action={
          selectableRuns.length > 0 ? (
            <select
              value={primaryId}
              onChange={(e) => setSelectedRunId(e.target.value)}
              aria-label="Forecast run"
              className="max-w-xs px-3 py-2 text-caption font-medium text-navy border border-border rounded-md bg-surface-raised hover:bg-surface"
            >
              {primaryId === "" && <option value="">Select a run</option>}
              {selectableRuns.map((run) => (
                <option key={run.id} value={run.id}>
                  {runOptionLabel(run)}
                </option>
              ))}
            </select>
          ) : undefined
        }
      />

      <QueryBoundary
        isLoading={
          scenarioSet.isLoading ||
          (chosenRunId !== null && chosenQuery.isLoading)
        }
        error={scenarioSet.error ?? chosenQuery.error}
        onRetry={() => {
          scenarioSet.refetch();
          void chosenQuery.refetch();
        }}
      >
        <PageContainer className="py-6 space-y-6">
          {!primary ? (
            <EmptyState
              Icon={TrendingUp}
              title="No succeeded forecast runs yet"
              description="The NII trajectory reads the per-year net-interest-income field on a persisted forecast run. Run a forecast from the Balance Sheet tab to populate this view."
              action={
                <Link
                  href="/forecasting"
                  className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
                >
                  Run a forecast
                  <ArrowRight size={13} aria-hidden />
                </Link>
              }
            />
          ) : (
            <NiiDashboard primary={primary} runsByScenario={runsByScenario} />
          )}
        </PageContainer>
      </QueryBoundary>
    </>
  );
}

function NiiDashboard({
  primary,
  runsByScenario,
}: {
  primary: ForecastRunRead;
  runsByScenario: Record<string, ForecastRunRead | undefined>;
}) {
  const earning = primary.path.filter((p) => p.year > 0);
  // The run's own horizon: every year axis and label follows it.
  const horizon = earning.length;
  const years = earning.map((p) => p.year);
  const niiSeries = earning.map((p) => num(p.nii));
  const y1Nii = niiSeries[0] ?? 0;
  const y5Nii = niiSeries[niiSeries.length - 1] ?? 0;
  const cumulativeNii = niiSeries.reduce((s, v) => s + v, 0);
  const niiCagr =
    y1Nii > 0 && niiSeries.length > 1
      ? (Math.pow(y5Nii / y1Nii, 1 / (niiSeries.length - 1)) - 1) * 100
      : null;

  const bridgeData: EarningsPoint[] = earning.map((p) => ({
    label: `Y${p.year}`,
    nii: num(p.nii),
    fees: num(p.fees),
    opex: -num(p.opex),
    creditLosses: -num(p.creditLosses),
    netIncome: num(p.netIncome),
  }));

  // Scenario comparison — one line per preset scenario with a succeeded run,
  // plus the run this tab reads when it is not one of them (an edited run).
  const comparisons: Comparison[] = SCENARIO_ORDER.flatMap((code) => {
    const run = runsByScenario[code];
    return run ? [{ key: code, label: scenarioLabel(code), run }] : [];
  });
  if (!comparisons.some((c) => c.run.id === primary.id)) {
    comparisons.push({
      key: "selected",
      label: `${scenarioLabel(primary.scenarioCode)} run ${shortId(primary.id)}`,
      run: primary,
    });
  }
  const scenarioSeries: ScenarioSeries[] = comparisons.map((c, i) => ({
    key: c.key,
    name: c.label,
    colorIndex: i,
    dashed: c.key !== "base",
  }));
  const scenarioData: ScenarioPoint[] = years.map((year) => {
    const point: ScenarioPoint = { label: `Y${year}` };
    for (const c of comparisons) {
      point[c.key] = niiAt(c.run, year);
    }
    return point;
  });

  const base = runsByScenario.base;

  return (
    <div className="space-y-6">
      <p className="text-caption text-slate">
        Reading run{" "}
        <span className="font-mono text-navy">{shortId(primary.id)}</span> —{" "}
        {scenarioLabel(primary.scenarioCode)} scenario · {horizon}-year horizon
        ·{" "}
        {primary.assumptionVersion
          ? `assumptions: ${provenanceLabel(primary.assumptionVersion)}`
          : "no approved assumption version recorded on this run"}
      </p>

      {/* KPI strip */}
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
        <KpiStat
          label="Y1 projected NII"
          value={fmtCurrency(y1Nii)}
          hint={`${scenarioLabel(primary.scenarioCode)} scenario`}
          sparkline={<Sparkline data={niiSeries} color={cssSeriesColor(0)} />}
        />
        <KpiStat
          label={`${horizon}-year cumulative NII`}
          value={fmtCurrency(cumulativeNii)}
          hint={`Sum of Y1–Y${horizon} path values (derived)`}
        />
        <KpiStat
          label={`NII CAGR Y1→Y${horizon}`}
          value={niiCagr === null ? "—" : fmtPctSigned(niiCagr, 1)}
          hint="Derived from the stored path"
        />
        <KpiStat
          label="NIM assumption"
          value={fmtPct(num(primary.assumptions.nimPct), 2)}
          hint="Resolved assumption persisted on the run"
        />
      </div>

      {/* Earnings bridge */}
      <ChartFrame
        title="Earnings bridge"
        subtitle="NII + fee income vs operating expenses and credit losses, with the resulting net income path"
        height={320}
        footer={
          <span>
            All series are persisted per-year fields on run{" "}
            <span className="font-mono">{primary.id.slice(0, 8)}</span> —{" "}
            {scenarioLabel(primary.scenarioCode)} scenario.
          </span>
        }
      >
        <EarningsChart data={bridgeData} />
      </ChartFrame>

      {/* Scenario NII lines + sensitivity table */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        <ChartFrame
          title="NII by scenario"
          subtitle="Latest succeeded run per preset scenario, plus the run this tab reads"
          height={280}
          footer={
            comparisons.length < 2 ? (
              <span>
                Run adverse / severely adverse scenarios from the Scenarios tab
                to compare trajectories here.
              </span>
            ) : undefined
          }
        >
          <ScenarioLinesChart
            data={scenarioData}
            series={scenarioSeries}
            valueFormatter={(v) => fmtCurrency(v)}
          />
        </ChartFrame>

        <SectionCard
          title="Sensitivity vs base"
          subtitle="Δ vs the base case"
          noPadding
          computedAt={primary.createdAt}
        >
          {base ? (
            <SensitivityTable
              years={years}
              base={base}
              comparisons={comparisons.filter((c) => c.key !== "base")}
            />
          ) : (
            <p className="px-5 py-4 text-body text-slate">
              No succeeded base-case run — deltas need a base reference. Run the
              base scenario from the Balance Sheet tab.
            </p>
          )}
        </SectionCard>
      </div>
    </div>
  );
}

function SensitivityTable({
  years,
  base,
  comparisons,
}: {
  years: number[];
  base: ForecastRunRead;
  comparisons: Comparison[];
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-body border-collapse tnum">
        <thead>
          <tr className="border-b border-border bg-surface text-micro font-medium uppercase tracking-wider text-slate">
            <th className="text-left px-4 py-2.5">Year</th>
            <th className="text-right px-4 py-2.5">Base NII</th>
            {comparisons.map((c) => (
              <th key={c.key} className="text-right px-4 py-2.5">
                {c.label} · Δ vs base
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {years.map((year) => {
            const baseNii = niiAt(base, year);
            return (
              <tr
                key={year}
                className="border-b border-border-light last:border-b-0"
              >
                <td className="px-4 py-2.5 font-medium text-navy">Y{year}</td>
                <td className="px-4 py-2.5 text-right font-mono tnum">
                  {baseNii === null ? "—" : fmtCurrency(baseNii)}
                </td>
                {comparisons.map((c) => {
                  const v = niiAt(c.run, year);
                  const deltaPct =
                    v !== null && baseNii !== null ? yoyPct(v, baseNii) : null;
                  return (
                    <td key={c.key} className="px-4 py-2.5 text-right">
                      {v === null ? (
                        <span className="text-slate">—</span>
                      ) : (
                        <span className="inline-flex items-center gap-2 font-mono tnum">
                          {fmtCurrency(v)}
                          {deltaPct !== null && (
                            <DeltaBadge
                              value={deltaPct}
                              suffix="%"
                              decimals={1}
                            />
                          )}
                        </span>
                      )}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
      {comparisons.length === 0 && (
        <p className="px-5 py-3 text-caption text-slate border-t border-border-light">
          Only the base scenario has a succeeded run — no sensitivity columns to
          show yet.
        </p>
      )}
      <p className="px-4 py-2.5 text-caption text-slate border-t border-border-light inline-flex items-center gap-2">
        <StatusPill tone="slate">Derived</StatusPill>
        Delta is the percentage difference between the two saved projection
        paths; no values are modeled client-side.
      </p>
    </div>
  );
}
