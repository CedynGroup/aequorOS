'use client';

import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

export type IncentivePoint = { incentiveBps: number; cpr: number };

/** Partial-dependence curve: modelled annual CPR vs rate incentive (note − refi). */
export default function PrepaymentCurveChart({
  curve,
  height = 260,
}: {
  curve: IncentivePoint[];
  height?: number;
}) {
  const data = curve.map((p) => ({ ...p, cprPct: p.cpr * 100 }));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-light, #eef1f4)" />
        <XAxis
          dataKey="incentiveBps"
          tick={{ fontSize: 11 }}
          tickFormatter={(v) => `${v > 0 ? '+' : ''}${v}`}
          label={{ value: 'Rate incentive (bps)', position: 'insideBottom', offset: -2, fontSize: 11 }}
        />
        <YAxis
          tick={{ fontSize: 11 }}
          tickFormatter={(v) => `${v.toFixed(0)}%`}
          width={44}
        />
        <Tooltip
          formatter={(v: number) => [`${v.toFixed(1)}%`, 'Annual CPR']}
          labelFormatter={(v) => `Incentive ${v > 0 ? '+' : ''}${v} bps`}
        />
        <ReferenceLine x={0} stroke="#94a3b8" strokeDasharray="4 4" />
        <Line
          type="monotone"
          dataKey="cprPct"
          stroke="var(--color-action, #2563eb)"
          strokeWidth={2}
          dot={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
