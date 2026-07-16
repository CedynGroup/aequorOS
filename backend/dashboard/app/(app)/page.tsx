'use client';

import Link from 'next/link';
import {
  ArrowRight,
  CheckCircle2,
  ChevronRight,
  ClipboardCheck,
  Clock,
  FileCheck2,
} from 'lucide-react';
import type {
  CapitalDashboardRead,
  LiquidityDashboardRead,
  RegulatoryRunRead,
} from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import KPICard from '@/components/ui/KPICard';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import QueryBoundary from '@/components/ui/QueryBoundary';
import LiveStatusCard from '@/components/live/LiveStatusCard';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useBankPeriodFacts,
  useBsd2Preview,
  useBsd3Preview,
  useCapitalDashboard,
  useLiquidityDashboard,
  useRegulatoryRun,
  useRegulatoryRuns,
  isNoBaselineRunError,
} from '@/lib/api/hooks';
import {
  totalAssets,
  totalCapital,
  totalDeposits,
  totalLoans,
} from '@/lib/api/facts';
import {
  fmtDateUTC,
  fmtTimestamp,
  labelize,
  num,
  severityTone,
  statusTone,
} from '@/lib/api/values';
import { fmtCurrency, fmtPct } from '@/lib/format';

export default function OverviewPage() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  const factsQuery = useBankPeriodFacts(bankId, periodId);
  const liqQuery = useLiquidityDashboard(bankId, periodId);
  const capQuery = useCapitalDashboard(bankId, periodId);
  const liqRun = useRegulatoryRun(bankId, liqQuery.data?.latestRunId);
  const capRun = useRegulatoryRun(bankId, capQuery.data?.latestRunId);
  const runsQuery = useRegulatoryRuns(bankId, { limit: 8 });

  const isLoading =
    factsQuery.isLoading || liqQuery.isLoading || capQuery.isLoading;
  const error = factsQuery.error ?? liqQuery.error ?? capQuery.error;

  return (
    <>
      <PageHeader
        title="Overview"
        subtitle={
          bank
            ? `${bank.name} · Bank of Ghana licensee · ${labelize(bank.licenseType)}`
            : 'Loading bank profile…'
        }
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
        action={
          <Link
            href="/submissions"
            className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-white bg-navy rounded-md hover:bg-navy-700"
          >
            Generate ALCO pack
            <ArrowRight size={13} aria-hidden />
          </Link>
        }
      />

      <QueryBoundary
        isLoading={isLoading}
        error={error}
        onRetry={() => {
          void factsQuery.refetch();
          void liqQuery.refetch();
          void capQuery.refetch();
        }}
      >
        {factsQuery.data && liqQuery.data && capQuery.data && (
          <div className="px-8 py-6 space-y-6">
            {/* Bank profile bar — canonical balance-sheet facts */}
            <div className="card px-5 py-4 grid grid-cols-2 md:grid-cols-4 gap-6">
              <ProfileCell
                label="Total assets"
                value={fmtCurrency(totalAssets(factsQuery.data), 'GHS')}
              />
              <ProfileCell
                label="Deposits"
                value={fmtCurrency(totalDeposits(factsQuery.data), 'GHS')}
              />
              <ProfileCell
                label="Loans"
                value={fmtCurrency(totalLoans(factsQuery.data), 'GHS')}
              />
              <ProfileCell
                label="Capital base"
                value={fmtCurrency(totalCapital(factsQuery.data), 'GHS')}
              />
            </div>

            {/* KPI grid — liquidity + capital dashboards */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
              <RatioKpi
                label="Liquidity Coverage Ratio"
                valuePct={liqQuery.data.metrics.lcrPct}
                status={liqQuery.data.metrics.lcrStatus}
                trend={liqQuery.data.trend.map((p) => num(p.lcrPct))}
                thresholdMin={metricThreshold(liqRun.data, 'lcr_pct')}
                fallbackThresholdCopy="BoG minimum 100%"
                href="/liquidity"
              />
              <RatioKpi
                label="Net Stable Funding Ratio"
                valuePct={liqQuery.data.metrics.nsfrPct}
                status={liqQuery.data.metrics.nsfrStatus}
                trend={liqQuery.data.trend.map((p) => num(p.nsfrPct))}
                thresholdMin={metricThreshold(liqRun.data, 'nsfr_pct')}
                fallbackThresholdCopy="BoG minimum 100%"
                href="/liquidity/nsfr"
              />
              <RatioKpi
                label="Capital Adequacy Ratio"
                valuePct={capQuery.data.metrics.carPct}
                status={capQuery.data.metrics.carStatus}
                trend={capQuery.data.trend.map((p) => num(p.carPct))}
                thresholdMin={metricThreshold(capRun.data, 'car_pct')}
                fallbackThresholdCopy="BoG minimum 10%"
                href="/basel"
              />
              <RatioKpi
                label="Tier 1 Capital Ratio"
                valuePct={capQuery.data.metrics.tier1RatioPct}
                status={capQuery.data.metrics.tier1Status}
                trend={capQuery.data.trend.map((p) => num(p.tier1RatioPct))}
                thresholdMin={metricThreshold(capRun.data, 'tier1_ratio_pct')}
                fallbackThresholdCopy="BoG minimum 8%"
                href="/basel"
              />
            </div>

            {/* Live status — cross-module current metrics + breach count */}
            <LiveStatusCard />

            {/* Two-column: open findings + submissions readiness */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              <OpenFindingsCard
                liquidity={liqQuery.data}
                capital={capQuery.data}
              />
              <SubmissionsCard periodLabel={period?.label ?? ''} />
            </div>

            {/* Recent regulatory runs */}
            <Card>
              <CardHeader
                title={
                  <span className="inline-flex items-center gap-2">
                    <Clock size={15} className="text-slate" aria-hidden />
                    Recent calculation runs
                  </span>
                }
                subtitle="Audit trail of regulatory engine runs across modules"
              />
              <CardBody className="p-0">
                <RecentRuns
                  runs={runsQuery.data?.runs ?? []}
                  isLoading={runsQuery.isLoading}
                />
              </CardBody>
            </Card>
          </div>
        )}
      </QueryBoundary>
    </>
  );
}

function ProfileCell({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-micro font-medium uppercase tracking-wider text-slate">
        {label}
      </p>
      <p className="mt-1 font-mono text-h2 text-navy tabular-nums">{value}</p>
    </div>
  );
}

function metricThreshold(
  run: RegulatoryRunRead | undefined,
  metricCode: string
): string | null {
  const result = run?.metricResults.find((m) => m.metricCode === metricCode);
  return result?.thresholdMin ?? null;
}

function RatioKpi({
  label,
  valuePct,
  status,
  trend,
  thresholdMin,
  fallbackThresholdCopy,
  href,
}: {
  label: string;
  valuePct: string;
  status: string;
  trend: number[];
  thresholdMin: string | null;
  fallbackThresholdCopy: string;
  href: string;
}) {
  const delta =
    trend.length >= 2 ? trend[trend.length - 1] - trend[trend.length - 2] : undefined;
  return (
    <KPICard
      label={label}
      value={num(valuePct)}
      suffix="%"
      decimals={2}
      status={statusTone(status)}
      delta={delta}
      sparkline={trend}
      href={href}
      footer={
        thresholdMin !== null
          ? `Regulatory minimum ${fmtPct(num(thresholdMin), 1)}`
          : fallbackThresholdCopy
      }
    />
  );
}

function OpenFindingsCard({
  liquidity,
  capital,
}: {
  liquidity: LiquidityDashboardRead;
  capital: CapitalDashboardRead;
}) {
  const findings = [
    ...liquidity.validations.map((v) => ({ module: 'Liquidity', ...v })),
    ...capital.validations.map((v) => ({ module: 'Capital', ...v })),
  ].filter((v) => !v.passed);

  return (
    <Card className="lg:col-span-2">
      <CardHeader
        title={
          <span className="inline-flex items-center gap-2">
            <ClipboardCheck size={15} className="text-action" aria-hidden />
            Open findings
          </span>
        }
        subtitle="Failed regulatory validations for the selected period"
        action={
          <Link
            href="/liquidity"
            className="text-caption font-medium text-action hover:text-action-hover inline-flex items-center gap-1"
          >
            View module <ChevronRight size={12} aria-hidden />
          </Link>
        }
      />
      <CardBody className="p-0">
        {findings.length === 0 ? (
          <div className="px-5 py-6 flex items-start gap-3">
            <CheckCircle2
              size={16}
              className="text-success shrink-0 mt-0.5"
              aria-hidden
            />
            <p className="text-body text-slate leading-relaxed">
              No open findings — all liquidity and capital validation rules
              passed for this reporting period.
            </p>
          </div>
        ) : (
          <ul className="divide-y divide-border-light">
            {findings.map((f) => (
              <li key={`${f.module}-${f.ruleCode}`} className="px-5 py-4">
                <div className="flex items-start gap-4">
                  <div className="shrink-0 mt-0.5">
                    <StatusPill tone={severityTone(f.severity)}>
                      {f.module}
                    </StatusPill>
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-body font-medium text-navy">
                      {labelize(f.ruleCode)}
                    </p>
                    <p className="mt-1 text-body text-slate leading-relaxed">
                      {f.message}
                    </p>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}

function SubmissionsCard({ periodLabel }: { periodLabel: string }) {
  const { bank, period } = useBankContext();
  const bsd3 = useBsd3Preview(bank?.id, period?.id);
  const bsd2 = useBsd2Preview(bank?.id, period?.id);

  const items: {
    title: string;
    form: string;
    href?: string;
    query: { isLoading: boolean; error: unknown; data: unknown };
  }[] = [
    {
      title: 'BoG Liquidity Returns (LCR & NSFR)',
      form: 'BSD-3',
      href: '/liquidity/submission',
      query: bsd3,
    },
    {
      title: 'BoG Capital Adequacy Return',
      form: 'BSD-2',
      query: bsd2,
    },
  ];

  return (
    <Card>
      <CardHeader
        title={
          <span className="inline-flex items-center gap-2">
            <FileCheck2 size={15} className="text-warning" aria-hidden />
            Submission previews
          </span>
        }
        subtitle="Bank of Ghana return readiness"
      />
      <CardBody className="p-0">
        <ul className="divide-y divide-border-light">
          {items.map((item) => {
            let tone: StatusTone = 'slate';
            let statusLabel = 'Checking…';
            if (!item.query.isLoading) {
              if (item.query.data) {
                tone = 'success';
                statusLabel = 'Ready — preview';
              } else if (isNoBaselineRunError(item.query.error)) {
                tone = 'amber';
                statusLabel = 'Baseline run required';
              } else if (item.query.error) {
                tone = 'slate';
                statusLabel = 'Unavailable';
              }
            }
            const body = (
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-body font-medium text-navy truncate">
                    {item.title}
                  </p>
                  <p className="mt-0.5 text-caption text-slate">
                    Form {item.form} ·{' '}
                    <span className="font-mono text-navy">{periodLabel}</span>
                  </p>
                </div>
                <StatusPill tone={tone}>{statusLabel}</StatusPill>
              </div>
            );
            return (
              <li key={item.form} className="px-5 py-3.5">
                {item.href ? (
                  <Link href={item.href} className="block hover:opacity-90">
                    {body}
                  </Link>
                ) : (
                  body
                )}
              </li>
            );
          })}
        </ul>
      </CardBody>
    </Card>
  );
}

function runStatusTone(status: string): StatusTone {
  if (status === 'succeeded') return 'success';
  if (status === 'failed') return 'critical';
  return 'slate';
}

function RecentRuns({
  runs,
  isLoading,
}: {
  runs: {
    id: string;
    createdAt: Date;
    module: string | null;
    scenarioCode: string;
    status: string;
    periodLabel: string;
  }[];
  isLoading: boolean;
}) {
  if (isLoading) {
    return <p className="px-5 py-4 text-body text-slate">Loading runs…</p>;
  }
  if (!runs.length) {
    return (
      <p className="px-5 py-4 text-body text-slate">
        No calculation runs yet — run a baseline from the Liquidity module to
        create the first auditable run.
      </p>
    );
  }
  return (
    <ul className="divide-y divide-border-light">
      {runs.map((run) => (
        <li key={run.id} className="px-5 py-3 flex items-center gap-4 text-body">
          <span className="font-mono text-caption text-slate w-36 shrink-0">
            {fmtTimestamp(run.createdAt)}
          </span>
          <span className="font-medium text-navy w-24 shrink-0">
            {run.module ? labelize(run.module) : '—'}
          </span>
          <span className="text-navy/85 flex-1 min-w-0 truncate">
            {labelize(run.scenarioCode)} scenario · period {run.periodLabel}
          </span>
          <StatusPill tone={runStatusTone(run.status)} className="shrink-0">
            {labelize(run.status)}
          </StatusPill>
        </li>
      ))}
    </ul>
  );
}
