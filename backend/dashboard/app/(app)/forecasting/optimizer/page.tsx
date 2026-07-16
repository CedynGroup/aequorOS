'use client';

/**
 * Strategy Optimizer — constrained scenario search over the deterministic
 * 5-year projection engine. Presents the persisted optimizer output as
 * ranked strategy cards (decision levers + outcomes + constraint headroom),
 * an ROE impact chart across the top candidates, and the full ranking
 * table. Wiring (useRunOptimizer + stored-run hydration) is unchanged.
 */

import { Loader2, Search, Trophy } from 'lucide-react';
import type {
  OptimizerResultRead,
  RegulatoryRunRead,
} from '@aequoros/risk-service-api';
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  Cell,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from 'recharts';
import PageHeader from '@/components/ui/PageHeader';
import KpiStat from '@/components/ui/KpiStat';
import StatusPill from '@/components/ui/StatusPill';
import RunBadge from '@/components/ui/RunBadge';
import LimitBar from '@/components/ui/LimitBar';
import EmptyState from '@/components/ui/EmptyState';
import SectionCard from '@/components/ui/SectionCard';
import ChartFrame from '@/components/ui/ChartFrame';
import QueryBoundary, { ErrorPanel } from '@/components/ui/QueryBoundary';
import RunProvenance from '@/components/forecasting/RunProvenance';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useRegulatoryRun,
  useRegulatoryRuns,
  useRunOptimizer,
} from '@/lib/api/hooks';
import { fmtDateUTC, labelize, num } from '@/lib/api/values';
import { fmtPct } from '@/lib/format';
import {
  axisProps,
  CHART_GRID,
  chartTooltipProps,
  seriesColor,
} from '@/lib/chartTheme';

const SCOPE_COPY =
  'Constrained scenario search across 108 decision combinations (loan growth × securities allocation × deposit pricing × dividend payout), projected 5 years each, filtered against BoG constraints (CAR ≥ 10%, LCR ≥ 100%, NSFR ≥ 100%), ranked by 5-year average ROE.';

// ---------------------------------------------------------------------------
// Normalized optimizer output — from a fresh result or a stored run.
// ---------------------------------------------------------------------------

type CandidateView = {
  decision: {
    loanGrowthPct: number;
    securitiesShiftPp: number;
    depositPremiumBps: number;
    dividendPayoutPct: number;
    depositGrowthDeltaPct: number | null;
    nimDeltaPct: number | null;
  };
  summary: {
    avgRoePct: number;
    year5CarPct: number;
    year5LcrPct: number;
    year5NsfrPct: number;
  };
  constraints: {
    constraint: string;
    minimumPct: number;
    observedMinPct: number;
    passed: boolean;
  }[];
};

type OptimizerView = {
  candidatesEvaluated: number;
  feasibleCount: number;
  histogram: Record<string, number>;
  top: CandidateView[];
  provenance: { runId: string; inputHash: string; createdAt: Date | null };
};

function fromResult(result: OptimizerResultRead): OptimizerView {
  return {
    candidatesEvaluated: result.candidatesEvaluated,
    feasibleCount: result.feasibleCount,
    histogram: result.bindingConstraintHistogram,
    provenance: {
      runId: result.runId,
      inputHash: result.inputHash,
      createdAt: result.createdAt,
    },
    top: result.top.map((candidate) => ({
      decision: {
        loanGrowthPct: num(candidate.decision.loanGrowthPct),
        securitiesShiftPp: num(candidate.decision.securitiesShiftPp),
        depositPremiumBps: candidate.decision.depositPremiumBps,
        dividendPayoutPct: num(candidate.decision.dividendPayoutPct),
        depositGrowthDeltaPct: num(candidate.decision.depositGrowthDeltaPct),
        nimDeltaPct: num(candidate.decision.nimDeltaPct),
      },
      summary: {
        avgRoePct: num(candidate.summary.avgRoePct),
        year5CarPct: num(candidate.summary.year5CarPct),
        year5LcrPct: num(candidate.summary.year5LcrPct),
        year5NsfrPct: num(candidate.summary.year5NsfrPct),
      },
      constraints: candidate.constraintStatus.map((c) => ({
        constraint: c.constraint,
        minimumPct: num(c.minimumPct),
        observedMinPct: num(c.observedMinPct),
        passed: c.passed,
      })),
    })),
  };
}

/** Stored optimizer regulatory runs carry the same payload as snake_case metrics. */
function fromStoredRun(run: RegulatoryRunRead): OptimizerView | null {
  const metrics = run.metrics as {
    candidates_evaluated?: number;
    feasible_count?: number;
    binding_constraint_histogram?: Record<string, number>;
    top?: {
      decision: {
        loan_growth_pct: string;
        securities_shift_pp: string;
        deposit_premium_bps: number;
        dividend_payout_pct: string;
        deposit_growth_delta_pct?: string;
        nim_delta_pct?: string;
      };
      summary: {
        avg_roe_pct: string;
        year5_car_pct: string;
        year5_lcr_pct: string;
        year5_nsfr_pct: string;
      };
      constraint_status: {
        constraint: string;
        minimum_pct: string;
        observed_min_pct: string;
        passed: boolean;
      }[];
    }[];
  };
  if (!Array.isArray(metrics.top)) return null;
  return {
    candidatesEvaluated: metrics.candidates_evaluated ?? 0,
    feasibleCount: metrics.feasible_count ?? 0,
    histogram: metrics.binding_constraint_histogram ?? {},
    provenance: {
      runId: run.id,
      inputHash: run.inputHash,
      createdAt: run.createdAt,
    },
    top: metrics.top.map((candidate) => ({
      decision: {
        loanGrowthPct: num(candidate.decision.loan_growth_pct),
        securitiesShiftPp: num(candidate.decision.securities_shift_pp),
        depositPremiumBps: candidate.decision.deposit_premium_bps,
        dividendPayoutPct: num(candidate.decision.dividend_payout_pct),
        depositGrowthDeltaPct:
          candidate.decision.deposit_growth_delta_pct === undefined
            ? null
            : num(candidate.decision.deposit_growth_delta_pct),
        nimDeltaPct:
          candidate.decision.nim_delta_pct === undefined
            ? null
            : num(candidate.decision.nim_delta_pct),
      },
      summary: {
        avgRoePct: num(candidate.summary.avg_roe_pct),
        year5CarPct: num(candidate.summary.year5_car_pct),
        year5LcrPct: num(candidate.summary.year5_lcr_pct),
        year5NsfrPct: num(candidate.summary.year5_nsfr_pct),
      },
      constraints: (candidate.constraint_status ?? []).map((c) => ({
        constraint: c.constraint,
        minimumPct: num(c.minimum_pct),
        observedMinPct: num(c.observed_min_pct),
        passed: c.passed,
      })),
    })),
  };
}

export default function StrategicOptimizer() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  const runOptimizer = useRunOptimizer(bankId);
  const runsQuery = useRegulatoryRuns(bankId, { module: 'optimizer', limit: 1 });
  const latestStoredId = runsQuery.data?.runs[0]?.id ?? null;
  const storedRun = useRegulatoryRun(bankId, latestStoredId);

  const view: OptimizerView | null = runOptimizer.data
    ? fromResult(runOptimizer.data)
    : storedRun.data
    ? fromStoredRun(storedRun.data)
    : null;

  const runButton = (
    <button
      type="button"
      disabled={runOptimizer.isPending || !periodId}
      onClick={() =>
        periodId && runOptimizer.mutate({ reportingPeriodId: periodId })
      }
      className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
    >
      {runOptimizer.isPending ? (
        <Loader2 size={13} className="animate-spin" aria-hidden />
      ) : (
        <Search size={13} aria-hidden />
      )}
      Run optimizer
    </button>
  );

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Balance Sheet Forecasting', href: '/forecasting' },
          { label: 'Optimizer' },
        ]}
        title="Strategy Optimizer"
        subtitle="Constrained scenario search over the deterministic 5-year projection engine"
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
        action={runButton}
      />

      <QueryBoundary
        isLoading={runsQuery.isLoading || storedRun.isLoading}
        error={runsQuery.error}
        onRetry={() => runsQuery.refetch()}
      >
        <div className="px-8 py-6 space-y-6">
          {runOptimizer.error && (
            <ErrorPanel error={runOptimizer.error} title="Optimizer run failed" />
          )}

          {!view ? (
            <>
              <MethodNote />
              <EmptyState
                Icon={Search}
                title="No optimizer runs yet"
                description="Run the optimizer to search the decision grid for the highest 5-year average ROE strategy that keeps CAR, LCR, and NSFR above their BoG minimums. The full result persists as an auditable run."
                action={runButton}
              />
            </>
          ) : (
            <>
              {/* Search stats */}
              <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
                <KpiStat
                  label="Candidates evaluated"
                  value={view.candidatesEvaluated.toString()}
                  hint="Full decision grid, projected 5 years each"
                />
                <KpiStat
                  label="Feasible strategies"
                  value={`${view.feasibleCount}`}
                  hint={`of ${view.candidatesEvaluated} cleared all BoG floors`}
                />
                <KpiStat
                  label="Best 5Y average ROE"
                  value={
                    view.top.length ? fmtPct(view.top[0].summary.avgRoePct, 2) : '—'
                  }
                  hint="Highest-ranked feasible strategy"
                />
                <div className="card px-4 py-3.5">
                  <p className="text-micro font-medium uppercase tracking-wider text-slate">
                    Binding constraints (infeasible candidates)
                  </p>
                  <div className="mt-2 flex items-center gap-x-4 gap-y-1.5 flex-wrap">
                    {Object.entries(view.histogram).map(([code, count]) => (
                      <span
                        key={code}
                        className="inline-flex items-center gap-1.5 text-caption"
                      >
                        <span className="font-medium text-navy uppercase">{code}</span>
                        <span className="font-mono text-slate tnum">{count}</span>
                      </span>
                    ))}
                    {Object.values(view.histogram).every((v) => v === 0) && (
                      <span className="text-caption text-slate">
                        No constraint bound any candidate.
                      </span>
                    )}
                  </div>
                </div>
              </div>

              {view.top.length === 0 ? (
                <SectionCard
                  title="No feasible strategy in this search"
                  subtitle="Every candidate breached at least one BoG floor"
                  footer={
                    <RunProvenance
                      runId={view.provenance.runId}
                      inputHash={view.provenance.inputHash}
                      createdAt={view.provenance.createdAt}
                    />
                  }
                >
                  <p className="text-body text-navy/80 leading-relaxed max-w-3xl">
                    {view.candidatesEvaluated} decision combinations were
                    projected and none kept every regulatory ratio above its
                    floor across all five years. The binding-constraint
                    histogram above shows which floor eliminated the
                    candidates — address that ratio (capital raise, asset-mix
                    shift, or lower payout) and re-run the search.
                  </p>
                </SectionCard>
              ) : (
                <>
              {/* Ranked strategy cards */}
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                {view.top.slice(0, 3).map((candidate, i) => (
                  <StrategyCard key={i} rank={i + 1} candidate={candidate} />
                ))}
              </div>

              {/* Impact chart */}
              <ChartFrame
                title="ROE impact across top strategies"
                subtitle="5-year average ROE of each ranked candidate — the recommended strategy highlighted"
                height={260}
                footer={
                  <RunProvenance
                    runId={view.provenance.runId}
                    inputHash={view.provenance.inputHash}
                    createdAt={view.provenance.createdAt}
                    note={`Persisted as ${labelize('constrained_search')} regulatory runs for audit.`}
                  />
                }
              >
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart
                    data={view.top.map((c, i) => ({
                      label: `#${i + 1}`,
                      roe: c.summary.avgRoePct,
                    }))}
                    margin={{ top: 8, right: 16, left: 4, bottom: 4 }}
                  >
                    <CartesianGrid
                      stroke={CHART_GRID}
                      strokeDasharray="3 3"
                      vertical={false}
                    />
                    <XAxis dataKey="label" {...axisProps} interval={0} />
                    <YAxis
                      {...axisProps}
                      axisLine={false}
                      width={48}
                      tickFormatter={(v: number) => `${v.toFixed(1)}%`}
                    />
                    <Tooltip
                      {...chartTooltipProps}
                      cursor={{ fill: 'transparent' }}
                      formatter={(v: number) => [fmtPct(v, 2), '5Y avg ROE']}
                    />
                    <Bar dataKey="roe" maxBarSize={44} radius={[3, 3, 0, 0]}>
                      {view.top.map((_, i) => (
                        <Cell
                          key={i}
                          fill={i === 0 ? seriesColor(0) : seriesColor(4)}
                          fillOpacity={i === 0 ? 0.95 : 0.55}
                        />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </ChartFrame>

              {/* Full ranking table */}
              <SectionCard
                title="Full ranking"
                subtitle="Top strategies by 5-year average ROE, with the decision levers and constraint outcomes"
                noPadding
                computedAt={view.provenance.createdAt ?? undefined}
                runBadge={
                  storedRun.data && !runOptimizer.data ? (
                    <RunBadge run={storedRun.data} />
                  ) : undefined
                }
              >
                <RankingTable view={view} />
              </SectionCard>
                </>
              )}

              <MethodNote />
            </>
          )}
        </div>
      </QueryBoundary>
    </>
  );
}

function MethodNote() {
  return (
    <SectionCard
      title="How the optimizer works"
      subtitle="Method and scope of the persisted search"
    >
      <p className="text-body text-navy/80 leading-relaxed max-w-3xl">
        {SCOPE_COPY}
      </p>
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// Strategy card — decision levers + outcomes + constraint headroom
// ---------------------------------------------------------------------------

function StrategyCard({
  rank,
  candidate,
}: {
  rank: number;
  candidate: CandidateView;
}) {
  const d = candidate.decision;
  const levers: { label: string; value: string }[] = [
    { label: 'Loan growth', value: fmtPct(d.loanGrowthPct, 1) },
    {
      label: 'Securities shift',
      value: `${d.securitiesShiftPp >= 0 ? '+' : ''}${d.securitiesShiftPp.toFixed(1)} pp`,
    },
    {
      label: 'Deposit premium',
      value: `${d.depositPremiumBps >= 0 ? '+' : ''}${d.depositPremiumBps} bps`,
    },
    { label: 'Dividend payout', value: fmtPct(d.dividendPayoutPct, 0) },
  ];
  if (d.depositGrowthDeltaPct !== null) {
    levers.push({
      label: 'Deposit growth Δ',
      value: `${d.depositGrowthDeltaPct >= 0 ? '+' : ''}${d.depositGrowthDeltaPct.toFixed(1)} pp`,
    });
  }
  if (d.nimDeltaPct !== null) {
    levers.push({
      label: 'NIM Δ',
      value: `${d.nimDeltaPct >= 0 ? '+' : ''}${d.nimDeltaPct.toFixed(2)} pp`,
    });
  }

  return (
    <div
      className={`card p-5 flex flex-col gap-4 ${
        rank === 1 ? 'ring-1 ring-success/30' : ''
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <span
            className={`inline-flex items-center justify-center w-8 h-8 rounded ${
              rank === 1
                ? 'bg-success-light text-success'
                : 'bg-surface text-slate'
            }`}
          >
            {rank === 1 ? <Trophy size={15} aria-hidden /> : `#${rank}`}
          </span>
          <div>
            <p className="text-caption font-medium text-slate uppercase tracking-wider">
              Strategy #{rank}
            </p>
            <p className="font-mono text-h2 text-navy tnum">
              {fmtPct(candidate.summary.avgRoePct, 2)}
              <span className="text-caption text-slate font-sans"> 5Y avg ROE</span>
            </p>
          </div>
        </div>
        {rank === 1 && <StatusPill tone="success">Recommended</StatusPill>}
      </div>

      {/* Decision levers */}
      <div>
        <p className="text-micro font-medium uppercase tracking-wider text-slate mb-2">
          Decision levers
        </p>
        <div className="grid grid-cols-2 gap-x-4 gap-y-2">
          {levers.map((lever) => (
            <div key={lever.label}>
              <p className="text-caption text-slate">{lever.label}</p>
              <p className="font-mono text-body font-medium text-navy tnum">
                {lever.value}
              </p>
            </div>
          ))}
        </div>
      </div>

      {/* Constraint headroom */}
      <div className="border-t border-border-light pt-3 space-y-3">
        <p className="text-micro font-medium uppercase tracking-wider text-slate">
          Constraint headroom (5-year minimum vs BoG floor)
        </p>
        {candidate.constraints.map((c) => (
          <LimitBar
            key={c.constraint}
            label={c.constraint.toUpperCase()}
            value={c.observedMinPct}
            limit={c.minimumPct}
            direction="above"
            unit="%"
            format={(v) => v.toFixed(1)}
            limitLabel="BoG floor"
          />
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Full ranking table
// ---------------------------------------------------------------------------

function RankingTable({ view }: { view: OptimizerView }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-body border-collapse tnum">
        <thead>
          <tr className="border-b border-border bg-surface text-micro font-medium uppercase tracking-wider text-slate">
            <th className="text-left px-4 py-2.5">Rank</th>
            <th className="text-right px-4 py-2.5">Loan growth</th>
            <th className="text-right px-4 py-2.5">Securities shift</th>
            <th className="text-right px-4 py-2.5">Deposit premium</th>
            <th className="text-right px-4 py-2.5">Dividend payout</th>
            <th className="text-right px-4 py-2.5">Avg ROE</th>
            <th className="text-right px-4 py-2.5">Y5 CAR</th>
            <th className="text-right px-4 py-2.5">Y5 LCR</th>
            <th className="text-right px-4 py-2.5">Y5 NSFR</th>
            <th className="text-left px-4 py-2.5">Constraints</th>
          </tr>
        </thead>
        <tbody>
          {view.top.map((candidate, i) => (
            <tr
              key={i}
              className={`border-b border-border-light last:border-b-0 ${
                i === 0 ? 'bg-success-light/40' : 'hover:bg-surface-alt'
              }`}
            >
              <td className="px-4 py-2.5">
                <span className="inline-flex items-center gap-2">
                  <span className="font-mono font-medium text-navy tnum">
                    #{i + 1}
                  </span>
                  {i === 0 && <StatusPill tone="success">Recommended</StatusPill>}
                </span>
              </td>
              <td className="px-4 py-2.5 text-right font-mono tnum">
                {fmtPct(candidate.decision.loanGrowthPct, 1)}
              </td>
              <td className="px-4 py-2.5 text-right font-mono tnum">
                {candidate.decision.securitiesShiftPp >= 0 ? '+' : ''}
                {candidate.decision.securitiesShiftPp.toFixed(1)} pp
              </td>
              <td className="px-4 py-2.5 text-right font-mono tnum">
                {candidate.decision.depositPremiumBps >= 0 ? '+' : ''}
                {candidate.decision.depositPremiumBps} bps
              </td>
              <td className="px-4 py-2.5 text-right font-mono tnum">
                {fmtPct(candidate.decision.dividendPayoutPct, 0)}
              </td>
              <td className="px-4 py-2.5 text-right font-mono tnum font-medium text-navy">
                {fmtPct(candidate.summary.avgRoePct, 2)}
              </td>
              <td className="px-4 py-2.5 text-right font-mono tnum">
                {fmtPct(candidate.summary.year5CarPct, 2)}
              </td>
              <td className="px-4 py-2.5 text-right font-mono tnum">
                {fmtPct(candidate.summary.year5LcrPct, 1)}
              </td>
              <td className="px-4 py-2.5 text-right font-mono tnum">
                {fmtPct(candidate.summary.year5NsfrPct, 1)}
              </td>
              <td className="px-4 py-2.5">
                <span className="inline-flex items-center gap-1.5 flex-wrap">
                  {candidate.constraints.map((c) => (
                    <StatusPill
                      key={c.constraint}
                      tone={c.passed ? 'success' : 'critical'}
                    >
                      {c.constraint.toUpperCase()}
                    </StatusPill>
                  ))}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
