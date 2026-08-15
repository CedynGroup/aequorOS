'use client';

import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { ChartFrame } from '@/components/ui';
import {
  CHART_GRID,
  axisProps,
  chartLegendProps,
  chartMargins,
  chartTooltipProps,
  seriesColor,
} from '@/lib/chartTheme';
import { fmtDate } from '@/lib/format';
import type { FxForwardConstructResponse } from '@/lib/api';

/**
 * The outright FX-forward term structure. The forward rate rides the left axis
 * as a line (with the spot marked as a reference line), and the forward points
 * ride the right axis as bars — points are tiny relative to the outright, so a
 * shared axis would flatten them. Theme is entirely token-driven.
 */

const RATE = seriesColor(0);
const POINTS = seriesColor(4);

export function FxForwardChart({ result }: { result: FxForwardConstructResponse }) {
  const data = result.rows.map((row) => ({
    date: fmtDate(row.date),
    rate: row.forward_rate,
    points: row.forward_points,
  }));

  return (
    <ChartFrame
      title={
        <span className="inline-flex items-center gap-2">
          Forward curve <span className="font-mono text-slate">{result.pair}</span>
        </span>
      }
      subtitle="Outright forward vs spot, with forward points on the right axis"
      height={300}
    >
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={chartMargins}>
          <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" vertical={false} />
          <XAxis {...axisProps} dataKey="date" minTickGap={16} />
          <YAxis
            yAxisId="rate"
            {...axisProps}
            width={64}
            domain={['auto', 'auto']}
            tickFormatter={(v: number) => v.toFixed(4)}
          />
          <YAxis
            yAxisId="points"
            orientation="right"
            {...axisProps}
            width={56}
            tickFormatter={(v: number) => v.toFixed(3)}
          />
          <Tooltip
            {...chartTooltipProps}
            formatter={(value: number | string, name) =>
              name === 'Forward points'
                ? [`${Number(value) >= 0 ? '+' : ''}${Number(value).toFixed(4)}`, name]
                : [Number(value).toFixed(4), name]
            }
            labelFormatter={(label) => `Value date ${label}`}
          />
          <Legend {...chartLegendProps} />
          <ReferenceLine
            yAxisId="rate"
            y={result.spot}
            stroke="rgb(var(--line-strong))"
            strokeDasharray="4 3"
            label={{ value: `spot ${result.spot.toFixed(4)}`, position: 'insideTopLeft' }}
          />
          <Bar yAxisId="points" dataKey="points" name="Forward points" fill={POINTS} maxBarSize={26} radius={[2, 2, 0, 0]} />
          <Line
            yAxisId="rate"
            type="monotone"
            dataKey="rate"
            name="Outright forward"
            stroke={RATE}
            strokeWidth={2}
            dot={{ r: 2.5 }}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}
