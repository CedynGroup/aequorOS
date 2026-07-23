'use client';

import { Loader2, PlayCircle } from 'lucide-react';
import type { CapitalLineRead } from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import KpiStat from '@/components/ui/KpiStat';
import SectionCard from '@/components/ui/SectionCard';
import EmptyState from '@/components/ui/EmptyState';
import QueryBoundary, { ErrorPanel } from '@/components/ui/QueryBoundary';
import { useBankContext } from '@/components/shell/BankContext';
import {
  isNoBaselineRunError,
  useCapitalDashboard,
  useCapitalStructure,
  useCreateRegulatoryRun,
} from '@/lib/api/hooks';
import { fmtDateUTC, num, shortId } from '@/lib/api/values';
import { seriesColor } from '@/lib/chartTheme';
import { fmtCurrencyFull } from '@/lib/format';

function TierBlock({
  title,
  total,
  items,
  deductions,
  description,
  footnote,
}: {
  title: string;
  total: number;
  items: CapitalLineRead[];
  deductions: CapitalLineRead[];
  description: string;
  footnote?: string;
}) {
  return (
    <SectionCard
      title={title}
      subtitle={description}
      noPadding
      footer={footnote ? <span>{footnote}</span> : undefined}
    >
      <table className="w-full text-body tnum">
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
                {fmtCurrencyFull(num(it.weightedAmount))}
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
                {fmtCurrencyFull(-Math.abs(num(d.weightedAmount)))}
              </td>
            </tr>
          ))}
          <tr className="bg-surface font-medium border-t-2 border-navy">
            <td className="px-5 py-3 text-navy uppercase text-caption tracking-wider">
              {title} Total
            </td>
            <td className="px-5 py-3 num text-navy text-h3">
              {fmtCurrencyFull(total)}
            </td>
          </tr>
        </tbody>
      </table>
    </SectionCard>
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

  const cet1 = num(data?.cet1CapitalGhs);
  const at1 = num(data?.at1CapitalGhs);
  const tier2 = num(data?.tier2CapitalGhs);
  const total = num(data?.totalCapitalGhs);
  const compositionSegments = [
    { label: 'CET1', value: cet1, color: seriesColor(0) },
    { label: 'AT1', value: at1, color: seriesColor(1) },
    { label: 'Tier 2', value: tier2, color: seriesColor(2) },
  ].filter((s) => s.value > 0);

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
      className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
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
                <KpiStat
                  label="CET1"
                  value={fmtCurrencyFull(cet1)}
                  hint={pctOfRwa(cet1)}
                />
                <KpiStat
                  label="Tier 1 (CET1 + AT1)"
                  value={fmtCurrencyFull(num(data.tier1CapitalGhs))}
                  hint={pctOfRwa(num(data.tier1CapitalGhs))}
                />
                <KpiStat
                  label="Total capital"
                  value={fmtCurrencyFull(total)}
                  hint={`${pctOfRwa(total)} · CAR`}
                />
              </div>

              {/* Tier mix bar */}
              <SectionCard
                title="Tier mix"
                subtitle="Share of total qualifying capital by tier"
                footer={
                  <span>
                    Source run{' '}
                    <span className="font-mono text-navy">
                      {shortId(data.runId)}
                    </span>
                  </span>
                }
              >
                <div>
                  <div
                    className="flex h-4 rounded-sm overflow-hidden"
                    role="img"
                    aria-label={compositionSegments
                      .map(
                        (s) =>
                          `${s.label} ${
                            total > 0 ? ((s.value / total) * 100).toFixed(1) : 0
                          }%`
                      )
                      .join(', ')}
                  >
                    {compositionSegments.map((s) => (
                      <div
                        key={s.label}
                        style={{
                          width: `${total > 0 ? (s.value / total) * 100 : 0}%`,
                          background: s.color,
                        }}
                        title={`${s.label} · ${fmtCurrencyFull(s.value)}`}
                      />
                    ))}
                  </div>
                  <div className="mt-3 flex items-center gap-6 flex-wrap text-caption">
                    {compositionSegments.map((s) => (
                      <span key={s.label} className="inline-flex items-center gap-2">
                        <span
                          className="w-2 h-2 rounded-sm"
                          style={{ background: s.color }}
                          aria-hidden
                        />
                        <span className="text-navy font-medium">{s.label}</span>
                        <span className="font-mono text-navy tnum">
                          {fmtCurrencyFull(s.value)}
                        </span>
                        <span className="font-mono text-slate tnum">
                          {total > 0
                            ? `${((s.value / total) * 100).toFixed(1)}%`
                            : '—'}
                        </span>
                      </span>
                    ))}
                  </div>
                </div>
              </SectionCard>

              <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <TierBlock
                  title="CET1"
                  total={cet1}
                  items={data.cet1Components}
                  deductions={data.cet1Deductions}
                  description="Common Equity Tier 1 — highest quality capital"
                />
                <TierBlock
                  title="Additional Tier 1"
                  total={at1}
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
                  total={tier2}
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
