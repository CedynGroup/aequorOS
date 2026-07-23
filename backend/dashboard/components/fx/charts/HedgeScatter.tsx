'use client';

import {
  CartesianGrid,
  Cell,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  CHART_CRIT,
  CHART_GRID,
  CHART_OK,
  axisProps,
  chartTooltipProps,
} from '@/lib/chartTheme';
import { fmtCurrencySigned, fmtPct } from '@/lib/format';

export type HedgePoint = {
  hedgeId: string;
  pair: string;
  instrument: string;
  r2Pct: number;
  offsetPct: number;
  mtmGhs: number;
  effective: boolean;
};

/**
 * IFRS 9 dual-test effectiveness scatter: dollar-offset ratio on X,
 * prospective R² on Y, with the pass region (offset within the band AND R²
 * above the floor) shaded. Band edges come from the stored run's parameter
 * snapshot when available.
 */
export default function HedgeScatter({
  data,
  r2MinPct,
  offsetLowPct,
  offsetHighPct,
  height = 300,
}: {
  data: HedgePoint[];
  r2MinPct: number;
  offsetLowPct: number;
  offsetHighPct: number;
  height?: number;
}) {
  const xValues = data.map((d) => d.offsetPct);
  const yValues = data.map((d) => d.r2Pct);
  const xMin = Math.min(offsetLowPct - 15, ...xValues, 60);
  const xMax = Math.max(offsetHighPct + 15, ...xValues, 140);
  const yMin = Math.max(0, Math.min(r2MinPct - 25, ...yValues));
  const yMax = 100;

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ScatterChart margin={{ top: 8, right: 16, bottom: 12, left: 8 }}>
        <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" />
        <XAxis
          type="number"
          dataKey="offsetPct"
          domain={[Math.floor(xMin), Math.ceil(xMax)]}
          {...axisProps}
          tickFormatter={(v: number) => `${v}%`}
          label={{
            value: 'Dollar-offset ratio',
            position: 'insideBottom',
            offset: -6,
            fontSize: 11,
            fill: 'rgb(var(--text-muted))',
          }}
        />
        <YAxis
          type="number"
          dataKey="r2Pct"
          domain={[Math.floor(yMin), yMax]}
          {...axisProps}
          tickFormatter={(v: number) => `${v}%`}
          width={44}
        />
        {/* IFRS 9 pass region: offset within band AND R² above the floor. */}
        <ReferenceArea
          x1={offsetLowPct}
          x2={offsetHighPct}
          y1={r2MinPct}
          y2={yMax}
          fill={CHART_OK}
          fillOpacity={0.08}
          stroke={CHART_OK}
          strokeOpacity={0.35}
          strokeDasharray="4 3"
        />
        <ReferenceLine y={r2MinPct} stroke={CHART_OK} strokeDasharray="4 3" strokeOpacity={0.5} />
        <Tooltip
          {...chartTooltipProps}
          cursor={{ strokeDasharray: '3 3', stroke: 'rgb(var(--line-strong))' }}
          content={({ active, payload }) => {
            const point = payload?.[0]?.payload as HedgePoint | undefined;
            if (!active || !point) return null;
            return (
              <div style={chartTooltipProps.contentStyle}>
                <p style={chartTooltipProps.labelStyle}>
                  {point.hedgeId} · {point.pair}
                </p>
                <p style={chartTooltipProps.itemStyle}>{point.instrument}</p>
                <p style={chartTooltipProps.itemStyle}>
                  R² {fmtPct(point.r2Pct, 1)} · Offset {fmtPct(point.offsetPct, 1)}
                </p>
                <p style={chartTooltipProps.itemStyle}>
                  MTM {fmtCurrencySigned(point.mtmGhs)} ·{' '}
                  {point.effective ? 'Effective' : 'Ineffective'}
                </p>
              </div>
            );
          }}
        />
        <Scatter data={data} isAnimationActive={false}>
          {data.map((point) => (
            <Cell
              key={point.hedgeId}
              fill={point.effective ? CHART_OK : CHART_CRIT}
              fillOpacity={0.85}
            />
          ))}
        </Scatter>
      </ScatterChart>
    </ResponsiveContainer>
  );
}
