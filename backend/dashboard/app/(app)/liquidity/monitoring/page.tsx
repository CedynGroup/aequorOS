'use client';

import Link from 'next/link';
import { Percent } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import KpiStat from '@/components/ui/KpiStat';
import SectionCard from '@/components/ui/SectionCard';
import EmptyState from '@/components/ui/EmptyState';
import StatusPill from '@/components/ui/StatusPill';
import QueryBoundary from '@/components/ui/QueryBoundary';
import DataTable, { type Column } from '@/components/ui/DataTable';
import { useBankContext } from '@/components/shell/BankContext';
import StressedLadderPanel from '@/components/liquidity/StressedLadderPanel';
import CashflowWindowPanel from '@/components/liquidity/CashflowWindowPanel';
import {
  useLiquidityDashboard,
  useLiquidityHaircutSchedule,
  useLiquidityThresholdRegister,
  useRegulatoryRun,
} from '@/lib/api/hooks';
import { fmtDateUTC, num } from '@/lib/api/values';
import { currencyCode, fmtCurrency, fmtPct, regShort } from '@/lib/format';
import type {
  LiquidityHaircutRead,
  LiquidityThresholdRead,
} from '@aequoros/risk-service-api';

// LMTD 2026 ¶11(b)–(e): the Board's internal threshold register over the
// directive's published minimums, plus the LRMD ¶60–63 liquidity-value
// (haircut) schedule — the two Board registers behind the monitoring tools.

const thresholdColumns: Column<LiquidityThresholdRead>[] = [
  {
    key: 'code',
    header: 'Threshold',
    width: '34%',
    render: (r) => (
      <div>
        <p className="font-medium text-navy">{r.thresholdCode}</p>
        <p className="text-caption text-slate">
          {r.institutionClass === 'sdi' ? 'Binding for SDIs (¶9)' : 'Monitoring tool for banks'}
        </p>
      </div>
    ),
  },
  {
    key: 'value',
    header: 'Active level',
    numeric: true,
    render: (r) => fmtPct(num(r.thresholdPct), 2),
  },
  {
    key: 'source',
    header: 'Source',
    render: (r) =>
      r.source === 'board_register' ? (
        <StatusPill tone="success">Board register</StatusPill>
      ) : (
        <StatusPill tone="slate">Directive minimum</StatusPill>
      ),
  },
  {
    key: 'evidence',
    header: 'Approval evidence',
    render: (r) =>
      r.approvedBy ? (
        <div>
          <p className="text-body text-navy">{r.approvedBy}</p>
          <p className="text-caption text-slate">
            Effective {r.effectiveFrom ? fmtDateUTC(r.effectiveFrom) : '—'}
          </p>
        </div>
      ) : (
        <span className="text-caption text-slate">Regulatory default — no Board row yet</span>
      ),
  },
];

const haircutColumns: Column<LiquidityHaircutRead>[] = [
  { key: 'asset_class', header: 'Asset class', render: (r) => r.assetClass },
  {
    key: 'haircut',
    header: 'Estimated haircut',
    numeric: true,
    render: (r) => fmtPct(num(r.haircutPct), 2),
  },
  {
    key: 'effective',
    header: 'Effective',
    render: (r) => fmtDateUTC(r.effectiveFrom),
  },
  { key: 'approved', header: 'Reviewed by', render: (r) => r.approvedBy },
];

export default function MonitoringTools() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  const dashboard = useLiquidityDashboard(bankId, periodId);
  const thresholds = useLiquidityThresholdRegister(bankId);
  const haircuts = useLiquidityHaircutSchedule(bankId);
  const latestRunId = dashboard.data?.latestRunId;
  const latestRun = useRegulatoryRun(bankId, latestRunId);

  const metrics = dashboard.data?.metrics;
  const fxGap = metrics?.fxFundingGapGhs != null ? num(metrics.fxFundingGapGhs) : null;
  const fxShare =
    metrics?.fxShareOfLiabilitiesPct != null ? num(metrics.fxShareOfLiabilitiesPct) : null;
  const boardRows = thresholds.data?.thresholds.filter((row) => row.source === 'board_register') ?? [];

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Liquidity Risk', href: '/liquidity' },
          { label: 'Monitoring Tools' },
        ]}
        title="Liquidity Monitoring Tools"
        subtitle={`Board threshold register (LMTD ¶11) · liquidity-value schedule (LRMD ¶60–63) · per-currency funding mismatch`}
      />

      <QueryBoundary
        isLoading={thresholds.isLoading}
        error={thresholds.error}
        onRetry={() => thresholds.refetch()}
      >
        {thresholds.data && (
          <div className="px-8 py-6 space-y-6">
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
              <KpiStat
                label="Board-adopted thresholds"
                value={`${boardRows.length} / ${thresholds.data.thresholds.length}`}
                status={boardRows.length > 0 ? 'ok' : 'warn'}
                hint={
                  boardRows.length > 0
                    ? 'Remaining levels run on directive minimums'
                    : 'All levels currently run on directive minimums'
                }
              />
              <KpiStat
                label="Haircut schedule rows"
                value={String(haircuts.data?.haircuts.length ?? 0)}
                status={(haircuts.data?.haircuts.length ?? 0) > 0 ? 'ok' : 'warn'}
                hint="Unlisted classes report zero haircut with the gap noted (¶60–63)"
              />
              <KpiStat
                label="FX funding gap"
                value={fxGap !== null ? fmtCurrency(fxGap) : '—'}
                status={fxGap !== null && fxGap < 0 ? 'warn' : 'ok'}
                hint={`Non-${currencyCode()} assets minus liabilities (latest baseline run)`}
              />
              <KpiStat
                label="FX share of liabilities"
                value={fxShare !== null ? fmtPct(fxShare, 2) : '—'}
                status={'ok'}
                hint="Foreign-currency funding dependence"
              />
            </div>

            <StressedLadderPanel
              runId={latestRunId}
              run={latestRun.data}
              isLoading={dashboard.isLoading || latestRun.isLoading}
            />

            <CashflowWindowPanel bankId={bankId} />

            <SectionCard
              title="Board threshold register"
              subtitle={`Internal thresholds for the ${regShort()} monitoring tools — the Board's adopted levels over the directive's published minimums`}
              noPadding
              footer={
                <span>
                  Effective-dated generations with approval evidence answer the
                  examiner&apos;s &ldquo;show me your Board-approved thresholds&rdquo;;
                  updates are approver-gated and audited.
                </span>
              }
            >
              <DataTable columns={thresholdColumns} rows={thresholds.data.thresholds} />
            </SectionCard>

            <SectionCard
              title="Liquidity-value (haircut) schedule"
              subtitle="LRMD ¶60–63: the institution's own estimated haircuts per asset class, reviewed at least annually by Senior Management"
              noPadding
              footer={
                <span>
                  LMT Table 9&apos;s &ldquo;Estimated Haircut&rdquo; and
                  &ldquo;Monetized Value of Collateral&rdquo; columns resolve from
                  this schedule.
                </span>
              }
            >
              {haircuts.data && haircuts.data.haircuts.length > 0 ? (
                <DataTable columns={haircutColumns} rows={haircuts.data.haircuts} />
              ) : (
                <div className="p-5">
                  <EmptyState
                    Icon={Percent}
                    title="No haircut rows adopted yet"
                    description="Table 9 reports zero haircuts with the gap noted on the template rather than an invented number. Adopted rows appear here once Senior Management reviews the schedule."
                  />
                </div>
              )}
            </SectionCard>

            <SectionCard
              title="The eleven LMTD appendix tables"
              subtitle="Filed through the Regulatory Reporting as the LMT return"
            >
              <div className="text-body text-navy/85 leading-relaxed space-y-3">
                <p>
                  All eleven monitoring-tool tables — the contractual mismatch ladder,
                  concentration schedules, collateral and re-hypothecation views,
                  significant-currency splits and the unencumbered-assets schedule —
                  generate from canonical position data and export in the return
                  format from the{' '}
                  <Link href="/submissions" className="text-action hover:underline">
                    Regulatory Reporting
                  </Link>{' '}
                  (return code LMT). Per-currency funding gaps and the USD funding
                  stress ride every liquidity run on the{' '}
                  <Link href="/liquidity/stress" className="text-action hover:underline">
                    Stress tab
                  </Link>
                  .
                </p>
              </div>
            </SectionCard>
          </div>
        )}
      </QueryBoundary>
    </>
  );
}
