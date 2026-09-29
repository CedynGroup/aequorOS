"use client";

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
 * Gaps are honest. The ladder only has rows for days a refresh actually ran,
 * so a missing day is drawn as a break, never interpolated or zero-filled.
 *
 * The composite axis is zoomed to the observed range WITH visible tick
 * labels. The previous revision drew a hidden fixed 0–1 axis, so a real move
 * of a few hundredths rendered as a flat two-pixel line — hiding the axis is
 * what misleads, not scaling it; the footer states that scores live on the
 * 0–1 interval.
 */

import { useMemo } from "react";
import type { LiveSnapshotRead } from "@aequoros/risk-service-api";
import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import { seriesColor, useChartTokens } from "@/components/bi/echartsTheme";
import {
  gapAwareData,
  itemTooltip,
  LINE_SERIES_BASE,
} from "@/lib/echartsOptions";
import { fmtDateUTC } from "@/lib/api/values";

const COMPONENT_LABELS: Record<string, string> = {
  capital_resilience: "Capital resilience",
  asset_quality: "Asset quality",
  liquidity_resilience: "Liquidity resilience",
  concentration: "Concentration",
  earnings_capacity: "Earnings capacity",
  irrbb_sensitivity: "Interest-rate sensitivity",
};

type Point = {
  date: Date;
  composite: number | null;
  grade: string | null;
  components: Record<string, number>;
};

type Row = {
  /** ISO-ish day key — stable identity for a calendar day. */
  day: string;
  /** Short tick label, from UTC parts so the plotted day never TZ-shifts. */
  label: string;
  date: Date;
  composite: number | null;
  grade: string | null;
} & Record<`c_${string}`, number | null>;

function toPoint(snapshot: LiveSnapshotRead): Point {
  const metrics = (snapshot.metrics ?? {}) as Record<string, unknown>;
  const scores = Array.isArray(metrics.component_scores)
    ? (metrics.component_scores as { code: string; score: string }[])
    : [];
  const composite =
    typeof metrics.composite_score === "string"
      ? Number(metrics.composite_score)
      : null;
  return {
    date: snapshot.snapshotDate,
    composite:
      composite !== null && Number.isFinite(composite) ? composite : null,
    grade:
      typeof metrics.rating_grade === "string" ? metrics.rating_grade : null,
    components: Object.fromEntries(
      scores.map((score) => [score.code, Number(score.score)]),
    ),
  };
}

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

function dayKey(date: Date): string {
  return `${date.getUTCFullYear()}-${date.getUTCMonth()}-${date.getUTCDate()}`;
}

function shortLabel(date: Date): string {
  return `${date.getUTCDate()} ${MONTHS[date.getUTCMonth()]}`;
}

/** Observed range padded for legibility, clamped to the unit interval. */
function paddedDomain(values: number[]): [number, number] {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = Math.max((max - min) * 0.25, 0.015);
  return [Math.max(0, min - pad), Math.min(1, max + pad)];
}

export default function SdiFinancialStrengthTrend({
  snapshots,
  className,
}: {
  snapshots: LiveSnapshotRead[];
  /** Layout hook for side-by-side placement (e.g. "h-full"). */
  className?: string;
}) {
  const points = useMemo(() => snapshots.map(toPoint), [snapshots]);

  const rows = useMemo<Row[]>(() => {
    const assessedPoints = points.filter((point) => point.composite !== null);
    if (assessedPoints.length === 0) return [];
    // A continuous day spine from first to last assessed day, so unassessed
    // days occupy real width and render as breaks rather than being squeezed
    // out (which would misstate the assessment cadence).
    const byDay = new Map(points.map((point) => [dayKey(point.date), point]));
    const spine: Row[] = [];
    const cursor = new Date(assessedPoints[0].date.getTime());
    const end = assessedPoints[assessedPoints.length - 1].date.getTime();
    while (cursor.getTime() <= end) {
      const point = byDay.get(dayKey(cursor));
      const row: Row = {
        day: dayKey(cursor),
        label: shortLabel(cursor),
        date: new Date(cursor.getTime()),
        composite: point?.composite ?? null,
        grade: point?.grade ?? null,
      };
      for (const code of Object.keys(COMPONENT_LABELS)) {
        row[`c_${code}`] =
          point && code in point.components ? point.components[code] : null;
      }
      spine.push(row);
      cursor.setUTCDate(cursor.getUTCDate() + 1);
    }
    return spine;
  }, [points]);

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
  const migrations: {
    day: string;
    date: Date;
    from: string;
    to: string;
    composite: number | null;
  }[] = [];
  assessed.forEach((point, index) => {
    if (index === 0 || point.grade === null) return;
    const previous = assessed[index - 1].grade;
    if (previous && previous !== point.grade) {
      migrations.push({
        day: dayKey(point.date),
        date: point.date,
        from: previous,
        to: point.grade,
        composite: point.composite,
      });
    }
  });

  const compositeDomain = paddedDomain(
    assessed.map((point) => point.composite as number),
  );
  const componentCodes = Object.keys(last.components);

  return (
    <div
      className={`border border-border bg-surface-raised rounded-lg overflow-hidden flex flex-col ${className ?? ""}`}
    >
      <div className="px-5 py-4 border-b border-border flex items-baseline justify-between gap-4 flex-wrap">
        <div>
          <p className="text-body font-medium text-navy">Assessment trend</p>
          <p className="mt-1 text-caption text-slate">
            {assessed.length} assessed {assessed.length === 1 ? "day" : "days"}{" "}
            · {fmtDateUTC(first.date)} → {fmtDateUTC(last.date)}
          </p>
        </div>
        {change !== null ? (
          <div className="text-right">
            <p className="text-caption text-slate">Composite change</p>
            <p
              className={`text-body font-mono tnum ${
                change > 0
                  ? "text-success"
                  : change < 0
                    ? "text-critical"
                    : "text-navy"
              }`}
            >
              {change > 0 ? "+" : ""}
              {change.toFixed(4)}
            </p>
          </div>
        ) : null}
      </div>

      <div className="px-3 pt-4 pb-1">
        <p className="px-2 text-caption text-slate mb-1">Composite</p>
        <CompositeTrendChart
          rows={rows}
          migrations={migrations}
          domain={compositeDomain}
        />
      </div>

      {componentCodes.length > 0 ? (
        <div className="px-5 pb-4 grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3">
          {componentCodes.map((code, index) => {
            const series = assessed
              .filter((point) => code in point.components)
              .map((point) => point.components[code]);
            if (series.length === 0) return null;
            const latest = series[series.length - 1];
            const delta = series.length > 1 ? latest - series[0] : null;
            return (
              <div key={code}>
                <div className="flex items-baseline justify-between gap-2">
                  <p className="text-micro text-slate truncate">
                    {COMPONENT_LABELS[code] ?? code}
                  </p>
                  <p className="text-micro font-mono tnum text-navy/85 shrink-0">
                    {latest.toFixed(4)}
                    {delta !== null && delta !== 0 ? (
                      <span
                        className={delta > 0 ? "text-success" : "text-critical"}
                      >
                        {" "}
                        {delta > 0 ? "+" : ""}
                        {delta.toFixed(4)}
                      </span>
                    ) : null}
                  </p>
                </div>
                <ComponentSparkChart
                  rows={rows}
                  code={code}
                  label={COMPONENT_LABELS[code] ?? code}
                  colorIndex={index + 1}
                  domain={paddedDomain(series)}
                />
              </div>
            );
          })}
        </div>
      ) : null}

      <div className="mt-auto px-5 py-3 border-t border-border bg-surface">
        {migrations.length > 0 ? (
          <div className="space-y-1">
            <p className="text-micro text-slate">
              Grade migrations{" "}
              <span className="text-navy/60">(ringed on the chart)</span>
            </p>
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
            {last.grade ? ` — held at ${last.grade}` : ""}.
          </p>
        )}
        <p className="mt-2 text-micro text-slate leading-relaxed">
          Each point is the assessment recorded that day, not a recomputation.
          Days with no refresh are shown as breaks. Scores live on the 0–1
          interval; the axis spans the observed range.
        </p>
      </div>
    </div>
  );
}

/**
 * The composite score trend, with a hollow marker on every grade-migration day.
 *
 * `<ReferenceDot>` becomes a second SCATTER series rather than a `markPoint`: a
 * markPoint draws a pin, and a scatter point is the same hollow ring the Recharts
 * version drew. It also means `MarkPointComponent` need not be registered.
 *
 * An unassessed day stays `null`, so the line breaks there — it always did, with
 * `connectNulls={false}` — and `gapAwareData` adds the missing half of that rule:
 * a single assessed day surrounded by unassessed ones keeps its own marker.
 */
function CompositeTrendChart({
  rows,
  migrations,
  domain,
}: {
  rows: ReadonlyArray<{
    label: string;
    date: Date;
    composite: number | null;
    grade: string | null;
  }>;
  migrations: ReadonlyArray<{
    day: string;
    date: Date;
    composite: number | null;
  }>;
  domain: [number, number];
}) {
  const tokens = useChartTokens();
  const color = seriesColor(tokens, 0);
  const labelIndex = new Map(rows.map((row, index) => [row.label, index]));
  const titles = rows.map((row) => {
    const grade = row.grade ? ` · grade ${row.grade}` : "";
    return `${fmtDateUTC(row.date)}${grade}`;
  });

  const option: BiEChartsOption = {
    grid: { left: 4, right: 12, top: 8, bottom: 4, containLabel: true },
    xAxis: {
      type: "category",
      data: rows.map((row) => row.label),
      axisLabel: { hideOverlap: true },
    },
    yAxis: {
      type: "value",
      min: domain[0],
      max: domain[1],
      axisLabel: { formatter: (value: number) => value.toFixed(2) },
    },
    tooltip: {
      trigger: "item",
      formatter: itemTooltip(titles, (index) => {
        const row = rows[index];
        return row === undefined || row.composite === null
          ? []
          : [
              {
                label: "Composite",
                value: row.composite.toFixed(4),
                color,
              },
            ];
      }),
    },
    series: [
      {
        ...LINE_SERIES_BASE,
        name: "Composite",
        smooth: true,
        showSymbol: true,
        symbolSize: 5,
        lineStyle: { color, width: 2 },
        itemStyle: { color },
        data: gapAwareData(rows.map((row) => row.composite)),
      },
      {
        type: "scatter",
        name: "Grade migration",
        animation: false,
        symbolSize: 10,
        itemStyle: {
          color: "transparent",
          borderColor: tokens.caution,
          borderWidth: 1.75,
        },
        data: migrations.flatMap((migration) => {
          const index = labelIndex.get(shortLabel(migration.date));
          return migration.composite === null || index === undefined
            ? []
            : [[index, migration.composite]];
        }),
      },
    ],
  } as BiEChartsOption;

  return (
    <EChart
      option={option}
      height={190}
      ariaLabel={`Composite financial-strength score across ${rows.length} days, with ${migrations.length} grade migrations marked`}
    />
  );
}

/** One component score as a bare 44px line, axes hidden. */
function ComponentSparkChart({
  rows,
  code,
  label,
  colorIndex,
  domain,
}: {
  rows: ReadonlyArray<Row>;
  code: string;
  label: string;
  colorIndex: number;
  domain: [number, number];
}) {
  const tokens = useChartTokens();
  const color = seriesColor(tokens, colorIndex);
  const values = rows.map((row) => row[`c_${code}`]);
  const titles = rows.map((row) => fmtDateUTC(row.date));

  const option: BiEChartsOption = {
    grid: { left: 2, right: 2, top: 4, bottom: 2 },
    xAxis: {
      type: "category",
      data: rows.map((row) => row.label),
      show: false,
    },
    yAxis: { type: "value", min: domain[0], max: domain[1], show: false },
    tooltip: {
      trigger: "item",
      formatter: itemTooltip(titles, (index) => {
        const value = values[index];
        return value === null || value === undefined
          ? []
          : [{ label, value: value.toFixed(4), color }];
      }),
    },
    series: [
      {
        ...LINE_SERIES_BASE,
        name: label,
        smooth: true,
        lineStyle: { color, width: 1.5 },
        itemStyle: { color },
        data: gapAwareData(values, 3),
      },
    ],
  } as BiEChartsOption;

  return (
    <EChart
      option={option}
      height={44}
      ariaLabel={`${label} score trend across ${rows.length} days`}
    />
  );
}
