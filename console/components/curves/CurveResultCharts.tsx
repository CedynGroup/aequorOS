'use client';

import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
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
import type { DeskCurveConstructResponse } from '@/lib/api';

/**
 * Interactive recharts visuals for the Curve Construction workspace — the
 * Bloomberg/Eikon "curve analysis" surface. Replaces the old dependency-free
 * single-series SVG LineChart with crosshair tooltips and dual-axis composition
 * so the term structure (forward yield + discount factor) reads as one picture,
 * and the calibration pillars show quote-vs-solved-DF side by side.
 *
 * Theme comes entirely from lib/chartTheme (the shared --chart-* tokens); no
 * colors are hard-coded here.
 */

function fmtMonths(months: number): string {
  if (months <= 0) return 'spot';
  if (months % 12 === 0) return `${months / 12}y`;
  return `${months}m`;
}

const YIELD = seriesColor(0);
const DF = seriesColor(2);
const QUOTE = seriesColor(4);

export function CurveResultCharts({ result }: { result: DeskCurveConstructResponse }) {
  const freq = result.curve_frequency_months || 1;

  // Term structure: forward yield (%) on the left axis, discount factor on the
  // right. Spot (row 0) has no forward yield, so it is null and connectNulls
  // keeps the DF line continuous.
  const termData = result.rows.map((row, i) => ({
    tenor: fmtMonths(i * freq),
    yieldPct: i === 0 ? null : row.forward_yield * 100,
    df: row.discount_factor,
  }));

  // Calibration pillars: instrument quote (%) as bars, solved discount factor
  // as a line on the right axis.
  const pillarData = result.pillars.map((p) => ({
    label: `${p.instrument} ${p.tenor}`,
    quotePct: p.quote * 100,
    df: p.discount_factor,
  }));

  return (
    <div className="grid gap-5 p-5 lg:grid-cols-2">
      <ChartFrame
        title="Term structure"
        subtitle={
          <>
            Forward yield ({result.output_basis}) and discount factor across the grid
          </>
        }
        height={300}
      >
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={termData} margin={chartMargins}>
            <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" vertical={false} />
            <XAxis {...axisProps} dataKey="tenor" minTickGap={16} />
            <YAxis
              yAxisId="yield"
              {...axisProps}
              width={52}
              tickFormatter={(v: number) => `${v.toFixed(1)}%`}
            />
            <YAxis
              yAxisId="df"
              orientation="right"
              {...axisProps}
              width={48}
              domain={[0, 1]}
              tickFormatter={(v: number) => v.toFixed(2)}
            />
            <Tooltip
              {...chartTooltipProps}
              formatter={(value: number | string, name) =>
                name === 'Discount factor'
                  ? [Number(value).toFixed(6), name]
                  : [`${Number(value).toFixed(3)}%`, name]
              }
              labelFormatter={(label) => `Tenor ${label}`}
            />
            <Legend {...chartLegendProps} />
            <Line
              yAxisId="yield"
              type="monotone"
              dataKey="yieldPct"
              name="Forward yield"
              stroke={YIELD}
              strokeWidth={2}
              dot={{ r: 2 }}
              connectNulls
            />
            <Line
              yAxisId="df"
              type="monotone"
              dataKey="df"
              name="Discount factor"
              stroke={DF}
              strokeWidth={2}
              strokeDasharray="4 2"
              dot={{ r: 2 }}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </ChartFrame>

      <ChartFrame
        title="Pillar nodes"
        subtitle="Calibration instrument quotes and the solved discount factor at each pillar"
        height={300}
      >
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={pillarData} margin={chartMargins}>
            <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" vertical={false} />
            <XAxis {...axisProps} dataKey="label" minTickGap={8} angle={-15} textAnchor="end" height={48} />
            <YAxis
              yAxisId="quote"
              {...axisProps}
              width={52}
              tickFormatter={(v: number) => `${v.toFixed(1)}%`}
            />
            <YAxis
              yAxisId="df"
              orientation="right"
              {...axisProps}
              width={48}
              domain={[0, 1]}
              tickFormatter={(v: number) => v.toFixed(2)}
            />
            <Tooltip
              {...chartTooltipProps}
              formatter={(value: number | string, name) =>
                name === 'Discount factor'
                  ? [Number(value).toFixed(6), name]
                  : [`${Number(value).toFixed(3)}%`, name]
              }
            />
            <Legend {...chartLegendProps} />
            <Bar yAxisId="quote" dataKey="quotePct" name="Quote" fill={QUOTE} maxBarSize={26} radius={[2, 2, 0, 0]} />
            <Line
              yAxisId="df"
              type="monotone"
              dataKey="df"
              name="Discount factor"
              stroke={DF}
              strokeWidth={2}
              dot={{ r: 2 }}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </ChartFrame>
    </div>
  );
}
