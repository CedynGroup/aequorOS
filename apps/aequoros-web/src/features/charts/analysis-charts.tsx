import { useEffect, useMemo, useRef, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Alert } from "../../components/ui";
import { formatDecimal, formatMoney } from "../../lib/money";
import type {
  CapitalComparisonSeries,
  DecimalChartValue,
  ForecastTrajectorySeries,
  LiquidityCoverageSeries,
  UnavailableSpan,
} from "./analysis-chart-adapters";

const CHART_HEIGHT = 280;
const MIN_CHART_WIDTH = 520;
const LEGEND_STYLE = {
  fontFamily: "inherit",
  fontSize: 11,
  fontVariantNumeric: "tabular-nums",
} as const;

export function ForecastTrajectoryChart({
  series,
}: {
  series: ForecastTrajectorySeries;
}) {
  if (series.availability !== "ready") {
    return <ChartState title="Forecast trajectory" reason={series.reason} />;
  }
  const currency =
    series.points.find((point) => point.currency)?.currency ?? "USD";
  const data = series.points.map((point) => ({
    periodNumber: point.periodNumber,
    periodEnd: point.periodEnd,
    assets: point.assets?.pixel ?? null,
    assetsDecimal: point.assets?.decimal ?? null,
    liabilities: point.liabilities?.pixel ?? null,
    liabilitiesDecimal: point.liabilities?.decimal ?? null,
    equity: point.equity?.pixel ?? null,
    equityDecimal: point.equity?.decimal ?? null,
  }));
  const values = series.points.flatMap((point) => [
    point.assets,
    point.liabilities,
    point.equity,
  ]);

  return (
    <ChartFrame
      title="Balance-sheet trajectory"
      description="Projected assets, liabilities, and equity by forecast period."
      unavailableSpans={series.unavailableSpans}
    >
      {(width, animate) => (
        <LineChart
          width={width}
          height={CHART_HEIGHT}
          data={data}
          aria-label="Balance-sheet trajectory chart"
          title="Balance-sheet trajectory"
          desc="Projected assets, liabilities, and equity by forecast period."
          margin={{ top: 18, right: 20, bottom: 8, left: 8 }}
        >
          <CartesianGrid stroke="rgb(var(--border))" strokeDasharray="3 3" />
          <XAxis
            dataKey="periodNumber"
            tickFormatter={(value) => `P${String(value)}`}
            tick={{
              fontSize: 11,
              style: { fontVariantNumeric: "tabular-nums" },
            }}
          />
          <DecimalYAxis
            values={values}
            formatter={(value) => formatMoney(value, currency)}
          />
          <Tooltip content={<ForecastTooltip currency={currency} />} />
          <Legend verticalAlign="top" height={28} wrapperStyle={LEGEND_STYLE} />
          <Line
            type="linear"
            dataKey="assets"
            name="Assets"
            stroke="rgb(var(--primary))"
            strokeWidth={2}
            connectNulls={false}
            isAnimationActive={animate}
            dot={<ChartDot series="forecast-assets" />}
          />
          <Line
            type="linear"
            dataKey="liabilities"
            name="Liabilities"
            stroke="rgb(var(--warning))"
            strokeWidth={2}
            connectNulls={false}
            isAnimationActive={animate}
            dot={<ChartDot series="forecast-liabilities" />}
          />
          <Line
            type="linear"
            dataKey="equity"
            name="Equity"
            stroke="rgb(var(--success))"
            strokeWidth={2}
            connectNulls={false}
            isAnimationActive={animate}
            dot={<ChartDot series="forecast-equity" />}
          />
        </LineChart>
      )}
    </ChartFrame>
  );
}

export function LiquidityCoverageChart({
  series,
}: {
  series: LiquidityCoverageSeries;
}) {
  const threshold = series.threshold;
  if (series.availability !== "ready" || !threshold) {
    return <ChartState title="Liquidity coverage" reason={series.reason} />;
  }
  const data = series.points.map((point) => ({
    periodNumber: point.periodNumber,
    periodEnd: point.periodEnd,
    coverage: point.coverage?.pixel ?? null,
    coverageDecimal: point.coverage?.decimal ?? null,
  }));
  const values = [...series.points.map((point) => point.coverage), threshold];

  return (
    <ChartFrame
      title="Sources coverage by period"
      description={`Persisted forecast sources divided by uses; the line marks the ${series.thresholdRuleVersion} classification threshold.`}
      unavailableSpans={series.unavailableSpans}
    >
      {(width, animate) => (
        <LineChart
          width={width}
          height={CHART_HEIGHT}
          data={data}
          aria-label="Liquidity sources coverage chart"
          title="Liquidity sources coverage"
          desc="Sources coverage by forecast period with the classification threshold."
          margin={{ top: 18, right: 20, bottom: 8, left: 8 }}
        >
          <CartesianGrid stroke="rgb(var(--border))" strokeDasharray="3 3" />
          <XAxis
            dataKey="periodNumber"
            tickFormatter={(value) => `P${String(value)}`}
            tick={{
              fontSize: 11,
              style: { fontVariantNumeric: "tabular-nums" },
            }}
          />
          <DecimalYAxis
            values={values}
            formatter={(value) => `${formatDecimal(value, 2)}x`}
          />
          <Tooltip content={<CoverageTooltip />} />
          <ReferenceLine
            y={threshold.pixel}
            stroke="rgb(var(--warning))"
            strokeDasharray="5 4"
            strokeWidth={2}
            className="liquidity-threshold-line"
            label={{
              value: `${formatDecimal(threshold.decimal, 2)}x threshold`,
              position: "insideTopRight",
              fill: "rgb(var(--warning))",
              fontSize: 11,
            }}
          />
          <Line
            type="linear"
            dataKey="coverage"
            name="Sources coverage"
            stroke="rgb(var(--primary))"
            strokeWidth={2}
            connectNulls={false}
            isAnimationActive={animate}
            dot={<ChartDot series="liquidity-coverage" />}
          />
        </LineChart>
      )}
    </ChartFrame>
  );
}

export function CapitalComparisonChart({
  series,
}: {
  series: CapitalComparisonSeries;
}) {
  if (series.availability !== "ready") {
    return <ChartState title="Capital comparison" reason={series.reason} />;
  }
  const data = series.points.map((point) => ({
    periodNumber: point.periodNumber,
    baseline: point.baselineRatio?.pixel ?? null,
    baselineDecimal: point.baselineRatio?.decimal ?? null,
    downside: point.downsideRatio?.pixel ?? null,
    downsideDecimal: point.downsideRatio?.decimal ?? null,
  }));
  const values = series.points.flatMap((point) => [
    point.baselineRatio,
    point.downsideRatio,
  ]);

  return (
    <ChartFrame
      title="Equity-to-assets comparison"
      description="Baseline and downside ratios for API-enforced, basis-compatible projections."
      unavailableSpans={series.unavailableSpans}
    >
      {(width, animate) => (
        <LineChart
          width={width}
          height={CHART_HEIGHT}
          data={data}
          aria-label="Baseline versus downside capital chart"
          title="Baseline versus downside capital"
          desc="Equity-to-assets ratio for basis-compatible baseline and downside projections."
          margin={{ top: 18, right: 20, bottom: 8, left: 8 }}
        >
          <CartesianGrid stroke="rgb(var(--border))" strokeDasharray="3 3" />
          <XAxis
            dataKey="periodNumber"
            tickFormatter={(value) => `P${String(value)}`}
            tick={{
              fontSize: 11,
              style: { fontVariantNumeric: "tabular-nums" },
            }}
          />
          <DecimalYAxis values={values} formatter={formatPercent} />
          <Tooltip content={<CapitalTooltip />} />
          <Line
            type="linear"
            dataKey="baseline"
            name="Baseline"
            stroke="rgb(var(--success))"
            strokeWidth={2}
            connectNulls={false}
            isAnimationActive={animate}
            dot={<ChartDot series="capital-baseline" />}
          />
          <Line
            type="linear"
            dataKey="downside"
            name="Downside"
            stroke="rgb(var(--danger))"
            strokeWidth={2}
            connectNulls={false}
            isAnimationActive={animate}
            dot={<ChartDot series="capital-downside" />}
          />
        </LineChart>
      )}
    </ChartFrame>
  );
}

function ChartFrame({
  title,
  description,
  unavailableSpans,
  children,
}: {
  title: string;
  description: string;
  unavailableSpans: UnavailableSpan[];
  children: (width: number, animate: boolean) => React.ReactNode;
}) {
  const { ref, width } = useChartWidth();
  const animate = useChartAnimation();
  return (
    <figure aria-label={title} className="min-w-0 space-y-2 py-2">
      <figcaption>
        <div className="text-xs font-semibold">{title}</div>
        <div className="text-[11px] text-[rgb(var(--muted-foreground))]">
          {description}
        </div>
      </figcaption>
      <div ref={ref} className="min-w-0 overflow-x-auto font-mono tabular-nums">
        {children(width, animate)}
      </div>
      <GapAnnotations spans={unavailableSpans} />
    </figure>
  );
}

function ChartState({
  title,
  reason,
}: {
  title: string;
  reason: string | null;
}) {
  return (
    <div data-chart-state="unavailable" className="py-2">
      <Alert title={`${title} unavailable`} tone="warning">
        {reason ?? "There are no meaningful values to chart."} The tabular
        values remain authoritative.
      </Alert>
    </div>
  );
}

function GapAnnotations({ spans }: { spans: UnavailableSpan[] }) {
  if (!spans.length) return null;
  return (
    <div className="rounded-md border border-amber-200 bg-amber-50 p-2 text-[11px] text-amber-900">
      <div className="font-medium">Chart gaps</div>
      <ul className="mt-1 list-disc pl-4">
        {spans.map((span) => (
          <li key={`${span.periodNumber}-${span.reason}`}>
            Period {span.periodNumber}: {span.reason}
          </li>
        ))}
      </ul>
    </div>
  );
}

function DecimalYAxis({
  values,
  formatter,
}: {
  values: Array<DecimalChartValue | null>;
  formatter: (decimal: string) => string;
}) {
  const originals = useMemo(() => {
    const map = new Map<number, string>();
    for (const value of values) {
      if (value) map.set(value.pixel, value.decimal);
    }
    return map;
  }, [values]);
  const ticks = [...originals.keys()].sort((left, right) => left - right);
  return (
    <YAxis
      width={92}
      ticks={ticks}
      tickFormatter={(value) =>
        formatter(originals.get(Number(value)) ?? String(value))
      }
      tick={{ fontSize: 10, style: { fontVariantNumeric: "tabular-nums" } }}
    />
  );
}

function ChartDot({
  cx,
  cy,
  stroke,
  value,
  series,
}: {
  cx?: number;
  cy?: number;
  stroke?: string;
  value?: number | null;
  series: string;
}) {
  if (cx === undefined || cy === undefined || value == null) return <></>;
  return (
    <circle
      cx={cx}
      cy={cy}
      r={3}
      fill={stroke ?? "rgb(var(--primary))"}
      stroke="rgb(var(--surface))"
      strokeWidth={1}
      data-chart-point={series}
    />
  );
}

interface TooltipDatum {
  periodNumber: number;
  periodEnd?: Date | null;
  assetsDecimal?: string | null;
  liabilitiesDecimal?: string | null;
  equityDecimal?: string | null;
  coverageDecimal?: string | null;
  baselineDecimal?: string | null;
  downsideDecimal?: string | null;
}

interface ChartTooltipProps {
  active?: boolean;
  payload?: Array<{ payload: TooltipDatum }>;
}

function ForecastTooltip({
  active,
  payload,
  currency,
}: ChartTooltipProps & { currency: string }) {
  const point = active ? payload?.[0]?.payload : undefined;
  if (!point) return null;
  return (
    <TooltipCard heading={tooltipHeading(point)}>
      <TooltipRow label="Assets" value={money(point.assetsDecimal, currency)} />
      <TooltipRow
        label="Liabilities"
        value={money(point.liabilitiesDecimal, currency)}
      />
      <TooltipRow label="Equity" value={money(point.equityDecimal, currency)} />
    </TooltipCard>
  );
}

function CoverageTooltip({ active, payload }: ChartTooltipProps) {
  const point = active ? payload?.[0]?.payload : undefined;
  if (!point) return null;
  return (
    <TooltipCard heading={tooltipHeading(point)}>
      <TooltipRow
        label="Coverage"
        value={
          point.coverageDecimal
            ? `${formatDecimal(point.coverageDecimal, 2)}x`
            : "Not meaningful"
        }
      />
    </TooltipCard>
  );
}

function CapitalTooltip({ active, payload }: ChartTooltipProps) {
  const point = active ? payload?.[0]?.payload : undefined;
  if (!point) return null;
  return (
    <TooltipCard heading={`Period ${point.periodNumber}`}>
      <TooltipRow label="Baseline" value={rate(point.baselineDecimal)} />
      <TooltipRow label="Downside" value={rate(point.downsideDecimal)} />
    </TooltipCard>
  );
}

function TooltipCard({
  heading,
  children,
}: {
  heading: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--surface))] p-2 text-xs shadow-sm">
      <div className="mb-1 font-medium">{heading}</div>
      <dl className="grid grid-cols-[auto_auto] gap-x-3 gap-y-1 tabular-nums">
        {children}
      </dl>
    </div>
  );
}

function TooltipRow({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="text-[rgb(var(--muted-foreground))]">{label}</dt>
      <dd className="text-right font-mono">{value}</dd>
    </>
  );
}

function tooltipHeading(point: TooltipDatum) {
  return point.periodEnd
    ? `Period ${point.periodNumber} · ${formatChartDate(point.periodEnd)}`
    : `Period ${point.periodNumber}`;
}

export function formatChartDate(value: Date) {
  return value.toLocaleDateString(undefined, { timeZone: "UTC" });
}

function money(value: string | null | undefined, currency: string) {
  return value ? formatMoney(value, currency) : "Not meaningful";
}

function rate(value: string | null | undefined) {
  return value ? formatPercent(value) : "Not meaningful";
}

function formatPercent(value: string) {
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value);
  if (!match) return value;
  const fraction = match[3] ?? "";
  const shifted = `${match[1]}${match[2]}${fraction.slice(0, 2).padEnd(2, "0")}.${fraction.slice(2) || "0"}`;
  return `${formatDecimal(shifted, 1)}%`;
}

function useChartWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(720);
  useEffect(() => {
    const element = ref.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const update = () =>
      setWidth(Math.max(MIN_CHART_WIDTH, Math.floor(element.clientWidth)));
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return { ref, width };
}

function useChartAnimation() {
  const [animate, setAnimate] = useState(false);
  useEffect(() => {
    if (import.meta.env.MODE === "test") return;
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    setAnimate(!query.matches);
    const update = () => setAnimate(!query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return animate;
}
