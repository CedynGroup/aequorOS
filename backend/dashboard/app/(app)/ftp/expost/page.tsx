'use client';

import { useMemo } from 'react';
import { FlaskConical } from 'lucide-react';
import type { RegulatoryRunSummaryRead } from '@aequoros/risk-service-api';
import KpiStat from '@/components/ui/KpiStat';
import SectionCard from '@/components/ui/SectionCard';
import EmptyState from '@/components/ui/EmptyState';
import DataTable, { type Column } from '@/components/ui/DataTable';
import FtpModuleFrame, { type FtpFrameContext } from '@/components/ftp/FtpModuleFrame';
import IllustrativeBadge from '@/components/ftp/IllustrativeBadge';
import { useRegulatoryRuns } from '@/lib/api/hooks';
import { num } from '@/lib/api/values';
import { fmtCurrency, fmtPct } from '@/lib/format';

const SCENARIO_ORDER = ['baseline', 'rates_up_200', 'funding_stress'] as const;
type ScenarioCode = (typeof SCENARIO_ORDER)[number];

const SCENARIO_LABELS: Record<ScenarioCode, string> = {
  baseline: 'Baseline',
  rates_up_200: 'Rates +200 bp',
  funding_stress: 'Funding stress',
};

type MetricRow = {
  metric: string;
  format: (v: number) => string;
  key: string;
  /** Whether the ex-post stand-in exists for this metric. */
  exPost?: number;
};

function runMetric(run: RegulatoryRunSummaryRead | undefined, key: string): number | null {
  const raw = run?.metrics?.[key];
  if (raw === null || raw === undefined) return null;
  const parsed = typeof raw === 'number' ? raw : Number(raw);
  return Number.isFinite(parsed) ? parsed : null;
}

export default function FtpExPostPage() {
  return (
    <FtpModuleFrame
      crumb="Ex-ante vs Ex-post"
      title="Ex-ante vs Ex-post"
      subtitle="Scenario-priced margins for this period against the subsequently measured outcome"
    >
      {(ctx) => <ExPostBody ctx={ctx} />}
    </FtpModuleFrame>
  );
}

function ExPostBody({ ctx }: { ctx: FtpFrameContext }) {
  const { data, bankId, periodId } = ctx;

  const runsQuery = useRegulatoryRuns(bankId, {
    module: 'ftp',
    reportingPeriodId: periodId,
    limit: 50,
  });

  /** Latest succeeded run per scenario for the selected period. */
  const runsByScenario = useMemo(() => {
    const byScenario = new Map<string, RegulatoryRunSummaryRead>();
    const runs = runsQuery.data?.runs ?? [];
    const sorted = [...runs].sort(
      (a, b) => b.createdAt.getTime() - a.createdAt.getTime()
    );
    for (const run of sorted) {
      if (run.status !== 'succeeded') continue;
      if (!byScenario.has(run.scenarioCode)) byScenario.set(run.scenarioCode, run);
    }
    return byScenario;
  }, [runsQuery.data]);

  /**
   * Ex-post stand-in: the first period AFTER the selected one whose measured
   * baseline NIM already exists in the trend payload. This is the engine's
   * next-period measurement on actual next-period facts — not a realized
   * accounting margin — hence the Illustrative badge.
   */
  const exPost = useMemo(() => {
    const idx = data.trend.findIndex((p) => p.reportingPeriodId === periodId);
    if (idx === -1 || idx + 1 >= data.trend.length) return null;
    const next = data.trend[idx + 1];
    return { label: next.label, nimPct: num(next.portfolioNimPct) };
  }, [data.trend, periodId]);

  const scenarioRuns = SCENARIO_ORDER.map((code) => ({
    code,
    run: runsByScenario.get(code),
  }));
  const anyRun = scenarioRuns.some((s) => s.run !== undefined);

  const baselineNim = runMetric(runsByScenario.get('baseline'), 'portfolio_nim_pct');
  const stressNims = scenarioRuns
    .filter((s) => s.code !== 'baseline')
    .map((s) => runMetric(s.run, 'portfolio_nim_pct'))
    .filter((v): v is number => v !== null);
  const worstStressNim = stressNims.length > 0 ? Math.min(...stressNims) : null;

  const metricRows: MetricRow[] = [
    {
      metric: 'Portfolio NIM',
      key: 'portfolio_nim_pct',
      format: (v) => fmtPct(v, 2),
      exPost: exPost?.nimPct,
    },
    {
      metric: 'Weighted asset yield',
      key: 'weighted_asset_yield_pct',
      format: (v) => fmtPct(v, 2),
    },
    {
      metric: 'Weighted funding credit',
      key: 'weighted_funding_credit_pct',
      format: (v) => fmtPct(v, 2),
    },
    {
      metric: 'Total contribution',
      key: 'total_contribution_ghs',
      format: (v) => fmtCurrency(v, 'GHS'),
    },
    {
      metric: 'Products below floor',
      key: 'products_below_min_margin',
      format: (v) => String(v),
    },
    {
      metric: 'Curve shift applied',
      key: 'curve_shift_pct',
      format: (v) => (v === 0 ? 'None' : fmtPct(v, 2)),
    },
  ];

  const columns: Column<MetricRow>[] = [
    { key: 'metric', header: 'Metric', render: (r) => r.metric, width: '24%' },
    ...scenarioRuns.map(({ code, run }) => ({
      key: code,
      header: `${SCENARIO_LABELS[code]} (ex-ante)`,
      numeric: true,
      render: (r: MetricRow) => {
        const v = runMetric(run, r.key);
        return v === null ? <span className="text-slate">—</span> : r.format(v);
      },
    })),
    {
      key: 'expost',
      header: (
        <span className="inline-flex items-center gap-1.5">
          Ex-post <IllustrativeBadge />
        </span>
      ),
      numeric: true,
      render: (r) =>
        r.exPost === undefined ? (
          <span className="text-slate">—</span>
        ) : (
          <span className="font-medium">{r.format(r.exPost)}</span>
        ),
    },
  ];

  if (!anyRun) {
    return (
      <EmptyState
        Icon={FlaskConical}
        title="No stored scenario runs for this period"
        description="The ex-ante columns come from the persisted baseline, rates-up, and funding-stress runs. Use 'Run all scenarios' in the header to mint them — the comparison frame fills in automatically."
      />
    );
  }

  return (
    <>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <KpiStat
          label="Ex-ante baseline NIM"
          value={baselineNim !== null ? fmtPct(baselineNim, 2) : '—'}
          hint="Persisted baseline run for this period"
        />
        <KpiStat
          label="Worst ex-ante stress NIM"
          value={worstStressNim !== null ? fmtPct(worstStressNim, 2) : '—'}
          status={
            worstStressNim !== null && baselineNim !== null && worstStressNim < baselineNim
              ? 'warn'
              : undefined
          }
          hint="Minimum across the stress overlays"
        />
        <KpiStat
          label="Ex-post stand-in NIM"
          value={exPost ? fmtPct(exPost.nimPct, 2) : '—'}
          hint={
            exPost
              ? `Measured in ${exPost.label} (next period)`
              : 'No subsequent period measured yet'
          }
        />
      </div>

      <SectionCard
        title="Scenario comparison"
        subtitle="Ex-ante columns are persisted scenario runs for the selected period"
        noPadding
        footer={
          <span>
            The ex-post column is the engine&apos;s measured baseline for the{' '}
            <em>following</em> period ({exPost ? exPost.label : 'not yet available'}) —
            a stand-in until realized accounting margins are ingested, hence the
            Illustrative badge. Only the NIM row has a measurable counterpart today.
          </span>
        }
      >
        <DataTable columns={columns} rows={metricRows} density="compact" />
      </SectionCard>
    </>
  );
}
