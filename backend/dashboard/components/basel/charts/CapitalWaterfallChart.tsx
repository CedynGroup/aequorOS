'use client';

/**
 * Capital-stack waterfall: CET1 components → regulatory deductions → AT1 →
 * Tier 2 → total capital. Rendered as floating bars (transparent base +
 * visible segment) so each tier's contribution to the total is legible.
 * All step values come from the stored capital structure — display only.
 */

import {
  ResponsiveContainer,
  BarChart,
  Bar,
  Cell,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceLine,
} from 'recharts';
import {
  CHART_ACCENT,
  CHART_AXIS,
  CHART_CRIT,
  CHART_OK,
  axisProps,
  chartTooltipProps,
  seriesColor,
} from '@/lib/chartTheme';
import { fmtCurrency } from '@/lib/format';

type Step = {
  name: string;
  base: number;
  delta: number;
  signed: number;
  color: string;
};

export default function CapitalWaterfallChart({
  cet1Gross,
  deductions,
  at1,
  tier2,
  total,
  height = 280,
}: {
  /** CET1 components before deductions. */
  cet1Gross: number;
  /** Regulatory deductions (positive magnitude). */
  deductions: number;
  at1: number;
  tier2: number;
  total: number;
  height?: number;
}) {
  const cet1Net = cet1Gross - deductions;
  const tier1 = cet1Net + at1;
  const steps: Step[] = [
    {
      name: 'CET1 components',
      base: 0,
      delta: cet1Gross,
      signed: cet1Gross,
      color: CHART_ACCENT,
    },
    {
      name: 'Deductions',
      base: cet1Net,
      delta: deductions,
      signed: -deductions,
      color: CHART_CRIT,
    },
    {
      name: 'AT1',
      base: cet1Net,
      delta: at1,
      signed: at1,
      color: seriesColor(1),
    },
    {
      name: 'Tier 2',
      base: tier1,
      delta: tier2,
      signed: tier2,
      color: seriesColor(2),
    },
    {
      name: 'Total capital',
      base: 0,
      delta: total,
      signed: total,
      color: CHART_OK,
    },
  ];

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={steps} margin={{ top: 8, right: 16, bottom: 4, left: 8 }}>
        <XAxis dataKey="name" {...axisProps} interval={0} />
        <YAxis
          axisLine={false}
          tickLine={false}
          tick={axisProps.tick}
          tickFormatter={(v: number) => fmtCurrency(v, 'GHS', { decimals: 0 })}
          width={72}
        />
        <Tooltip
          {...chartTooltipProps}
          cursor={{ fill: 'rgb(var(--surface-hover))' }}
          formatter={(value: number, _name, item) => {
            const signed = (item?.payload as Step | undefined)?.signed ?? value;
            return [
              `${signed < 0 ? '−' : ''}${fmtCurrency(Math.abs(signed), 'GHS')}`,
              'Contribution',
            ];
          }}
        />
        <ReferenceLine y={0} stroke={CHART_AXIS} strokeWidth={1} />
        <Bar
          dataKey="base"
          stackId="wf"
          fill="transparent"
          isAnimationActive={false}
          legendType="none"
          tooltipType="none"
        />
        <Bar dataKey="delta" stackId="wf" maxBarSize={56} radius={[2, 2, 0, 0]}>
          {steps.map((step) => (
            <Cell key={step.name} fill={step.color} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
