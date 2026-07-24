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
  axisProps,
  chartTooltipProps,
  seriesColor,
} from '@/lib/chartTheme';
import { fmtCurrencySigned, fmtPct } from '@/lib/format';

export type MarginBarPoint = {
  label: string;
  /** Signed value (margin % or contribution GHS). */
  value: number;
  side: 'asset' | 'liability' | 'mixed';
  /** Highlights the bar in the critical tone (e.g. below the margin floor). */
  flagged?: boolean;
};

/**
 * Horizontal signed bars for product / business-line profitability. Asset
 * books and liability books get distinct series colors; flagged rows (below
 * the margin floor) render in the critical tone.
 */
export default function MarginBars({
  data,
  mode,
  floorPct,
  height = 300,
}: {
  data: MarginBarPoint[];
  /** 'pct' formats as margin %, 'ghs' as GHS contribution. */
  mode: 'pct' | 'ghs';
  /** Optional margin-floor reference line (pct mode). */
  floorPct?: number;
  height?: number;
}) {
  const fmt = (v: number) =>
    mode === 'pct' ? fmtPct(v, 2) : fmtCurrencySigned(v);
  const axisFmt = (v: number) =>
    mode === 'pct' ? `${v}%` : `${(v / 1_000_000).toFixed(0)}M`;

  const fill = (p: MarginBarPoint): string => {
    if (p.flagged) return CHART_CRIT;
    return p.side === 'asset' ? seriesColor(0) : p.side === 'liability' ? seriesColor(1) : seriesColor(2);
  };

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart
        data={data}
        layout="vertical"
        margin={{ top: 8, right: 24, bottom: 4, left: 8 }}
      >
        <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" horizontal={false} />
        <XAxis type="number" {...axisProps} tickFormatter={axisFmt} />
        <YAxis
          type="category"
          dataKey="label"
          {...axisProps}
          axisLine={false}
          width={168}
        />
        <Tooltip
          {...chartTooltipProps}
          formatter={(value: number | string, _name, item) => {
            const point = item?.payload as MarginBarPoint | undefined;
            const v = typeof value === 'number' ? value : Number(value);
            return [
              `${fmt(v)}${point?.flagged ? ' · below floor' : ''}`,
              point
                ? point.side === 'asset'
                  ? 'Asset book'
                  : point.side === 'liability'
                  ? 'Funding book'
                  : 'Mixed'
                : '',
            ];
          }}
        />
        <ReferenceLine x={0} stroke={CHART_AXIS} />
        {mode === 'pct' && floorPct !== undefined && (
          <ReferenceLine
            x={floorPct}
            stroke={CHART_CRIT}
            strokeDasharray="5 4"
            label={{
              value: `Floor ${floorPct.toFixed(1)}%`,
              position: 'insideTopRight',
              fontSize: 10,
              fill: CHART_CRIT,
            }}
          />
        )}
        <Bar dataKey="value" barSize={14} radius={[2, 2, 2, 2]}>
          {data.map((p) => (
            <Cell key={p.label} fill={fill(p)} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
