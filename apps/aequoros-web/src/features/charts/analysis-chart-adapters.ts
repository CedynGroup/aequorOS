import type {
  CalculationRunRead,
  CapitalComparisonRead,
  ForecastPeriodRead,
  LiquiditySummaryRead,
} from "@aequoros/risk-service-api";

export type ChartAvailability = "ready" | "empty" | "unavailable";

export interface UnavailableSpan {
  periodNumber: number;
  reason: string;
}

export interface DecimalChartValue {
  decimal: string;
  pixel: number;
}

export interface ForecastTrajectoryPoint {
  periodNumber: number;
  periodEnd: Date | null;
  currency: string | null;
  assets: DecimalChartValue | null;
  liabilities: DecimalChartValue | null;
  equity: DecimalChartValue | null;
}

export interface ForecastTrajectorySeries {
  availability: ChartAvailability;
  reason: string | null;
  points: ForecastTrajectoryPoint[];
  unavailableSpans: UnavailableSpan[];
}

export function forecastRunToSeries(
  run: CalculationRunRead,
): ForecastTrajectorySeries {
  if (!run.outputs.length) {
    return {
      availability: "empty",
      reason: "This run has no forecast periods to chart.",
      points: [],
      unavailableSpans: [],
    };
  }

  const rows = new Map(run.outputs.map((row) => [row.periodNumber, row]));
  const horizon = Math.max(
    run.forecastPeriods,
    ...run.outputs.map((row) => row.periodNumber),
  );
  const unavailableSpans: UnavailableSpan[] = [];
  const points = Array.from({ length: horizon }, (_, index) => {
    const periodNumber = index + 1;
    const row = rows.get(periodNumber);
    if (!row) {
      unavailableSpans.push({
        periodNumber,
        reason: `Forecast period ${periodNumber} is missing from the persisted run.`,
      });
      return missingForecastPoint(periodNumber);
    }

    const assets = decimalChartValue(row.totalAssets);
    const liabilities = decimalChartValue(row.totalLiabilities);
    const equity = decimalChartValue(row.totalEquity);
    if (!assets || !liabilities || !equity) {
      unavailableSpans.push({
        periodNumber,
        reason: `Forecast period ${periodNumber} contains a value that cannot be plotted.`,
      });
    }
    return {
      periodNumber,
      periodEnd: row.periodEnd,
      currency: row.currency,
      assets,
      liabilities,
      equity,
    };
  });
  const hasValue = points.some(
    (point) => point.assets || point.liabilities || point.equity,
  );

  return {
    availability: hasValue ? "ready" : "unavailable",
    reason: hasValue
      ? null
      : "The persisted forecast values are not meaningful for charting.",
    points,
    unavailableSpans,
  };
}

export interface LiquidityCoveragePoint {
  periodNumber: number;
  periodEnd: Date | null;
  coverage: DecimalChartValue | null;
}

export interface LiquidityCoverageSeries {
  availability: ChartAvailability;
  reason: string | null;
  points: LiquidityCoveragePoint[];
  unavailableSpans: UnavailableSpan[];
  threshold: DecimalChartValue;
}

const COVERAGE_THRESHOLD = "1.20";

export function liquidityCoverageToSeries(
  run: CalculationRunRead,
  summary: LiquiditySummaryRead,
): LiquidityCoverageSeries {
  const threshold = decimalChartValue(COVERAGE_THRESHOLD)!;
  if (!run.outputs.length) {
    return {
      availability: "empty",
      reason: "This run has no forecast periods to chart.",
      points: [],
      unavailableSpans: [],
      threshold,
    };
  }

  const metric = summary.metrics.find(
    (candidate) => candidate.key === "minimum_sources_coverage",
  );
  const rows = new Map(run.outputs.map((row) => [row.periodNumber, row]));
  const horizon = Math.max(
    run.forecastPeriods,
    ...run.outputs.map((row) => row.periodNumber),
  );
  const unavailableSpans: UnavailableSpan[] = [];
  const points = Array.from({ length: horizon }, (_, index) => {
    const periodNumber = index + 1;
    const row = rows.get(periodNumber);
    if (!row) {
      const reason = `Forecast period ${periodNumber} is missing from the persisted run.`;
      unavailableSpans.push({ periodNumber, reason });
      return { periodNumber, periodEnd: null, coverage: null };
    }

    const coverage = coverageForPeriod(row);
    if (!coverage) {
      const reason =
        metric?.diagnostic ??
        `Sources coverage is not meaningful for forecast period ${periodNumber}.`;
      unavailableSpans.push({ periodNumber, reason });
    }
    return { periodNumber, periodEnd: row.periodEnd, coverage };
  });
  const hasValue = points.some((point) => point.coverage);

  return {
    availability: hasValue ? "ready" : "unavailable",
    reason: hasValue
      ? null
      : (metric?.diagnostic ??
        "Sources coverage is not meaningful for the persisted forecast."),
    points,
    unavailableSpans,
    threshold,
  };
}

export interface CapitalComparisonPoint {
  periodNumber: number;
  baselineRatio: DecimalChartValue | null;
  downsideRatio: DecimalChartValue | null;
}

export interface CapitalComparisonSeries {
  availability: ChartAvailability;
  reason: string | null;
  points: CapitalComparisonPoint[];
  unavailableSpans: UnavailableSpan[];
}

export function capitalComparisonToSeries(
  comparison: CapitalComparisonRead | undefined,
): CapitalComparisonSeries {
  if (!comparison?.baseline || !comparison.downside) {
    return {
      availability: "empty",
      reason: "Generate successful baseline and downside projections to chart.",
      points: [],
      unavailableSpans: [],
    };
  }
  if (comparison.diagnostic) {
    return {
      availability: "unavailable",
      reason: comparison.diagnostic.message,
      points: [],
      unavailableSpans: [],
    };
  }
  if (!comparison.periods.length) {
    return {
      availability: "empty",
      reason: "The compatible comparison has no forecast periods to chart.",
      points: [],
      unavailableSpans: [],
    };
  }

  const rows = new Map(
    comparison.periods.map((period) => [period.periodNumber, period]),
  );
  const horizon = Math.max(
    ...comparison.periods.map((row) => row.periodNumber),
  );
  const unavailableSpans: UnavailableSpan[] = [];
  const points = Array.from({ length: horizon }, (_, index) => {
    const periodNumber = index + 1;
    const row = rows.get(periodNumber);
    if (!row) {
      unavailableSpans.push({
        periodNumber,
        reason: `Comparison period ${periodNumber} is missing.`,
      });
      return { periodNumber, baselineRatio: null, downsideRatio: null };
    }
    const baselineRatio = decimalChartValue(row.baselineEquityToAssetsRatio);
    const downsideRatio = decimalChartValue(row.downsideEquityToAssetsRatio);
    if (!baselineRatio || !downsideRatio) {
      unavailableSpans.push({
        periodNumber,
        reason: `Comparison period ${periodNumber} contains a ratio that cannot be plotted.`,
      });
    }
    return { periodNumber, baselineRatio, downsideRatio };
  });
  const hasValue = points.some(
    (point) => point.baselineRatio || point.downsideRatio,
  );

  return {
    availability: hasValue ? "ready" : "unavailable",
    reason: hasValue
      ? null
      : "The compatible comparison ratios are not meaningful for charting.",
    points,
    unavailableSpans,
  };
}

function missingForecastPoint(periodNumber: number): ForecastTrajectoryPoint {
  return {
    periodNumber,
    periodEnd: null,
    currency: null,
    assets: null,
    liabilities: null,
    equity: null,
  };
}

function decimalChartValue(decimal: string): DecimalChartValue | null {
  if (!parseDecimal(decimal)) return null;
  const pixel = Number(decimal);
  return Number.isFinite(pixel) ? { decimal, pixel } : null;
}

function coverageForPeriod(
  period: ForecastPeriodRead,
): DecimalChartValue | null {
  const inflows = parseDecimal(period.projectedInflows);
  const draw = parseDecimal(period.creditDraw);
  const outflows = parseDecimal(period.projectedOutflows);
  const repayment = parseDecimal(period.debtRepayment);
  if (!inflows || !draw || !outflows || !repayment) return null;

  const sources = addDecimals(inflows, draw);
  const uses = addDecimals(outflows, repayment);
  if (uses.unscaled <= 0n) return null;
  const decimal = divideDecimal(sources, uses, 4);
  return decimalChartValue(decimal);
}

interface ParsedDecimal {
  unscaled: bigint;
  scale: number;
}

function parseDecimal(value: string): ParsedDecimal | null {
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value);
  if (!match) return null;
  const fraction = match[3] ?? "";
  const magnitude = BigInt(`${match[2]}${fraction}`);
  return {
    unscaled: match[1] ? -magnitude : magnitude,
    scale: fraction.length,
  };
}

function addDecimals(left: ParsedDecimal, right: ParsedDecimal): ParsedDecimal {
  const scale = Math.max(left.scale, right.scale);
  return {
    unscaled:
      left.unscaled * 10n ** BigInt(scale - left.scale) +
      right.unscaled * 10n ** BigInt(scale - right.scale),
    scale,
  };
}

function divideDecimal(
  numerator: ParsedDecimal,
  denominator: ParsedDecimal,
  scale: number,
) {
  const negative = numerator.unscaled < 0n !== denominator.unscaled < 0n;
  const dividend =
    absolute(numerator.unscaled) * 10n ** BigInt(denominator.scale + scale);
  const divisor =
    absolute(denominator.unscaled) * 10n ** BigInt(numerator.scale);
  let quotient = dividend / divisor;
  const remainder = dividend % divisor;
  if (remainder * 2n >= divisor) quotient += 1n;
  const signed = negative ? -quotient : quotient;
  const sign = signed < 0n ? "-" : "";
  const digits = absolute(signed)
    .toString()
    .padStart(scale + 1, "0");
  return `${sign}${digits.slice(0, -scale)}.${digits.slice(-scale)}`;
}

function absolute(value: bigint) {
  return value < 0n ? -value : value;
}
