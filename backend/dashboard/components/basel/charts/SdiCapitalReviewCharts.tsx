'use client';

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  CHART_ACCENT,
  CHART_CRIT,
  CHART_GRID,
  CHART_OK,
  axisProps,
  chartLegendProps,
  chartTooltipProps,
  seriesColor,
} from '@/lib/chartTheme';
import { fmtCurrency } from '@/lib/format';

export type SdiCapitalControl = {
  label: string;
  actual: number | null;
  required: number | null;
};

export type SdiRiskWeightBand = {
  label: string;
  exposure: number;
  rwa: number;
  weightPct: number;
};

export function SdiCarThresholdChart({
  carPct,
  carMinPct,
  height = 180,
}: {
  carPct: number | null;
  carMinPct: number;
  height?: number;
}) {
  if (carPct === null) return null;
  const ceiling = Math.max(carPct, carMinPct) * 1.2 || 10;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={[{ label: 'Capital adequacy ratio', actual: carPct }]} layout="vertical" margin={{ top: 16, right: 28, bottom: 8, left: 8 }}>
        <CartesianGrid horizontal={false} stroke={CHART_GRID} strokeDasharray="3 3" />
        <XAxis type="number" domain={[0, ceiling]} {...axisProps} tickFormatter={(value: number) => `${value.toFixed(0)}%`} />
        <YAxis type="category" dataKey="label" hide />
        <Tooltip {...chartTooltipProps} formatter={(value: number) => [`${value.toFixed(2)}%`, 'CAR']} />
        <ReferenceLine x={carMinPct} stroke={CHART_CRIT} strokeDasharray="4 3" label={{ value: `Minimum ${carMinPct.toFixed(1)}%`, position: 'top', fill: CHART_CRIT, fontSize: 11 }} />
        <Bar dataKey="actual" fill={carPct >= carMinPct ? CHART_OK : CHART_CRIT} maxBarSize={30} radius={[0, 3, 3, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function SdiRwaCompositionChart({ data, height = 230 }: { data: SdiRiskWeightBand[]; height?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, bottom: 4, left: 8 }}>
        <CartesianGrid horizontal={false} stroke={CHART_GRID} strokeDasharray="3 3" />
        <XAxis type="number" {...axisProps} tickFormatter={(value: number) => fmtCurrency(value, undefined, { decimals: 0 })} />
        <YAxis type="category" dataKey="label" axisLine={false} tickLine={false} tick={axisProps.tick} width={128} />
        <Tooltip
          {...chartTooltipProps}
          formatter={(value: number, name: string, item) => {
            const weight = (item?.payload as SdiRiskWeightBand | undefined)?.weightPct;
            const label = weight === undefined ? fmtCurrency(value) : `${fmtCurrency(value)} @ ${weight.toFixed(0)}% RW`;
            return [name === 'RWA' ? label : fmtCurrency(value), name];
          }}
        />
        <Legend {...chartLegendProps} />
        <Bar dataKey="exposure" name="Exposure" fill={seriesColor(0)} maxBarSize={16} radius={[0, 2, 2, 0]} />
        <Bar dataKey="rwa" name="RWA" fill={seriesColor(2)} maxBarSize={16} radius={[0, 2, 2, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function SdiCapitalControlsChart({ data, height = 220 }: { data: SdiCapitalControl[]; height?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 8, bottom: 4, left: 8 }}>
        <CartesianGrid vertical={false} stroke={CHART_GRID} strokeDasharray="3 3" />
        <XAxis dataKey="label" {...axisProps} interval={0} />
        <YAxis {...axisProps} tickFormatter={(value: number) => fmtCurrency(value, undefined, { decimals: 0 })} width={72} />
        <Tooltip {...chartTooltipProps} formatter={(value: number, name: string) => [fmtCurrency(value), name]} />
        <Legend {...chartLegendProps} />
        <Bar dataKey="actual" name="Actual" fill={CHART_ACCENT} maxBarSize={36} radius={[3, 3, 0, 0]} />
        <Bar dataKey="required" name="Required" fill={seriesColor(3)} maxBarSize={36} radius={[3, 3, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export type SdiCapitalTrendPoint = {
  asOf: string;
  carPct: number | null;
  nplPct: number;
};

export function SdiCapitalTrendChart({
  data,
  carMinPct,
  height = 240,
}: {
  data: SdiCapitalTrendPoint[];
  carMinPct: number;
  height?: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 12, right: 16, bottom: 4, left: 8 }}>
        <CartesianGrid vertical={false} stroke={CHART_GRID} strokeDasharray="3 3" />
        <XAxis dataKey="asOf" {...axisProps} minTickGap={28} />
        <YAxis {...axisProps} tickFormatter={(value: number) => `${value.toFixed(0)}%`} width={42} />
        <Tooltip {...chartTooltipProps} formatter={(value: number, name: string) => [`${value.toFixed(2)}%`, name]} />
        <Legend {...chartLegendProps} />
        <ReferenceLine y={carMinPct} stroke={CHART_CRIT} strokeDasharray="4 3" label={{ value: 'CAR floor', position: 'right', fill: CHART_CRIT, fontSize: 11 }} />
        <Line type="monotone" dataKey="carPct" name="CAR" stroke={CHART_OK} strokeWidth={2} dot={false} connectNulls />
        <Line type="monotone" dataKey="nplPct" name="NPL ratio" stroke={seriesColor(3)} strokeWidth={2} dot={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export type SdiLoanQualityBand = {
  grade: string;
  exposure: number;
  provision: number;
};

export function SdiLoanQualityChart({ data, height = 250 }: { data: SdiLoanQualityBand[]; height?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 8, bottom: 4, left: 8 }}>
        <CartesianGrid vertical={false} stroke={CHART_GRID} strokeDasharray="3 3" />
        <XAxis dataKey="grade" {...axisProps} interval={0} />
        <YAxis {...axisProps} tickFormatter={(value: number) => fmtCurrency(value, undefined, { decimals: 0 })} width={72} />
        <Tooltip {...chartTooltipProps} formatter={(value: number, name: string) => [fmtCurrency(value), name]} />
        <Legend {...chartLegendProps} />
        <Bar dataKey="exposure" name="Exposure" fill={seriesColor(0)} maxBarSize={34} radius={[3, 3, 0, 0]} />
        <Bar dataKey="provision" name="Required provision" fill={seriesColor(3)} maxBarSize={34} radius={[3, 3, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export type SdiDelinquencyBand = {
  label: string;
  exposure: number;
  count: number;
  severity: 'current' | 'early' | 'npl' | 'loss';
};

export function SdiDelinquencyChart({ data, height = 250 }: { data: SdiDelinquencyBand[]; height?: number }) {
  const color = (severity: SdiDelinquencyBand['severity']) => {
    if (severity === 'loss') return CHART_CRIT;
    if (severity === 'npl') return seriesColor(3);
    if (severity === 'early') return 'rgb(var(--warn))';
    return seriesColor(0);
  };
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 8, bottom: 4, left: 8 }}>
        <CartesianGrid vertical={false} stroke={CHART_GRID} strokeDasharray="3 3" />
        <XAxis dataKey="label" {...axisProps} interval={0} />
        <YAxis {...axisProps} tickFormatter={(value: number) => fmtCurrency(value, undefined, { decimals: 0 })} width={72} />
        <Tooltip
          {...chartTooltipProps}
          formatter={(value: number, _name, item) => {
            const count = (item?.payload as SdiDelinquencyBand | undefined)?.count;
            return [count === undefined ? fmtCurrency(value) : `${fmtCurrency(value)} · ${count} loan(s)`, 'Exposure'];
          }}
        />
        <Bar dataKey="exposure" name="Exposure" maxBarSize={36} radius={[3, 3, 0, 0]}>
          {data.map((band) => <Cell key={band.label} fill={color(band.severity)} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

export type SdiExposureConcentration = {
  name: string;
  pctNetOwnFunds: number;
  limitPct: number;
  aboveLimit: boolean;
};

export function SdiExposureConcentrationChart({ data, height }: { data: SdiExposureConcentration[]; height?: number }) {
  const chartHeight = height ?? Math.max(190, data.length * 36 + 44);
  return (
    <ResponsiveContainer width="100%" height={chartHeight}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 18, bottom: 4, left: 8 }}>
        <CartesianGrid horizontal={false} stroke={CHART_GRID} strokeDasharray="3 3" />
        <XAxis type="number" {...axisProps} tickFormatter={(value: number) => `${value.toFixed(0)}%`} />
        <YAxis type="category" dataKey="name" axisLine={false} tickLine={false} tick={axisProps.tick} width={132} />
        <Tooltip {...chartTooltipProps} formatter={(value: number) => [`${value.toFixed(2)}%`, '% Net Own Funds']} />
        <Legend {...chartLegendProps} />
        <Bar dataKey="pctNetOwnFunds" name="Exposure" maxBarSize={20} radius={[0, 2, 2, 0]}>
          {data.map((row) => <Cell key={row.name} fill={row.aboveLimit ? CHART_CRIT : seriesColor(0)} />)}
        </Bar>
        <Bar dataKey="limitPct" name="Applicable limit" fill={CHART_ACCENT} maxBarSize={20} radius={[0, 2, 2, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}