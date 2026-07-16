'use client';

/**
 * NII Forecast — net-interest-income trajectory from the persisted 5-year
 * projection path (per-year `nii` field), the full earnings bridge
 * (NII + fees − opex − credit losses → net income), and scenario
 * sensitivity built from the latest succeeded run per preset scenario.
 */

import { TrendingUp } from 'lucide-react';
import type { ForecastRunRead } from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import KpiStat from '@/components/ui/KpiStat';
import Sparkline from '@/components/ui/Sparkline';
import RunBadge from '@/components/ui/RunBadge';
import StatusPill from '@/components/ui/StatusPill';
import EmptyState from '@/components/ui/EmptyState';
import SectionCard from '@/components/ui/SectionCard';
import ChartFrame from '@/components/ui/ChartFrame';
import DeltaBadge from '@/components/ui/DeltaBadge';
import QueryBoundary from '@/components/ui/QueryBoundary';
import EarningsChart, {
  type EarningsPoint,
} from '@/components/forecasting/charts/EarningsChart';
import ScenarioLinesChart, {
  type ScenarioPoint,
  type ScenarioSeries,
} from '@/components/forecasting/charts/ScenarioLinesChart';
import { useScenarioRunSet } from '@/components/forecasting/hooks';
import { scenarioLabel, yoyPct } from '@/components/forecasting/lib';
import { useBankContext } from '@/components/shell/BankContext';
import { fmtDateUTC, num } from '@/lib/api/values';
import { fmtCurrency, fmtPct, fmtPctSigned } from '@/lib/format';
import { seriesColor } from '@/lib/chartTheme';

const SCENARIO_ORDER = ['base', 'adverse', 'severely_adverse'] as const;

export default function NiiForecastPage() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;

  const scenarioSet = useScenarioRunSet(bankId);
  const runsByScenario: Record<string, ForecastRunRead | undefined> = {
    base: scenarioSet.base,
    adverse: scenarioSet.adverse,
    severely_adverse: scenarioSet.severelyAdverse,
  };
  // Primary run for the trajectory/bridge: base if present, else the first
  // scenario with a succeeded run.
  const primary =
    scenarioSet.base ??
    scenarioSet.adverse ??
    scenarioSet.severelyAdverse ??
    undefined;

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Balance Sheet Forecasting', href: '/forecasting' },
          { label: 'NII Forecast' },
        ]}
        title="Net Interest Income Forecast"
        subtitle="Projected NII per horizon year from the persisted forecast path · scenario sensitivity vs base"
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
      />

      <QueryBoundary
        isLoading={scenarioSet.isLoading}
        error={scenarioSet.error}
        onRetry={scenarioSet.refetch}
      >
        <div className="px-8 py-6 space-y-6">
          {!primary ? (
            <EmptyState
              Icon={TrendingUp}
              title="No succeeded forecast runs yet"
              description="The NII trajectory reads the per-year net-interest-income field on a persisted forecast run. Run a forecast from the Balance Sheet tab to populate this view."
            />
          ) : (
            <NiiDashboard primary={primary} runsByScenario={runsByScenario} />
          )}
        </div>
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

  // Scenario comparison — one line per preset scenario with a succeeded run.
  const presentScenarios = SCENARIO_ORDER.filter((code) => runsByScenario[code]);
  const scenarioSeries: ScenarioSeries[] = presentScenarios.map((code, i) => ({
    key: code,
    name: scenarioLabel(code),
    colorIndex: i,
    dashed: code !== 'base',
  }));
  const years = [1, 2, 3, 4, 5];
  const scenarioData: ScenarioPoint[] = years.map((year) => {
    const point: ScenarioPoint = { label: `Y${year}` };
    for (const code of presentScenarios) {
      const p = runsByScenario[code]?.path.find((x) => x.year === year);
      point[code] = p ? num(p.nii) : null;
    }
    return point;
  });

  const base = runsByScenario.base;

  return (
    <div className="space-y-6">
      {/* KPI strip */}
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
        <KpiStat
          label="Y1 projected NII"
          value={fmtCurrency(y1Nii, 'GHS')}
          hint={`${scenarioLabel(primary.scenarioCode)} scenario`}
          sparkline={<Sparkline data={niiSeries} color={seriesColor(0)} />}
        />
        <KpiStat
          label="5-year cumulative NII"
          value={fmtCurrency(cumulativeNii, 'GHS')}
          hint="Sum of Y1–Y5 path values (derived)"
        />
        <KpiStat
          label="NII CAGR Y1→Y5"
          value={niiCagr === null ? '—' : fmtPctSigned(niiCagr, 1)}
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
            All series are persisted per-year fields on run{' '}
            <span className="font-mono">{primary.id.slice(0, 8)}</span> —{' '}
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
          subtitle="Latest succeeded run per preset scenario"
          height={280}
          footer={
            presentScenarios.length < 2 ? (
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
            valueFormatter={(v) => fmtCurrency(v, 'GHS')}
          />
        </ChartFrame>

        <SectionCard
          title="Sensitivity vs base"
          subtitle="Per-year NII by scenario, delta vs the base-case run"
          noPadding
          computedAt={primary.createdAt}
          runBadge={<RunBadge run={primary} />}
        >
          {base ? (
            <SensitivityTable
              base={base}
              scenarios={presentScenarios.filter((c) => c !== 'base')}
              runsByScenario={runsByScenario}
            />
          ) : (
            <p className="px-5 py-4 text-body text-slate">
              No succeeded base-case run — deltas need a base reference. Run
              the base scenario from the Balance Sheet tab.
            </p>
          )}
        </SectionCard>
      </div>
    </div>
  );
}

function SensitivityTable({
  base,
  scenarios,
  runsByScenario,
}: {
  base: ForecastRunRead;
  scenarios: string[];
  runsByScenario: Record<string, ForecastRunRead | undefined>;
}) {
  const years = [1, 2, 3, 4, 5];
  const niiAt = (run: ForecastRunRead | undefined, year: number) => {
    const p = run?.path.find((x) => x.year === year);
    return p ? num(p.nii) : null;
  };

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-body border-collapse tnum">
        <thead>
          <tr className="border-b border-border bg-surface text-micro font-medium uppercase tracking-wider text-slate">
            <th className="text-left px-4 py-2.5">Year</th>
            <th className="text-right px-4 py-2.5">Base NII</th>
            {scenarios.map((code) => (
              <th key={code} className="text-right px-4 py-2.5">
                {scenarioLabel(code)} · Δ vs base
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {years.map((year) => {
            const baseNii = niiAt(base, year);
            return (
              <tr key={year} className="border-b border-border-light last:border-b-0">
                <td className="px-4 py-2.5 font-medium text-navy">Y{year}</td>
                <td className="px-4 py-2.5 text-right font-mono tnum">
                  {baseNii === null ? '—' : fmtCurrency(baseNii, 'GHS')}
                </td>
                {scenarios.map((code) => {
                  const v = niiAt(runsByScenario[code], year);
                  const deltaPct =
                    v !== null && baseNii !== null ? yoyPct(v, baseNii) : null;
                  return (
                    <td key={code} className="px-4 py-2.5 text-right">
                      {v === null ? (
                        <span className="text-slate">—</span>
                      ) : (
                        <span className="inline-flex items-center gap-2 font-mono tnum">
                          {fmtCurrency(v, 'GHS')}
                          {deltaPct !== null && (
                            <DeltaBadge value={deltaPct} suffix="%" decimals={1} />
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
      {scenarios.length === 0 && (
        <p className="px-5 py-3 text-caption text-slate border-t border-border-light">
          Only the base scenario has a succeeded run — no sensitivity columns
          to show yet.
        </p>
      )}
      <p className="px-4 py-2.5 text-caption text-slate border-t border-border-light inline-flex items-center gap-2">
        <StatusPill tone="slate">Derived</StatusPill>
        Delta is the percentage difference between the two persisted run paths;
        no values are modeled client-side.
      </p>
    </div>
  );
}
