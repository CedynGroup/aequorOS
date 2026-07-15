'use client';

import { Loader2, PlayCircle } from 'lucide-react';
import type { CapitalLineRead } from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import EmptyState from '@/components/ui/EmptyState';
import QueryBoundary, { ErrorPanel } from '@/components/ui/QueryBoundary';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import { useBankContext } from '@/components/shell/BankContext';
import {
  isNoBaselineRunError,
  useCapitalDashboard,
  useCapitalStructure,
  useCreateRegulatoryRun,
} from '@/lib/api/hooks';
import { fmtDateUTC, num } from '@/lib/api/values';
import { fmtCurrencyFull } from '@/lib/format';

function TierBlock({
  title,
  tone,
  total,
  items,
  deductions,
  description,
  footnote,
}: {
  title: string;
  tone: string;
  total: number;
  items: CapitalLineRead[];
  deductions: CapitalLineRead[];
  description: string;
  footnote?: string;
}) {
  return (
    <Card className={`border-l-4 border-l-${tone}`}>
      <CardHeader title={title} subtitle={description} />
      <CardBody className="p-0">
        <table className="w-full text-body">
          <tbody>
            {items.length === 0 && deductions.length === 0 && (
              <tr className="border-b border-border-light">
                <td className="px-5 py-2.5 text-slate" colSpan={2}>
                  No instruments outstanding.
                </td>
              </tr>
            )}
            {items.map((it) => (
              <tr key={it.lineCode} className="border-b border-border-light">
                <td className="px-5 py-2.5 text-navy/85">{it.description}</td>
                <td className="px-5 py-2.5 num text-navy/90">
                  {fmtCurrencyFull(num(it.weightedAmount), 'GHS')}
                </td>
              </tr>
            ))}
            {deductions.map((d) => (
              <tr
                key={d.lineCode}
                className="border-b border-border-light bg-critical-light/30"
              >
                <td className="px-5 py-2.5 text-critical text-caption">
                  Less: {d.description}
                </td>
                <td className="px-5 py-2.5 num text-critical">
                  {fmtCurrencyFull(-Math.abs(num(d.weightedAmount)), 'GHS')}
                </td>
              </tr>
            ))}
            <tr className="bg-surface font-medium border-t-2 border-navy">
              <td className="px-5 py-3 text-navy uppercase text-caption tracking-wider">
                {title} Total
              </td>
              <td className="px-5 py-3 num text-navy text-h3">
                {fmtCurrencyFull(total, 'GHS')}
              </td>
            </tr>
          </tbody>
        </table>
        {footnote && (
          <p className="px-5 py-3 text-caption text-slate border-t border-border-light leading-relaxed">
            {footnote}
          </p>
        )}
      </CardBody>
    </Card>
  );
}

export default function CapitalStructurePage() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  const structure = useCapitalStructure(bankId, periodId);
  const dashboard = useCapitalDashboard(bankId, periodId);
  const runBaseline = useCreateRegulatoryRun(bankId);

  const data = structure.data;
  const needsBaseline = isNoBaselineRunError(structure.error);
  const totalRwa = num(dashboard.data?.metrics.totalRwaGhs);
  const gpCapNote = dashboard.data?.validations.find(
    (v) => v.ruleCode === 'tier2_gp_cap_applied'
  );

  const pctOfRwa = (value: number) =>
    totalRwa > 0 ? `${((value / totalRwa) * 100).toFixed(2)}% of RWA` : '—';

  const runBaselineButton = (
    <button
      type="button"
      disabled={runBaseline.isPending || !periodId}
      onClick={() =>
        periodId &&
        runBaseline.mutate({
          module: 'capital',
          reportingPeriodId: periodId,
          scenarioCode: 'baseline',
        })
      }
      className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-white bg-navy rounded-md hover:bg-navy-700 disabled:opacity-60"
    >
      {runBaseline.isPending ? (
        <Loader2 size={13} className="animate-spin" aria-hidden />
      ) : (
        <PlayCircle size={13} aria-hidden />
      )}
      Run baseline
    </button>
  );

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Basel Capital', href: '/basel' },
          { label: 'Capital Structure' },
        ]}
        title="Capital Structure"
        subtitle="Tier 1 (CET1, AT1), Tier 2, and regulatory deductions"
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
      />

      {needsBaseline ? (
        <div className="px-8 py-6">
          <EmptyState
            Icon={PlayCircle}
            title="Baseline run required"
            description="The capital structure detail comes from a persisted baseline capital run. Run baseline to calculate and store the tiered capital breakdown for this reporting period."
            action={
              <div className="flex flex-col items-center gap-3">
                {runBaselineButton}
                {runBaseline.error && (
                  <ErrorPanel error={runBaseline.error} title="Run failed" />
                )}
              </div>
            }
          />
        </div>
      ) : (
        <QueryBoundary
          isLoading={structure.isLoading}
          error={structure.error}
          onRetry={() => structure.refetch()}
        >
          {data && (
            <div className="px-8 py-6 space-y-6">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div className="card p-5">
                  <p className="text-micro font-medium uppercase tracking-wider text-slate">
                    CET1
                  </p>
                  <p className="mt-1 font-mono text-h1 text-navy tabular-nums">
                    {fmtCurrencyFull(num(data.cet1CapitalGhs), 'GHS')}
                  </p>
                  <p className="mt-1 text-caption text-slate">
                    {pctOfRwa(num(data.cet1CapitalGhs))}
                  </p>
                </div>
                <div className="card p-5">
                  <p className="text-micro font-medium uppercase tracking-wider text-slate">
                    Tier 1 (CET1 + AT1)
                  </p>
                  <p className="mt-1 font-mono text-h1 text-navy tabular-nums">
                    {fmtCurrencyFull(num(data.tier1CapitalGhs), 'GHS')}
                  </p>
                  <p className="mt-1 text-caption text-slate">
                    {pctOfRwa(num(data.tier1CapitalGhs))}
                  </p>
                </div>
                <div className="card p-5">
                  <p className="text-micro font-medium uppercase tracking-wider text-slate">
                    Total Capital
                  </p>
                  <p className="mt-1 font-mono text-h1 text-navy tabular-nums">
                    {fmtCurrencyFull(num(data.totalCapitalGhs), 'GHS')}
                  </p>
                  <p className="mt-1 text-caption text-slate">
                    {pctOfRwa(num(data.totalCapitalGhs))} · CAR
                  </p>
                </div>
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <TierBlock
                  title="CET1"
                  tone="navy"
                  total={num(data.cet1CapitalGhs)}
                  items={data.cet1Components}
                  deductions={data.cet1Deductions}
                  description="Common Equity Tier 1 — highest quality capital"
                />
                <TierBlock
                  title="Additional Tier 1"
                  tone="action"
                  total={num(data.at1CapitalGhs)}
                  items={data.at1Components}
                  deductions={[]}
                  description="Going-concern capital"
                  footnote={`Tier 1 = CET1 + AT1 = ${fmtCurrencyFull(
                    num(data.tier1CapitalGhs),
                    'GHS'
                  )}.`}
                />
                <TierBlock
                  title="Tier 2"
                  tone="teal"
                  total={num(data.tier2CapitalGhs)}
                  items={data.tier2Components}
                  deductions={[]}
                  description="Gone-concern capital · Sub-debt and qualifying reserves"
                  footnote={gpCapNote?.message}
                />
              </div>
            </div>
          )}
        </QueryBoundary>
      )}
    </>
  );
}
