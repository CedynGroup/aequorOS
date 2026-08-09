'use client';

/**
 * Window analysis — engine-computed start/end-date analytics. The backend
 * resolves every reporting period inside the window with the dashboards'
 * exact trend semantics (stored baseline run first, inline recompute
 * fallback) and returns the series plus window statistics; nothing is
 * sliced client-side. The daily lines aggregate the plane-2 snapshot
 * ladder per module over the same window.
 */

import { useState } from 'react';
import type { LiveModule, WindowRatio } from '@aequoros/risk-service-api';
import SectionCard from '@/components/ui/SectionCard';
import DeltaBadge from '@/components/ui/DeltaBadge';
import { ErrorPanel } from '@/components/ui/QueryBoundary';
import { SkeletonLine } from '@/components/ui/Skeleton';
import { LIVE_MODULE_LABELS } from '@/components/live/moduleDisplay';
import { useWindowAnalytics } from '@/lib/api/hooks';
import { labelize, num } from '@/lib/api/values';
import { fmtPct } from '@/lib/format';

const RATIO_LABELS: Record<WindowRatio, string> = {
  lcr_pct: 'LCR',
  nsfr_pct: 'NSFR',
  car_pct: 'CAR',
  cet1_ratio_pct: 'CET1 ratio',
};

/** Short labels for the daily primary-metric keys (mirrors moduleDisplay). */
const METRIC_LABELS: Record<string, string> = {
  lcr_pct: 'LCR',
  car_pct: 'CAR',
  eve_limit_pct: 'ΔEVE / Tier 1',
  nop_pct_tier1: 'NOP / Tier 1',
  portfolio_nim_pct: 'Portfolio NIM',
  year5_car_pct: 'Year-5 CAR',
};

const PRESETS: { label: string; months: number }[] = [
  { label: 'Last quarter', months: 3 },
  { label: '6M', months: 6 },
  { label: '1Y', months: 12 },
];

const DATE_INPUT_CLASSES =
  'border border-border bg-surface-raised rounded-md text-body font-mono px-2.5 py-1.5 text-navy';

function toIso(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function monthsAgoIso(months: number): string {
  const d = new Date();
  d.setMonth(d.getMonth() - months);
  return toIso(d);
}

function moduleLabel(module: string): string {
  return LIVE_MODULE_LABELS[module as LiveModule] ?? labelize(module);
}

export default function WindowAnalysis({
  bankId,
}: {
  bankId: string | undefined;
}) {
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [applied, setApplied] = useState<{ start: string; end: string } | null>(
    null
  );
  const query = useWindowAnalytics(
    bankId,
    applied?.start,
    applied?.end,
    Boolean(applied)
  );

  const data = query.data;
  const storedPoints = (data?.ratios ?? []).reduce(
    (count, stat) => count + stat.points.filter((p) => p.stored).length,
    0
  );

  return (
    <SectionCard
      title="Window analysis"
      subtitle="Engine-computed over the selected dates — averages, extremes and the move across the window"
      footer={
        data ? (
          <span>
            {data.periodCount} periods · {storedPoints} stored run points
          </span>
        ) : undefined
      }
    >
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="date"
          value={start}
          onChange={(e) => setStart(e.target.value)}
          aria-label="Window start date"
          className={DATE_INPUT_CLASSES}
        />
        <span className="text-caption text-slate">to</span>
        <input
          type="date"
          value={end}
          onChange={(e) => setEnd(e.target.value)}
          aria-label="Window end date"
          className={DATE_INPUT_CLASSES}
        />
        <div
          role="group"
          aria-label="Window presets"
          className="inline-flex items-center rounded-md border border-border-light bg-surface p-0.5"
        >
          {PRESETS.map((preset) => (
            <button
              key={preset.label}
              type="button"
              onClick={() => {
                setStart(monthsAgoIso(preset.months));
                setEnd(toIso(new Date()));
              }}
              className="px-2.5 py-1 rounded text-micro font-medium text-slate hover:text-navy"
            >
              {preset.label}
            </button>
          ))}
        </div>
        <button
          type="button"
          disabled={!start || !end || !bankId}
          onClick={() => setApplied({ start, end })}
          className="px-3 py-1.5 text-caption font-medium btn-primary disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Compute
        </button>
      </div>

      <div className="mt-4">
        {!applied ? (
          <p className="text-caption text-slate">
            Pick a start and end date (or a preset), then Compute — the engines
            resolve every period in the window server-side.
          </p>
        ) : query.isFetching ? (
          <div className="space-y-2.5" aria-busy="true" aria-label="Computing">
            <SkeletonLine width="70%" height={12} />
            <SkeletonLine width="65%" height={12} />
            <SkeletonLine width="72%" height={12} />
            <SkeletonLine width="60%" height={12} />
          </div>
        ) : query.error ? (
          <ErrorPanel
            error={query.error}
            title="Could not compute the window"
            onRetry={() => void query.refetch()}
          />
        ) : data && data.ratios.length === 0 && data.daily.length === 0 ? (
          <p className="text-body text-slate">No periods in this window.</p>
        ) : data ? (
          <>
            <div className="divide-y divide-border-light">
              {data.ratios.map((stat) => (
                <div
                  key={stat.ratio}
                  className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 py-2.5"
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <span className="text-body font-medium text-navy w-20 shrink-0">
                      {RATIO_LABELS[stat.ratio]}
                    </span>
                    <span className="text-body font-mono tnum text-navy whitespace-nowrap">
                      {fmtPct(num(stat.startValue))} →{' '}
                      {fmtPct(num(stat.endValue))}
                    </span>
                    <DeltaBadge value={num(stat.change)} suffix=" pp" decimals={2} />
                  </div>
                  <span className="text-caption text-slate font-mono tnum whitespace-nowrap">
                    avg {fmtPct(num(stat.avg))} · min {fmtPct(num(stat.min))} ·
                    max {fmtPct(num(stat.max))}
                  </span>
                </div>
              ))}
            </div>
            {data.daily.length > 0 && (
              <div className="mt-3 pt-3 border-t border-border-light space-y-1">
                {data.daily.map((row) => (
                  <p key={row.module} className="text-caption text-slate">
                    {moduleLabel(row.module)}: {row.dayCount} daily{' '}
                    {row.dayCount === 1 ? 'close' : 'closes'} ·{' '}
                    {METRIC_LABELS[row.metricKey] ?? labelize(row.metricKey)}{' '}
                    avg{' '}
                    <span className="font-mono tnum">
                      {fmtPct(num(row.avg), 1)}
                    </span>{' '}
                    · min{' '}
                    <span className="font-mono tnum">
                      {fmtPct(num(row.min), 1)}
                    </span>
                  </p>
                ))}
              </div>
            )}
          </>
        ) : null}
      </div>
    </SectionCard>
  );
}
