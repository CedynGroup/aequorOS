'use client';

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  CHART_AXIS,
  CHART_CRIT,
  CHART_GRID,
  CHART_OK,
  chartTooltipProps,
} from '@/lib/chartTheme';
import { fmtCurrencySigned, fmtPct } from '@/lib/format';

export type TornadoPoint = {
  label: string;
  /** ΔEVE in GHS (signed backend figure). */
  value: number;
  /** ΔEVE as % of Tier 1 (signed backend figure) for the tooltip. */
  pctTier1?: number;
  /** Backend breach flag — colors the bar critical. */
  breach?: boolean;
};

/**
 * Horizontal ΔEVE tornado: signed bars sorted by magnitude (largest loss on
 * top), colored by the backend's per-scenario breach flag. Display-only —
 * every figure is an engine output, never recomputed here.
 */
export default function TornadoChart({
  data,
  height = 300,
  categoryWidth = 118,
  sort = true,
}: {
  data: TornadoPoint[];
  height?: number;
  categoryWidth?: number;
  /** Sort by |ΔEVE| descending (tornado ordering). */
  sort?: boolean;
}) {
  const rows = sort
    ? [...data].sort((a, b) => Math.abs(b.value) - Math.abs(a.value))
    : data;

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart
        data={rows}
        layout="vertical"
        margin={{ top: 8, right: 24, left: 8, bottom: 4 }}
      >
        <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" vertical horizontal={false} />
        <XAxis
          type="number"
          axisLine={{ stroke: CHART_AXIS }}
          tickLine={false}
          tick={{ fontSize: 11 }}
          tickFormatter={(v: number) => `${(v / 1_000_000).toFixed(0)}M`}
        />
        <YAxis
          type="category"
          dataKey="label"
          axisLine={false}
          tickLine={false}
          width={categoryWidth}
          tick={{ fontSize: 11 }}
        />
        <Tooltip
          {...chartTooltipProps}
          cursor={{ fill: 'rgb(var(--surface-hover))' }}
          formatter={(v: number, _name, entry) => {
            const pct = (entry?.payload as TornadoPoint | undefined)?.pctTier1;
            const suffix =
              pct !== undefined ? ` (${fmtPct(pct, 2)} of Tier 1)` : '';
            return [`${fmtCurrencySigned(v)}${suffix}`, 'ΔEVE'];
          }}
        />
        <ReferenceLine x={0} stroke={CHART_AXIS} />
        <Bar dataKey="value" radius={[0, 2, 2, 0]} maxBarSize={22}>
          {rows.map((d, i) => (
            <Cell key={i} fill={d.breach ? CHART_CRIT : CHART_OK} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
