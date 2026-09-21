"use client";

/**
 * How an IRRBB Standardised Framework figure is PRINTED.
 *
 * Every figure on that screen arrives as text (a decimal the server did not
 * round) or as `null`. These helpers are the only place that decides what a
 * reader sees, and they share one rule:
 *
 *   **AN ABSENT FIGURE IS PRINTED AS ABSENT, NEVER AS A NUMBER.**
 *
 * There is no `?? 0` anywhere below, and there must never be one. Zero is a
 * real and excellent answer for an economic-value loss, so a missing loss
 * rendered as zero is indistinguishable on screen from a book with no interest
 * rate risk at all. The same goes for a ratio, a share, a cap and a maturity.
 *
 * Currency: the reporting figures are in the institution's own reporting
 * currency, so `fmtCurrency` is called with no currency argument and takes the
 * active jurisdiction's. A per-currency native figure is genuinely NOT the
 * institution's own currency, and only those calls pass one.
 */

import { fmtCurrency, fmtNum, fmtPct } from "@/lib/format";
import { NOT_REPORTED } from "./labels";
import { PCT_DECIMALS, YEARS_DECIMALS } from "./display";

function parsed(value: string | null): number | null {
  if (value === null) return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/** An amount in the institution's reporting currency, or the absence. */
export function money(value: string | null): string {
  const n = parsed(value);
  return n === null ? NOT_REPORTED : fmtCurrency(n, undefined, { compact: false });
}

/** An amount in a named currency that is not the reporting one. */
export function moneyIn(value: string | null, currency: string): string {
  const n = parsed(value);
  return n === null
    ? NOT_REPORTED
    : fmtCurrency(n, currency, { compact: false });
}

/** A percentage, or the absence. */
export function pct(value: string | null): string {
  const n = parsed(value);
  return n === null ? NOT_REPORTED : fmtPct(n, PCT_DECIMALS);
}

/** A duration in years, or the absence. */
export function years(value: string | null): string {
  const n = parsed(value);
  return n === null ? NOT_REPORTED : fmtNum(n, YEARS_DECIMALS);
}

/** A conversion rate, or the absence. */
export function rate(value: string | null): string {
  const n = parsed(value);
  return n === null ? NOT_REPORTED : fmtNum(n, PCT_DECIMALS);
}

/** A whole count, or the absence. */
export function count(value: number | null): string {
  return value === null ? NOT_REPORTED : fmtNum(value);
}

/** An ISO moment as a plain date, or the absence. */
export function day(value: string | null): string {
  if (value === null) return NOT_REPORTED;
  const at = new Date(value);
  return Number.isFinite(at.getTime())
    ? at.toISOString().slice(0, "0000-00-00".length)
    : NOT_REPORTED;
}
