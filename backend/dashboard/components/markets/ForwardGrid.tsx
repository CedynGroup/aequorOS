'use client';

/**
 * Forward-grid viewer (FC-5 deliverable §4.2) — the read-only, professional
 * tenor-adjusted grid for a published curve. The published canonical points are
 * `(tenor_months, rate)`; everything else on the grid is DERIVED and labelled as
 * such, because the desk's exact schedule dates, day-count basis, and discount
 * factors are not carried in the bank-facing views payload:
 *
 * - Start / End are rolled from the curve's as-of anchor by whole months.
 * - Discount factor is the simple money-market DF on the MM Act/360 anchor and
 *   is held invariant across the "Convert to" basis switch.
 * - The displayed yield is re-expressed on the chosen day-count (Act/360 →
 *   round-trips to the published rate; Act/365 / Act/Act rescale it).
 *
 * When the bank has an active overlay composition on the curve and the parent is
 * in "Your adjusted" view, the composed adjusted rate is shown alongside the
 * official series.
 */

import { useState } from 'react';
import { Info } from 'lucide-react';
import type { YieldCurveViewRead } from '@aequoros/risk-service-api';
import SectionCard from '@/components/ui/SectionCard';
import DataTable, { type Column } from '@/components/ui/DataTable';
import { num, fmtDateUTC } from '@/lib/api/values';
import { fmtPct } from '@/lib/format';
import {
  BASIS_LABELS,
  BASIS_ORDER,
  buildForwardGrid,
  type DayCountBasis,
  type ForwardGridRow,
} from '@/lib/markets/curveGrid';
import { tenorLabel } from './CurveBoard';

export function BasisSwitcher({
  value,
  onChange,
}: {
  value: DayCountBasis;
  onChange: (basis: DayCountBasis) => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-micro font-medium text-slate uppercase tracking-wider whitespace-nowrap">
        Convert to
      </span>
      <div
        role="group"
        aria-label="Output day-count basis"
        className="inline-flex items-center rounded-md border border-border-light bg-surface p-0.5"
      >
        {BASIS_ORDER.map((basis) => (
          <button
            key={basis}
            type="button"
            onClick={() => onChange(basis)}
            aria-pressed={value === basis}
            className={`px-2.5 py-1 rounded text-micro font-medium whitespace-nowrap ${
              value === basis
                ? 'bg-surface-raised text-navy shadow-subtle'
                : 'text-slate hover:text-navy'
            }`}
          >
            {BASIS_LABELS[basis]}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function ForwardGrid({
  curve,
  showAdjusted,
}: {
  curve: YieldCurveViewRead;
  showAdjusted: boolean;
}) {
  const [basis, setBasis] = useState<DayCountBasis>('act360');

  const adjustedByTenor =
    showAdjusted && curve.adjustedPoints.length > 0
      ? new Map(curve.adjustedPoints.map((p) => [p.tenorMonths, num(p.rate)]))
      : undefined;
  const hasAdjustedColumn = adjustedByTenor !== undefined && adjustedByTenor.size > 0;

  const rows = buildForwardGrid(
    curve.points.map((p) => ({ tenorMonths: p.tenorMonths, rate: num(p.rate) })),
    curve.asOfDate,
    basis,
    adjustedByTenor
  );

  const isForward = curve.curveType === 'forward';
  const startHeader = isForward ? 'Period start' : 'Start';
  const endHeader = isForward ? 'Period end' : 'Maturity';

  const columns: Column<ForwardGridRow>[] = [
    {
      key: 'tenor',
      header: 'Tenor',
      width: '9%',
      render: (row) => (
        <span className="font-mono text-caption text-navy">{tenorLabel(row.tenorMonths)}</span>
      ),
    },
    {
      key: 'start',
      header: (
        <span className="inline-flex items-center gap-1">
          {startHeader}
          <span className="text-slate-light lowercase font-normal tracking-normal">(derived)</span>
        </span>
      ),
      render: (row) => (
        <span className="font-mono text-caption text-slate">{fmtDateUTC(row.start)}</span>
      ),
    },
    {
      key: 'end',
      header: (
        <span className="inline-flex items-center gap-1">
          {endHeader}
          <span className="text-slate-light lowercase font-normal tracking-normal">(derived)</span>
        </span>
      ),
      render: (row) => (
        <span className="font-mono text-caption text-navy/90">{fmtDateUTC(row.end)}</span>
      ),
    },
    {
      key: 'days',
      header: 'Days',
      numeric: true,
      width: '8%',
      render: (row) => <span className="text-caption">{row.days}</span>,
    },
    {
      key: 'df',
      header: (
        <span className="inline-flex items-center gap-1">
          Discount factor
          <span className="text-slate-light lowercase font-normal tracking-normal">(derived)</span>
        </span>
      ),
      numeric: true,
      render: (row) => <span className="tnum">{row.df.toFixed(6)}</span>,
    },
    {
      key: 'yield',
      header: `Yield · ${BASIS_LABELS[basis]}`,
      numeric: true,
      render: (row) => <span className="tnum text-navy">{fmtPct(row.displayYieldPct, 3)}</span>,
    },
  ];

  if (hasAdjustedColumn) {
    columns.push(
      {
        key: 'adjDf',
        header: 'Adj. DF',
        numeric: true,
        render: (row) =>
          row.adjustedDf === undefined ? (
            '—'
          ) : (
            <span className="tnum text-action">{row.adjustedDf.toFixed(6)}</span>
          ),
      },
      {
        key: 'adjYield',
        header: `Adj. yield · ${BASIS_LABELS[basis]}`,
        numeric: true,
        render: (row) =>
          row.adjustedYieldPct === undefined ? (
            '—'
          ) : (
            <span className="tnum text-action">{fmtPct(row.adjustedYieldPct, 3)}</span>
          ),
      }
    );
  }

  return (
    <SectionCard
      title="Forward grid"
      subtitle={
        isForward
          ? 'Tenor-adjusted forward grid — the read-only desk deliverable'
          : 'Tenor-adjusted grid derived from the published curve points'
      }
      actions={<BasisSwitcher value={basis} onChange={setBasis} />}
      noPadding
      footer={
        <span className="inline-flex items-start gap-1.5 text-caption text-slate">
          <Info size={12} className="mt-0.5 shrink-0" aria-hidden />
          <span>
            Published payload carries tenor + rate only. <strong>{startHeader}/{endHeader}</strong>{' '}
            are rolled from the as-of anchor by whole months (not the desk&rsquo;s exact schedule
            dates), and the <strong>discount factor</strong> is a simple money-market DF
            (1 / (1 + rate·τ)) on the MM Act/360 anchor, held invariant while{' '}
            <strong>Convert to</strong> re-expresses only the displayed yield on the chosen
            day-count.
          </span>
        </span>
      }
    >
      <DataTable columns={columns} rows={rows} density="compact" stickyHeader maxHeight={360} />
    </SectionCard>
  );
}
