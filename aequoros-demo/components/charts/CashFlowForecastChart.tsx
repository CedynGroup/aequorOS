'use client';

import {
  ResponsiveContainer,
  ComposedChart,
  Line,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  ReferenceLine,
} from 'recharts';

export type HistoryPoint = {
  /** Day offset relative to the as-of date (≤ 0). */
  day: number;
  netFlow: number;
};

export type ForecastPoint = {
  /** Day offset after the as-of date (≥ 1). */
  day: number;
  netFlow: number;
  lower: number;
  upper: number;
};

type ChartRow = {
  day: number;
  actual?: number;
  forecast?: number;
  lower?: number;
  upper?: number;
};

export default function CashFlowForecastChart({
  history,
  forecast,
  showBand = true,
  forecastLabel = 'LSTM forecast',
}: {
  history: HistoryPoint[];
  forecast: ForecastPoint[];
  showBand?: boolean;
  forecastLabel?: string;
}) {
  const rows: ChartRow[] = [
    ...history.map((p) => ({ day: p.day, actual: p.netFlow })),
    ...forecast.map((p) => ({
      day: p.day,
      forecast: p.netFlow,
      lower: p.lower,
      upper: p.upper,
    })),
  ];

  const horizon = forecast.length || 30;
  const span = rows.length || 1;

  return (
    <ResponsiveContainer width="100%" height={340}>
      <ComposedChart
        data={rows}
        margin={{ top: 16, right: 24, left: 0, bottom: 8 }}
      >
        <CartesianGrid stroke="#E4E8EC" strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="day"
          axisLine={{ stroke: '#D0D7DE' }}
          tickLine={false}
          tickFormatter={(v: number) => (v > 0 ? `D+${v}` : `D${v}`)}
          interval={Math.max(0, Math.floor(span / 12) - 1)}
        />
        <YAxis
          axisLine={false}
          tickLine={false}
          tickFormatter={(v) => `${v}M`}
          width={50}
        />
        <Tooltip
          labelFormatter={(v: number) => (v > 0 ? `Day +${v}` : `Day ${v}`)}
          formatter={(value: number, name: string) => [
            `GHS ${value.toFixed(2)}M`,
            name,
          ]}
        />
        <ReferenceLine y={0} stroke="#5A6776" strokeWidth={1} />
        <ReferenceLine
          x={0}
          stroke="#D0D7DE"
          strokeDasharray="4 3"
          label={{
            value: 'As of',
            position: 'insideTopLeft',
            fill: '#5A6776',
            fontSize: 11,
          }}
        />

        {/* Confidence band (paint upper fill, then mask below lower) */}
        {showBand && (
          <Area
            type="monotone"
            dataKey="upper"
            stroke="none"
            fill="#2D7FF9"
            fillOpacity={0.08}
            name="95% upper"
            legendType="none"
            tooltipType="none"
          />
        )}
        {showBand && (
          <Area
            type="monotone"
            dataKey="lower"
            stroke="none"
            fill="#FFFFFF"
            fillOpacity={1}
            name="95% lower"
            legendType="none"
            tooltipType="none"
          />
        )}

        <Line
          type="monotone"
          dataKey="actual"
          stroke="#5A6776"
          strokeWidth={1.5}
          dot={false}
          name="Actual net flow"
        />
        <Line
          type="monotone"
          dataKey="forecast"
          stroke="#2D7FF9"
          strokeWidth={2}
          dot={false}
          name={`${forecastLabel} (${horizon}d)`}
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
