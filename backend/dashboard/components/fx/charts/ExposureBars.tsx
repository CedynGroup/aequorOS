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
  CHART_WARN,
  axisProps,
  chartTooltipProps,
} from '@/lib/chartTheme';
import { fmtCurrencySigned, fmtPct } from '@/lib/format';

export type ExposureBarPoint = {
  currency: string;
  /** Signed net open position in GHS (long positive, short negative). */
  netGhs: number;
  /** |NOP| as % of Tier 1 — drives the ok / warn / crit coloring. */
  absPctTier1: number;
  withinSingleLimit: boolean;
};

/**
 * Per-currency signed NOP bars. Bar direction encodes long / short; color
 * encodes the single-currency limit state (ok, approaching at >= 80% of the
 * limit, breach past it). All figures come straight from the FX payload.
 */
export default function ExposureBars({
  data,
  singleLimitPct,
  height = 300,
}: {
  data: ExposureBarPoint[];
  singleLimitPct: number;
  height?: number;
}) {
  const color = (p: ExposureBarPoint): string => {
    if (!p.withinSingleLimit) return CHART_CRIT;
    if (singleLimitPct > 0 && p.absPctTier1 >= singleLimitPct * 0.8) {
      return CHART_WARN;
    }
    return CHART_OK;
  };

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart
        data={data}
        layout="vertical"
        margin={{ top: 8, right: 24, bottom: 4, left: 8 }}
      >
        <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" horizontal={false} />
        <XAxis
          type="number"
          {...axisProps}
          tickFormatter={(v: number) => `${(v / 1_000_000).toFixed(0)}M`}
        />
        <YAxis
          type="category"
          dataKey="currency"
          {...axisProps}
          axisLine={false}
          width={52}
        />
        <Tooltip
          {...chartTooltipProps}
          formatter={(value: number | string, _name, item) => {
            const point = item?.payload as ExposureBarPoint | undefined;
            const net = typeof value === 'number' ? value : Number(value);
            return [
              `${fmtCurrencySigned(net)} · ${fmtPct(point?.absPctTier1 ?? 0, 2)} of Tier 1`,
              point ? `${point.currency} ${net >= 0 ? 'long' : 'short'}` : 'Net NOP',
            ];
          }}
        />
        <ReferenceLine x={0} stroke={CHART_AXIS} />
        <Bar dataKey="netGhs" barSize={16} radius={[2, 2, 2, 2]}>
          {data.map((p) => (
            <Cell key={p.currency} fill={color(p)} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
