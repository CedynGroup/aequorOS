'use client';

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  CHART_GRID,
  CHART_OK,
  axisProps,
  chartTooltipProps,
  seriesColor,
} from '@/lib/chartTheme';
import { fmtCurrency, fmtCurrencySigned } from '@/lib/format';

export type VarWaterfallInput = {
  /** Per-currency standalone VaR steps (all >= 0). */
  standalone: { currency: string; varGhs: number }[];
  /** Diversification benefit (>= 0; plotted as a downward step). */
  diversificationBenefitGhs: number;
  /** Diversified portfolio VaR (the closing total). */
  portfolioVarGhs: number;
};

type Step = {
  label: string;
  base: number;
  span: number;
  signed: number;
  kind: 'currency' | 'benefit' | 'total';
};

/**
 * Waterfall decomposition of the 99% 1-day VaR: standalone per-currency VaR
 * steps stack up to the undiversified sum, the diversification benefit steps
 * back down, and the diversified portfolio VaR closes the bridge. Bars only
 * position backend figures — no risk math happens here.
 */
export default function VarWaterfall({
  input,
  height = 300,
}: {
  input: VarWaterfallInput;
  height?: number;
}) {
  const steps: Step[] = [];
  let running = 0;
  input.standalone.forEach((s) => {
    steps.push({
      label: s.currency,
      base: running,
      span: s.varGhs,
      signed: s.varGhs,
      kind: 'currency',
    });
    running += s.varGhs;
  });
  steps.push({
    label: 'Diversification',
    base: running - input.diversificationBenefitGhs,
    span: input.diversificationBenefitGhs,
    signed: -input.diversificationBenefitGhs,
    kind: 'benefit',
  });
  steps.push({
    label: 'Portfolio VaR',
    base: 0,
    span: input.portfolioVarGhs,
    signed: input.portfolioVarGhs,
    kind: 'total',
  });

  const fill = (step: Step): string =>
    step.kind === 'benefit'
      ? CHART_OK
      : step.kind === 'total'
      ? seriesColor(1)
      : seriesColor(0);

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={steps} margin={{ top: 8, right: 12, bottom: 4, left: 8 }}>
        <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="label" {...axisProps} interval={0} />
        <YAxis
          {...axisProps}
          tickFormatter={(v: number) => `${(v / 1_000_000).toFixed(1)}M`}
        />
        <Tooltip
          {...chartTooltipProps}
          formatter={(_value: number | string, _name, item) => {
            const step = item?.payload as Step | undefined;
            if (!step) return ['—', ''];
            const text =
              step.kind === 'total'
                ? fmtCurrency(step.signed)
                : fmtCurrencySigned(step.signed);
            return [
              text,
              step.kind === 'benefit'
                ? 'Diversification benefit'
                : step.kind === 'total'
                ? 'Diversified portfolio VaR'
                : `${step.label} standalone VaR`,
            ];
          }}
        />
        {/* Invisible positioning bar, then the visible span. */}
        <Bar
          dataKey="base"
          stackId="wf"
          fill="transparent"
          isAnimationActive={false}
          tooltipType="none"
        />
        <Bar dataKey="span" stackId="wf" radius={[2, 2, 0, 0]}>
          {steps.map((step) => (
            <Cell key={step.label} fill={fill(step)} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
