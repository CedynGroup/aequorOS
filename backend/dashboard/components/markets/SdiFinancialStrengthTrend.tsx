'use client';

/**
 * How the SDI financial-strength assessment has moved, day by day.
 *
 * Reads the plane-2 daily ladder (`live_metric_snapshots` via
 * `GET /banks/{id}/live-snapshots?module=rating`) — past rows are end-of-day
 * closes, the newest is the live edge. Nothing new is computed or stored: the
 * composite, each component score and the grade are already inside every
 * snapshot's `metrics`, so a trend is a READ of what was recorded on the day.
 * That matters for defensibility — a chart that recomputed history would show
 * today's methodology applied to yesterday's book, which is not what the
 * institution was assessed at.
 *
 * Gaps are honest. The ladder only has rows for days a refresh actually ran, so
 * a missing day is drawn as a break, never interpolated or zero-filled.
 */

import { useMemo } from 'react';
import type { LiveSnapshotRead } from '@aequoros/risk-service-api';
import { fmtDateUTC } from '@/lib/api/values';

const COMPONENT_LABELS: Record<string, string> = {
  capital_resilience: 'Capital resilience',
  asset_quality: 'Asset quality',
  liquidity_resilience: 'Liquidity resilience',
  concentration: 'Concentration',
  earnings_capacity: 'Earnings capacity',
  irrbb_sensitivity: 'Interest-rate sensitivity',
};

type Point = {
  date: Date;
  composite: number | null;
  grade: string | null;
  components: Record<string, number>;
};

function toPoint(snapshot: LiveSnapshotRead): Point {
  const metrics = (snapshot.metrics ?? {}) as Record<string, unknown>;
  const scores = Array.isArray(metrics.component_scores)
    ? (metrics.component_scores as { code: string; score: string }[])
    : [];
  const composite =
    typeof metrics.composite_score === 'string'
      ? Number(metrics.composite_score)
      : null;
  return {
    date: snapshot.snapshotDate,
    composite: composite !== null && Number.isFinite(composite) ? composite : null,
    grade: typeof metrics.rating_grade === 'string' ? metrics.rating_grade : null,
    components: Object.fromEntries(
      scores.map((score) => [score.code, Number(score.score)])
    ),
  };
}

/** A polyline over the assessed days, with unassessed days left as breaks. */
function Line({
  values,
  width = 520,
  height = 56,
}: {
  values: (number | null)[];
  width?: number;
  height?: number;
}) {
  const segments: string[] = [];
  let current: string[] = [];
  values.forEach((value, index) => {
    if (value === null) {
      if (current.length > 1) segments.push(current.join(' '));
      current = [];
      return;
    }
    const x = values.length > 1 ? (index / (values.length - 1)) * width : width / 2;
    // Scores are already on [0,1], so the axis is the full unit interval — not
    // auto-scaled. Auto-scaling would make a flat, healthy series look volatile.
    const y = height - value * height;
    current.push(`${x.toFixed(1)},${y.toFixed(1)}`);
  });
  if (current.length > 1) segments.push(current.join(' '));

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="w-full h-14"
      preserveAspectRatio="none"
      aria-hidden
    >
      <line
        x1="0"
        y1={height / 2}
        x2={width}
        y2={height / 2}
        className="stroke-border"
        strokeWidth="1"
        strokeDasharray="3 3"
      />
      {segments.map((points) => (
        <polyline
          key={points}
          points={points}
          fill="none"
          className="stroke-action"
          strokeWidth="1.75"
          vectorEffect="non-scaling-stroke"
        />
      ))}
    </svg>
  );
}

export default function SdiFinancialStrengthTrend({
  snapshots,
}: {
  snapshots: LiveSnapshotRead[];
}) {
  const points = useMemo(() => snapshots.map(toPoint), [snapshots]);
  const assessed = points.filter((point) => point.composite !== null);

  if (assessed.length === 0) return null;

  const first = assessed[0];
  const last = assessed[assessed.length - 1];
  const change =
    first.composite !== null && last.composite !== null
      ? last.composite - first.composite
      : null;
  // Grade CHANGES, not every day's grade: a migration is the event worth
  // seeing, and a flat series should read as flat.
  const migrations: { date: Date; from: string; to: string }[] = [];
  assessed.forEach((point, index) => {
    if (index === 0 || point.grade === null) return;
    const previous = assessed[index - 1].grade;
    if (previous && previous !== point.grade) {
      migrations.push({ date: point.date, from: previous, to: point.grade });
    }
  });

  const componentCodes = Object.keys(last.components);

  return (
    <div className="border border-border bg-surface-raised rounded-lg overflow-hidden">
      <div className="px-5 py-4 border-b border-border flex items-baseline justify-between gap-4 flex-wrap">
        <div>
          <p className="text-body font-medium text-navy">Assessment trend</p>
          <p className="mt-1 text-caption text-slate">
            {assessed.length} assessed {assessed.length === 1 ? 'day' : 'days'} ·{' '}
            {fmtDateUTC(first.date)} → {fmtDateUTC(last.date)}
          </p>
        </div>
        {change !== null ? (
          <div className="text-right">
            <p className="text-caption text-slate">Composite change</p>
            <p
              className={`text-body font-mono tnum ${
                change > 0
                  ? 'text-success'
                  : change < 0
                    ? 'text-critical'
                    : 'text-navy'
              }`}
            >
              {change > 0 ? '+' : ''}
              {change.toFixed(4)}
            </p>
          </div>
        ) : null}
      </div>

      <div className="px-5 py-4">
        <p className="text-caption text-slate mb-1">Composite</p>
        <Line values={points.map((point) => point.composite)} />
        <div className="mt-1 flex justify-between text-micro text-slate font-mono tnum">
          <span>{first.composite?.toFixed(4)}</span>
          <span>{last.composite?.toFixed(4)}</span>
        </div>
      </div>

      {componentCodes.length > 0 ? (
        <div className="px-5 pb-4 grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3">
          {componentCodes.map((code) => (
            <div key={code}>
              <p className="text-micro text-slate">
                {COMPONENT_LABELS[code] ?? code}
              </p>
              <Line
                values={points.map((point) =>
                  code in point.components ? point.components[code] : null
                )}
                height={28}
              />
            </div>
          ))}
        </div>
      ) : null}

      <div className="px-5 py-3 border-t border-border bg-surface">
        {migrations.length > 0 ? (
          <div className="space-y-1">
            <p className="text-micro text-slate">Grade migrations</p>
            {migrations.map((migration) => (
              <p
                key={migration.date.toISOString()}
                className="text-micro text-navy/85 font-mono"
              >
                {fmtDateUTC(migration.date)} · {migration.from} → {migration.to}
              </p>
            ))}
          </div>
        ) : (
          <p className="text-micro text-slate">
            No grade migration over this window
            {last.grade ? ` — held at ${last.grade}` : ''}.
          </p>
        )}
        <p className="mt-2 text-micro text-slate leading-relaxed">
          Each point is the assessment recorded that day, not a recomputation.
          Days with no refresh are shown as breaks.
        </p>
      </div>
    </div>
  );
}
