'use client';

import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { FtpCurvePointRead } from '@aequoros/risk-service-api';
import {
  CHART_GRID,
  axisProps,
  chartLegendProps,
  chartTooltipProps,
  seriesColor,
} from '@/lib/chartTheme';
import { num } from '@/lib/api/values';
import { fmtPct } from '@/lib/format';

type StackPoint = {
  tenorLabel: string;
  baseYieldPct: number;
  liquidityPremiumPct: number;
  fundingSpreadPct: number;
  ftpRatePct: number;
};

/**
 * The transfer curve as its published composition: base market yield with the
 * liquidity-premium and funding-spread bands stacked on top (bps converted to
 * percentage points for display only), and the resulting FTP rate traced as a
 * line along the top of the stack.
 */
export default function TransferCurveChart({
  curve,
  height = 300,
}: {
  curve: FtpCurvePointRead[];
  height?: number;
}) {
  const data: StackPoint[] = curve.map((c) => ({
    tenorLabel: c.tenorLabel,
    baseYieldPct: num(c.baseYieldPct),
    liquidityPremiumPct: num(c.liquidityPremiumBps) / 100,
    fundingSpreadPct: num(c.fundingSpreadBps) / 100,
    ftpRatePct: num(c.ftpRatePct),
  }));

  const minBase = Math.min(...data.map((d) => d.baseYieldPct));
  const maxFtp = Math.max(...data.map((d) => d.ftpRatePct));

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 8, right: 20, bottom: 4, left: 4 }}>
        <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="tenorLabel" {...axisProps} />
        <YAxis
          {...axisProps}
          domain={[Math.floor(minBase - 1), Math.ceil(maxFtp + 1)]}
          tickFormatter={(v: number) => `${v}%`}
          width={48}
        />
        <Tooltip
          {...chartTooltipProps}
          formatter={(value: number | string, name) => {
            const v = typeof value === 'number' ? value : Number(value);
            if (name === 'Liquidity premium' || name === 'Funding spread') {
              return [`${(v * 100).toFixed(0)} bp`, name];
            }
            return [fmtPct(v, 2), name];
          }}
        />
        <Legend {...chartLegendProps} />
        <Area
          type="monotone"
          dataKey="baseYieldPct"
          name="Base market yield"
          stackId="curve"
          stroke={seriesColor(0)}
          fill={seriesColor(0)}
          fillOpacity={0.25}
          isAnimationActive={false}
        />
        <Area
          type="monotone"
          dataKey="liquidityPremiumPct"
          name="Liquidity premium"
          stackId="curve"
          stroke={seriesColor(1)}
          fill={seriesColor(1)}
          fillOpacity={0.35}
          isAnimationActive={false}
        />
        <Area
          type="monotone"
          dataKey="fundingSpreadPct"
          name="Funding spread"
          stackId="curve"
          stroke={seriesColor(2)}
          fill={seriesColor(2)}
          fillOpacity={0.35}
          isAnimationActive={false}
        />
        <Line
          type="monotone"
          dataKey="ftpRatePct"
          name="FTP rate"
          stroke={seriesColor(3)}
          strokeWidth={2}
          dot={{ r: 3 }}
          isAnimationActive={false}
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
