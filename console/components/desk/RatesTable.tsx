'use client';

import {
  DataTable,
  SectionCard,
  SemanticDelta,
  StatusPill,
  type Column,
} from '@/components/ui';
import { DASH, fmtDate, num } from '@/lib/format';
import type { DeskPackageView, DeskRateEntry } from '@/lib/api';
import { show, TREATMENT_ORDER } from './util';

type Delta = DeskPackageView['week_over_week']['deltas'][number];
type Row = [string, DeskRateEntry];

/**
 * The desk's rates package — grouped by methodology treatment, each group a
 * dense DataTable. When prior-week deltas are supplied, a week-over-week Δ
 * column renders through SemanticDelta (a rate rise is an adverse ▲, a fall a
 * favorable ▼). Freshness surfaces as a StatusPill.
 */
export function RatesTable({
  rates,
  deltas,
  className = '',
}: {
  rates: Record<string, DeskRateEntry>;
  deltas?: Delta[];
  className?: string;
}) {
  const deltaMap = new Map((deltas ?? []).map((d) => [d.series_code, d]));
  const hasDeltas = (deltas?.length ?? 0) > 0;

  const entries = Object.entries(rates);
  const groups = new Map<string, Row[]>();
  for (const [code, entry] of entries) {
    const key = entry.treatment ?? 'unspecified';
    const bucket = groups.get(key) ?? [];
    bucket.push([code, entry]);
    groups.set(key, bucket);
  }
  const orderedKeys = [
    ...TREATMENT_ORDER.filter((k) => groups.has(k)),
    ...[...groups.keys()].filter((k) => !TREATMENT_ORDER.includes(k)),
  ];

  const seriesCol: Column<Row> = {
    key: 'series',
    header: 'Series',
    sortable: true,
    sortAccessor: ([code]) => code,
    render: ([code, entry]) => (
      <div className="min-w-0">
        <span className="font-mono text-caption text-ink">{code}</span>
        {entry.source_series && entry.source_series.length > 0 && (
          <div className="max-w-md truncate font-mono text-micro text-slate-light">
            ← {entry.source_series.join(', ')}
          </div>
        )}
      </div>
    ),
  };
  const valueCol: Column<Row> = {
    key: 'value',
    header: 'Value',
    numeric: true,
    sortable: true,
    sortAccessor: ([, e]) => num(e.value),
    render: ([, e]) => show(e.value),
  };
  const deltaCol: Column<Row> = {
    key: 'delta',
    header: 'Δ vs prior',
    numeric: true,
    sortable: true,
    sortAccessor: ([code]) => {
      const d = deltaMap.get(code);
      return d?.delta_pp != null ? Number(d.delta_pp) : null;
    },
    render: ([code]) => {
      const d = deltaMap.get(code);
      const delta = d?.delta_pp != null ? Number(d.delta_pp) : null;
      if (delta == null || Number.isNaN(delta)) return <span className="text-slate-light">{DASH}</span>;
      const direction = delta > 0 ? 'up' : delta < 0 ? 'down' : 'flat';
      const favorability = delta > 0 ? 'adverse' : delta < 0 ? 'favorable' : 'neutral';
      return (
        <SemanticDelta direction={direction} favorability={favorability}>
          {`${delta > 0 ? '+' : ''}${delta.toFixed(2)} pp`}
        </SemanticDelta>
      );
    },
  };
  const asOfCol: Column<Row> = {
    key: 'asof',
    header: 'As of',
    sortable: true,
    sortAccessor: ([, e]) => e.as_of ?? '',
    render: ([, e]) => (
      <span className="text-caption text-slate">{e.as_of ? fmtDate(e.as_of) : DASH}</span>
    ),
  };
  const freshCol: Column<Row> = {
    key: 'fresh',
    header: 'Freshness',
    render: ([, e]) =>
      e.staleness_flag ? (
        <StatusPill tone="amber">stale</StatusPill>
      ) : (
        <span className="text-micro text-slate-light">fresh</span>
      ),
  };

  const columns: Column<Row>[] = [
    seriesCol,
    valueCol,
    ...(hasDeltas ? [deltaCol] : []),
    asOfCol,
    freshCol,
  ];

  return (
    <SectionCard
      title="Rates package"
      subtitle={
        hasDeltas
          ? 'Computed levels by treatment, with the week-over-week move vs the last published package.'
          : 'Computed levels grouped by methodology treatment.'
      }
      noPadding
      className={className}
    >
      {entries.length === 0 ? (
        <p className="px-4 py-4 text-caption text-slate">No rates computed yet.</p>
      ) : (
        orderedKeys.map((treatment) => (
          <div key={treatment} className="border-b border-border-light last:border-b-0">
            <div className="bg-surface px-4 py-1.5 text-micro uppercase tracking-wide text-slate">
              {treatment.replace(/_/g, ' ')}
            </div>
            <DataTable columns={columns} rows={groups.get(treatment) ?? []} density="compact" />
          </div>
        ))
      )}
    </SectionCard>
  );
}
