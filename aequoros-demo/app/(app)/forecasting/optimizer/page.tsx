'use client';

import { Loader2, Search } from 'lucide-react';
import type {
  OptimizerResultRead,
  RegulatoryRunRead,
} from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import StatusPill from '@/components/ui/StatusPill';
import RunBadge from '@/components/ui/RunBadge';
import EmptyState from '@/components/ui/EmptyState';
import QueryBoundary, { ErrorPanel } from '@/components/ui/QueryBoundary';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useRegulatoryRun,
  useRegulatoryRuns,
  useRunOptimizer,
} from '@/lib/api/hooks';
import { fmtDateUTC, fmtTimestamp, labelize, num, shortId } from '@/lib/api/values';
import { fmtPct } from '@/lib/format';

const SCOPE_COPY =
  'Constrained scenario search across 108 decision combinations (loan growth × securities allocation × deposit pricing × dividend payout), projected 5 years each, filtered against BoG constraints (CAR ≥ 10%, LCR ≥ 100%, NSFR ≥ 100%), ranked by 5-year average ROE.';

/** Normalized optimizer output — from a fresh result or a stored run. */
type CandidateView = {
  decision: {
    loanGrowthPct: number;
    securitiesShiftPp: number;
    depositPremiumBps: number;
    dividendPayoutPct: number;
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
  createdAt: Date | null;
};

function fromResult(result: OptimizerResultRead): OptimizerView {
  return {
    candidatesEvaluated: result.candidatesEvaluated,
    feasibleCount: result.feasibleCount,
    histogram: result.bindingConstraintHistogram,
    createdAt: result.createdAt,
    top: result.top.map((candidate) => ({
      decision: {
        loanGrowthPct: num(candidate.decision.loanGrowthPct),
        securitiesShiftPp: num(candidate.decision.securitiesShiftPp),
        depositPremiumBps: candidate.decision.depositPremiumBps,
        dividendPayoutPct: num(candidate.decision.dividendPayoutPct),
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

/** Stored regulatory runs carry the same payload as raw snake_case metrics. */
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
    createdAt: run.createdAt,
    top: metrics.top.map((candidate) => ({
      decision: {
        loanGrowthPct: num(candidate.decision.loan_growth_pct),
        securitiesShiftPp: num(candidate.decision.securities_shift_pp),
        depositPremiumBps: candidate.decision.deposit_premium_bps,
        dividendPayoutPct: num(candidate.decision.dividend_payout_pct),
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
  const runsQuery = useRegulatoryRuns(bankId, {
    module: 'optimizer',
    limit: 1,
  });
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
      className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-white bg-navy rounded-md hover:bg-navy-700 disabled:opacity-60"
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
          { label: 'Strategy Optimizer' },
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
          {/* Method — honest scope statement */}
          <Card>
            <CardBody className="flex items-start gap-3">
              <span className="inline-flex items-center justify-center w-9 h-9 rounded bg-action-light text-action shrink-0">
                <Search size={16} aria-hidden />
              </span>
              <div>
                <p className="text-body font-medium text-navy">
                  How the optimizer works
                </p>
                <p className="mt-1 text-body text-navy/80 leading-relaxed max-w-3xl">
                  {SCOPE_COPY}
                </p>
              </div>
            </CardBody>
          </Card>

          {runOptimizer.error && (
            <ErrorPanel error={runOptimizer.error} title="Optimizer run failed" />
          )}

          {!view ? (
            <EmptyState
              Icon={Search}
              title="No optimizer runs yet"
              description="Run the optimizer to search the decision grid for the highest 5-year average ROE strategy that keeps CAR, LCR, and NSFR above their BoG minimums. The full result persists as an auditable run."
              action={runButton}
            />
          ) : (
            <>
              {/* Stat strip */}
              <div className="card px-5 py-4 grid grid-cols-2 md:grid-cols-4 gap-6">
                <StatCell
                  label="Candidates evaluated"
                  value={view.candidatesEvaluated.toString()}
                />
                <StatCell
                  label="Feasible strategies"
                  value={`${view.feasibleCount} of ${view.candidatesEvaluated}`}
                />
                <div className="md:col-span-2">
                  <p className="text-micro font-medium uppercase tracking-wider text-slate">
                    Binding constraints across infeasible candidates
                  </p>
                  <div className="mt-2 flex items-center gap-4 flex-wrap">
                    {Object.entries(view.histogram).map(([code, count]) => (
                      <span
                        key={code}
                        className="inline-flex items-center gap-2 text-caption"
                      >
                        <span className="font-medium text-navy uppercase">
                          {code}
                        </span>
                        <span className="font-mono text-slate tabular-nums">
                          {count} binding
                        </span>
                      </span>
                    ))}
                    {Object.values(view.histogram).every((v) => v === 0) && (
                      <span className="text-caption text-slate">
                        No constraint bound any candidate — all strategies
                        cleared the regulatory floors.
                      </span>
                    )}
                  </div>
                </div>
              </div>

              {/* Top-10 table */}
              <Card>
                <CardHeader
                  title="Top strategies by 5-year average ROE"
                  subtitle={
                    view.createdAt
                      ? `Latest optimizer run · ${fmtTimestamp(view.createdAt)}`
                      : 'Latest optimizer run'
                  }
                  action={
                    storedRun.data && !runOptimizer.data ? (
                      <RunBadge run={storedRun.data} />
                    ) : undefined
                  }
                />
                <CardBody className="p-0">
                  <div className="overflow-x-auto">
                    <table className="w-full text-body border-collapse">
                      <thead>
                        <tr className="border-b border-border bg-surface text-micro font-medium uppercase tracking-wider text-slate">
                          <th className="text-left px-4 py-2.5">Rank</th>
                          <th className="text-right px-4 py-2.5">Loan growth</th>
                          <th className="text-right px-4 py-2.5">
                            Securities shift
                          </th>
                          <th className="text-right px-4 py-2.5">
                            Deposit premium
                          </th>
                          <th className="text-right px-4 py-2.5">
                            Dividend payout
                          </th>
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
                                <span className="font-mono font-medium text-navy tabular-nums">
                                  #{i + 1}
                                </span>
                                {i === 0 && (
                                  <StatusPill tone="success">
                                    Recommended
                                  </StatusPill>
                                )}
                              </span>
                            </td>
                            <td className="px-4 py-2.5 num">
                              {fmtPct(candidate.decision.loanGrowthPct, 1)}
                            </td>
                            <td className="px-4 py-2.5 num">
                              {candidate.decision.securitiesShiftPp >= 0 ? '+' : ''}
                              {candidate.decision.securitiesShiftPp.toFixed(1)} pp
                            </td>
                            <td className="px-4 py-2.5 num">
                              {candidate.decision.depositPremiumBps >= 0 ? '+' : ''}
                              {candidate.decision.depositPremiumBps} bps
                            </td>
                            <td className="px-4 py-2.5 num">
                              {fmtPct(candidate.decision.dividendPayoutPct, 0)}
                            </td>
                            <td className="px-4 py-2.5 num font-medium text-navy">
                              {fmtPct(candidate.summary.avgRoePct, 2)}
                            </td>
                            <td className="px-4 py-2.5 num">
                              {fmtPct(candidate.summary.year5CarPct, 2)}
                            </td>
                            <td className="px-4 py-2.5 num">
                              {fmtPct(candidate.summary.year5LcrPct, 1)}
                            </td>
                            <td className="px-4 py-2.5 num">
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
                </CardBody>
              </Card>

              <p className="text-caption text-slate max-w-3xl leading-relaxed">
                Decision grid: loan growth, securities allocation shift,
                deposit pricing premium, and dividend payout are varied
                jointly; every combination is projected with the same
                deterministic engine used by the Forecast Dashboard.
                Constraint columns show whether the strategy&apos;s{' '}
                <span className="font-medium text-navy">minimum</span> CAR,
                LCR, and NSFR across all five years stayed above the BoG
                floors. Results are persisted as{' '}
                {labelize('constrained_search')} regulatory runs for audit.
              </p>
            </>
          )}
        </div>
      </QueryBoundary>
    </>
  );
}

function StatCell({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-micro font-medium uppercase tracking-wider text-slate">
        {label}
      </p>
      <p className="mt-1 font-mono text-h1 text-navy tabular-nums">{value}</p>
    </div>
  );
}
