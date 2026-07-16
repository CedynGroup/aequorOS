'use client';

/**
 * Curve board: one line per currency across the tenor axis, plus a per-point
 * rate table. Rates arrive as decimal fractions (0.185) and render as
 * percentages; every curve carries its source attribution + freshness chip.
 */

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { YieldCurveViewRead } from '@aequoros/risk-service-api';
import ChartFrame from '@/components/ui/ChartFrame';
import SectionCard from '@/components/ui/SectionCard';
import DataTable, { type Column } from '@/components/ui/DataTable';
import {
  axisProps,
  chartLegendProps,
  chartMargins,
  chartTooltipProps,
  CHART_GRID,
  seriesColor,
} from '@/lib/chartTheme';
import { num, fmtDateUTC } from '@/lib/api/values';
import { fmtPct } from '@/lib/format';
import AttributionChip from './AttributionChip';

/** "1M", "9M", "1Y", "18M", "5Y" — tenor-months axis label. */
export function tenorLabel(months: number): string {
  if (months < 12 || months % 12 !== 0) return `${months}M`;
  return `${months / 12}Y`;
}

type CurvePoint = { tenorMonths: number } & Record<string, number>;

/** Merge every curve into one tenor-keyed series table for the chart. */
function mergePoints(curves: YieldCurveViewRead[]): CurvePoint[] {
  const byTenor = new Map<number, CurvePoint>();
  for (const curve of curves) {
    for (const point of curve.points) {
      const row =
        byTenor.get(point.tenorMonths) ??
        ({ tenorMonths: point.tenorMonths } as CurvePoint);
      row[curve.currency] = num(point.rate) * 100;
      byTenor.set(point.tenorMonths, row);
    }
  }
  return [...byTenor.values()].sort((a, b) => a.tenorMonths - b.tenorMonths);
}

export default function CurveBoard({ curves }: { curves: YieldCurveViewRead[] }) {
  const chartData = mergePoints(curves);

  const tableColumns: Column<CurvePoint>[] = [
    {
      key: 'tenor',
      header: 'Tenor',
      render: (row) => (
        <span className="font-mono text-caption">{tenorLabel(row.tenorMonths)}</span>
      ),
      width: '16%',
    },
    ...curves.map((curve) => ({
      key: curve.currency,
      header: curve.currency,
      numeric: true,
      render: (row: CurvePoint) => {
        const value = row[curve.currency];
        return value === undefined ? '—' : fmtPct(value, 2);
      },
    })),
  ];

  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
      <ChartFrame
        title="Yield curves"
        subtitle="Authoritative curve per currency at the as-of date"
        height={300}
        footer={
          <>
            {curves.map((curve) => (
              <span key={curve.currency} className="inline-flex items-center gap-1.5">
                <span className="font-medium text-navy">{curve.currency}</span>
                <span className="font-mono">{curve.curveName}</span>
                <AttributionChip attribution={curve.attribution} />
              </span>
            ))}
          </>
        }
      >
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chartData} margin={chartMargins}>
            <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" vertical={false} />
            <XAxis
              {...axisProps}
              dataKey="tenorMonths"
              tickFormatter={(value: number) => tenorLabel(value)}
            />
            <YAxis
              {...axisProps}
              tickFormatter={(value: number) => `${value.toFixed(1)}%`}
              width={52}
            />
            <Tooltip
              {...chartTooltipProps}
              labelFormatter={(value) => `Tenor ${tenorLabel(Number(value))}`}
              formatter={(value: number | string, name: string) => [
                fmtPct(Number(value), 2),
                name,
              ]}
            />
            <Legend {...chartLegendProps} />
            {curves.map((curve, index) => (
              <Line
                key={curve.currency}
                type="monotone"
                dataKey={curve.currency}
                name={curve.currency}
                stroke={seriesColor(index)}
                strokeWidth={2}
                dot={{ r: 2.5 }}
                connectNulls
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </ChartFrame>

      <SectionCard
        title="Curve points"
        subtitle="Per-tenor rates behind the chart"
        noPadding
        footer={
          <span>
            As of{' '}
            <span className="font-mono text-navy">
              {curves.map((curve) => fmtDateUTC(curve.asOfDate)).join(' · ')}
            </span>
          </span>
        }
      >
        <DataTable
          columns={tableColumns}
          rows={chartData}
          density="compact"
          stickyHeader
          maxHeight={300}
        />
      </SectionCard>
    </div>
  );
}
