'use client';

/**
 * Projection waterfall — opening balance → per-component deltas → closing
 * balance, built with the invisible-offset stacked-bar technique. Totals
 * render in the primary series color; deltas in risk-semantic green/red.
 * Token-themed via lib/chartTheme.ts.
 */

import {
  ResponsiveContainer,
  BarChart,
  Bar,
  Cell,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  ReferenceLine,
} from 'recharts';
import {
  axisProps,
  CHART_AXIS,
  CHART_CRIT,
  CHART_GRID,
  CHART_OK,
  chartTooltipProps,
  seriesColor,
} from '@/lib/chartTheme';
import { fmtCurrency, fmtCurrencySigned } from '@/lib/format';

export type WaterfallStep =
  | { kind: 'total'; label: string; value: number }
  | { kind: 'delta'; label: string; value: number };

type Row = {
  label: string;
  /** Invisible spacer below the visible segment. */
  offset: number;
  /** Visible segment height (always positive). */
  segment: number;
  /** Signed value for the tooltip. */
  signed: number;
  kind: 'total' | 'delta';
};

function buildRows(steps: WaterfallStep[]): Row[] {
  let running = 0;
  return steps.map((step) => {
    if (step.kind === 'total') {
      running = step.value;
      return {
        label: step.label,
        offset: 0,
        segment: step.value,
        signed: step.value,
        kind: 'total' as const,
      };
    }
    const start = running;
    running += step.value;
    return {
      label: step.label,
      offset: Math.min(start, running),
      segment: Math.abs(step.value),
      signed: step.value,
      kind: 'delta' as const,
    };
  });
}

export default function WaterfallChart({
  steps,
  height = 300,
}: {
  steps: WaterfallStep[];
  height?: number;
}) {
  const rows = buildRows(steps);

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={rows} margin={{ top: 8, right: 16, left: 4, bottom: 4 }}>
        <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="label" {...axisProps} interval={0} />
        <YAxis
          {...axisProps}
          axisLine={false}
          width={64}
          tickFormatter={(v: number) => fmtCurrency(v, undefined, { decimals: 1 })}
        />
        <Tooltip
          {...chartTooltipProps}
          cursor={{ fill: 'transparent' }}
          formatter={(v: number, name: string, entry) => {
            const row = entry?.payload as Row | undefined;
            if (!row || name === 'offset') return [null as unknown as string, ''];
            return [
              row.kind === 'total'
                ? fmtCurrency(row.signed)
                : fmtCurrencySigned(row.signed),
              row.kind === 'total' ? 'Balance' : 'Change',
            ];
          }}
        />
        <ReferenceLine y={0} stroke={CHART_AXIS} />
        <Bar dataKey="offset" stackId="w" fill="transparent" isAnimationActive={false} />
        <Bar dataKey="segment" stackId="w" maxBarSize={48} radius={[3, 3, 0, 0]}>
          {rows.map((row, i) => (
            <Cell
              key={i}
              fill={
                row.kind === 'total'
                  ? seriesColor(0)
                  : row.signed >= 0
                  ? CHART_OK
                  : CHART_CRIT
              }
              fillOpacity={row.kind === 'total' ? 0.9 : 0.8}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
