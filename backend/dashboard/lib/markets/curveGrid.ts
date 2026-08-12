/**
 * Forward-grid derivations for the Curves experience (FC-5).
 *
 * The bank-facing views payload carries a published curve as `(tenor_months,
 * rate)` points only — rates are decimal fractions on the desk's native basis,
 * which the payload does NOT name. This module derives, transparently and
 * honestly, the professional grid columns a bank needs (period Start/End dates,
 * discount factor, and the yield re-expressed on a chosen day-count) WITHOUT
 * fabricating anything the desk actually published:
 *
 * - Start/End dates are DERIVED from the as-of anchor by a simple monthly roll.
 *   They are not the desk's exact schedule dates (which are not in the payload).
 * - Discount factors use the simple money-market form DF = 1 / (1 + rate·τ) on a
 *   FIXED anchor day-count (MM Act/360), so the DF is invariant across the
 *   "Convert to" basis switch.
 * - The displayed yield is re-expressed on the chosen day-count while holding
 *   the DF invariant: y_B = (1/DF − 1) / τ_B. On the Act/360 anchor this
 *   round-trips exactly to the published rate.
 *
 * All functions are pure and UTC-safe (date-only arithmetic), so the same grid
 * reproduces identically regardless of the viewer's timezone.
 */

export type DayCountBasis = 'act360' | 'act365' | 'actact' | 'thirty360';

/** The fixed anchor the invariant discount factor is derived on. */
export const ANCHOR_BASIS: DayCountBasis = 'act360';

export const BASIS_LABELS: Record<DayCountBasis, string> = {
  act360: 'MM Act/360',
  act365: 'Act/365',
  actact: 'Act/Act',
  thirty360: '30/360',
};

export const BASIS_ORDER: DayCountBasis[] = ['act360', 'act365', 'actact', 'thirty360'];

const MS_PER_DAY = 86_400_000;

/** Midnight-UTC epoch ms for a date's calendar day (drops any time/TZ drift). */
function utcDayMs(d: Date): number {
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
}

function isLeap(year: number): boolean {
  return (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0;
}

function daysInYear(year: number): number {
  return isLeap(year) ? 366 : 365;
}

/** Add whole months to a UTC date, clamping the day to the target month end. */
export function rollMonthsUTC(anchor: Date, months: number): Date {
  const day = anchor.getUTCDate();
  const target = new Date(Date.UTC(anchor.getUTCFullYear(), anchor.getUTCMonth() + months, 1));
  const lastDayOfMonth = new Date(
    Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0)
  ).getUTCDate();
  target.setUTCDate(Math.min(day, lastDayOfMonth));
  return target;
}

/** Actual calendar days between two dates (UTC, date-only). */
export function actualDays(start: Date, end: Date): number {
  return Math.round((utcDayMs(end) - utcDayMs(start)) / MS_PER_DAY);
}

/** ACT/ACT (ISDA): sum each calendar year's day-count over that year's length. */
function actActIsdaFraction(start: Date, end: Date): number {
  const startYear = start.getUTCFullYear();
  const endYear = end.getUTCFullYear();
  if (startYear === endYear) {
    return actualDays(start, end) / daysInYear(startYear);
  }
  let fraction = 0;
  for (let year = startYear; year <= endYear; year += 1) {
    const segmentStart = year === startYear ? start : new Date(Date.UTC(year, 0, 1));
    const segmentEnd = year === endYear ? end : new Date(Date.UTC(year + 1, 0, 1));
    fraction += actualDays(segmentStart, segmentEnd) / daysInYear(year);
  }
  return fraction;
}

function thirty360Fraction(start: Date, end: Date): number {
  const startDay = Math.min(start.getUTCDate(), 30);
  const endDay = end.getUTCDate() === 31 && startDay === 30 ? 30 : end.getUTCDate();
  return (
    (360 * (end.getUTCFullYear() - start.getUTCFullYear()) +
      30 * (end.getUTCMonth() - start.getUTCMonth()) +
      endDay -
      startDay) /
    360
  );
}

/** Year fraction τ for a day-count basis. */
export function yearFraction(start: Date, end: Date, basis: DayCountBasis): number {
  const days = actualDays(start, end);
  switch (basis) {
    case 'act360':
      return days / 360;
    case 'act365':
      return days / 365;
    case 'actact':
      return actActIsdaFraction(start, end);
    case 'thirty360':
      return thirty360Fraction(start, end);
  }
}

/** Simple money-market discount factor DF = 1 / (1 + rate·τ). */
export function discountFactor(rate: number, tau: number): number {
  const denom = 1 + rate * tau;
  return denom > 0 ? 1 / denom : 0;
}

/** Yield (decimal fraction) that reproduces `df` on year-fraction `tau`. */
export function yieldFromDf(df: number, tau: number): number {
  if (tau <= 0 || df <= 0) return 0;
  return (1 / df - 1) / tau;
}

export type ForwardGridRow = {
  tenorMonths: number;
  start: Date;
  end: Date;
  days: number;
  /** Published rate as a decimal fraction (e.g. 0.185). */
  baseRate: number;
  /** Invariant discount factor derived on the anchor basis. */
  df: number;
  /** Displayed base yield on the selected basis, as a percentage. */
  displayYieldPct: number;
  /** Present only when the bank has an active adjusted (overlay) series. */
  adjustedRate?: number;
  adjustedDf?: number;
  adjustedYieldPct?: number;
};

export type GridPoint = { tenorMonths: number; rate: number };

/**
 * Build the tenor-adjusted grid for a curve.
 *
 * `anchorDate` is the derivation anchor for Start/End dates (the curve's
 * published as-of date). `adjustedByTenor` maps tenor→adjusted decimal rate for
 * the bank's composed series, and is empty when there are no active overlays.
 */
export function buildForwardGrid(
  points: GridPoint[],
  anchorDate: Date,
  basis: DayCountBasis,
  adjustedByTenor?: Map<number, number>
): ForwardGridRow[] {
  return [...points]
    .sort((a, b) => a.tenorMonths - b.tenorMonths)
    .map((point) => {
      const start = anchorDate;
      const end = rollMonthsUTC(anchorDate, point.tenorMonths);
      const days = actualDays(start, end);
      const tauAnchor = yearFraction(start, end, ANCHOR_BASIS);
      const tauDisplay = yearFraction(start, end, basis);
      const df = discountFactor(point.rate, tauAnchor);
      const row: ForwardGridRow = {
        tenorMonths: point.tenorMonths,
        start,
        end,
        days,
        baseRate: point.rate,
        df,
        displayYieldPct: yieldFromDf(df, tauDisplay) * 100,
      };
      const adjustedRate = adjustedByTenor?.get(point.tenorMonths);
      if (adjustedRate !== undefined) {
        const adjustedDf = discountFactor(adjustedRate, tauAnchor);
        row.adjustedRate = adjustedRate;
        row.adjustedDf = adjustedDf;
        row.adjustedYieldPct = yieldFromDf(adjustedDf, tauDisplay) * 100;
      }
      return row;
    });
}
