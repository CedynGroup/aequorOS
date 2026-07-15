import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatPct } from "./format";

export type TrendPoint = {
  label: string;
  value: string;
  stored: boolean;
};

export type TrendReferenceLine = {
  value: number;
  label: string;
  tone?: "danger" | "warning" | "neutral";
};

const referenceLineColor = {
  danger: "rgb(var(--danger))",
  warning: "rgb(var(--warning))",
  neutral: "rgb(var(--muted-foreground))",
} as const;

function StoredDot(props: {
  cx?: number;
  cy?: number;
  payload?: { stored?: boolean };
}) {
  const { cx, cy, payload } = props;
  if (cx === undefined || cy === undefined) return null;
  return (
    <circle
      cx={cx}
      cy={cy}
      r={3}
      stroke="rgb(var(--primary))"
      strokeWidth={1.5}
      fill={payload?.stored ? "rgb(var(--primary))" : "rgb(var(--surface))"}
    />
  );
}

export function TrendChart({
  points,
  referenceLines = [],
  seriesLabel,
  height = 180,
}: {
  points: TrendPoint[];
  referenceLines?: TrendReferenceLine[];
  seriesLabel: string;
  height?: number;
}) {
  const data = points.map((point) => ({
    label: point.label,
    value: Number(point.value),
    stored: point.stored,
    display: formatPct(point.value),
  }));
  const hasInlinePoints = points.some((point) => !point.stored);
  const plotted = [
    ...data.map((point) => point.value),
    ...referenceLines.map((line) => line.value),
  ].filter((value) => Number.isFinite(value));
  const domainMin = plotted.length ? Math.min(...plotted) : 0;
  const domainMax = plotted.length ? Math.max(...plotted) : 0;
  const padding = Math.max((domainMax - domainMin) * 0.08, 1);

  return (
    <div className="min-w-0">
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="rgb(var(--border))" strokeDasharray="2 4" vertical={false} />
          <XAxis
            dataKey="label"
            tick={{ fontSize: 10, fill: "rgb(var(--muted-foreground))" }}
            tickLine={false}
            axisLine={{ stroke: "rgb(var(--border))" }}
          />
          <YAxis
            width={44}
            tick={{ fontSize: 10, fill: "rgb(var(--muted-foreground))" }}
            tickLine={false}
            axisLine={false}
            domain={[domainMin - padding, domainMax + padding]}
            tickFormatter={(value: number) => value.toFixed(0)}
          />
          <Tooltip
            formatter={(value: number) => [`${value.toFixed(1)}%`, seriesLabel]}
            contentStyle={{
              fontSize: 11,
              borderRadius: 6,
              border: "1px solid rgb(var(--border))",
            }}
          />
          {referenceLines.map((line) => (
            <ReferenceLine
              key={`${line.label}-${line.value}`}
              y={line.value}
              stroke={referenceLineColor[line.tone ?? "neutral"]}
              strokeDasharray="4 4"
              label={{
                value: line.label,
                position: "insideTopRight",
                fontSize: 10,
                fill: referenceLineColor[line.tone ?? "neutral"],
              }}
            />
          ))}
          <Line
            type="monotone"
            dataKey="value"
            name={seriesLabel}
            stroke="rgb(var(--primary))"
            strokeWidth={1.5}
            dot={<StoredDot />}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
      {hasInlinePoints ? (
        <div className="mt-1 text-[11px] text-[rgb(var(--muted-foreground))]">
          Hollow dots: computed inline (not yet stored)
        </div>
      ) : null}
    </div>
  );
}
