/**
 * Numerical formatters for banking displays.
 * Per design brief: thousands separators, consistent decimals, currency prefixes.
 */

const numberFmt = new Intl.NumberFormat('en-US');

/** "1,234,567" */
export function fmtNum(value: number, fractionDigits = 0): string {
  return value.toLocaleString('en-US', {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  });
}

/** "GHS 2.4B", "GHS 180.0M", "GHS 1,250" */
export function fmtCurrency(
  value: number,
  ccy: 'GHS' | 'USD' | 'NGN' | 'EUR' | 'GBP' = 'GHS',
  opts?: { compact?: boolean; decimals?: number }
): string {
  const decimals = opts?.decimals ?? 1;
  const symbol = ccy === 'USD' ? '$' : ccy;
  const prefix = ccy === 'USD' ? '$' : `${ccy} `;

  if (opts?.compact !== false) {
    const abs = Math.abs(value);
    if (abs >= 1_000_000_000) {
      return `${prefix}${(value / 1_000_000_000).toFixed(decimals)}B`;
    }
    if (abs >= 1_000_000) {
      return `${prefix}${(value / 1_000_000).toFixed(decimals)}M`;
    }
    if (abs >= 1_000) {
      return `${prefix}${(value / 1_000).toFixed(decimals)}K`;
    }
  }
  return `${prefix}${numberFmt.format(value)}`;
}

/** Full numeric without compact suffix. "GHS 2,400,000,000" */
export function fmtCurrencyFull(
  value: number,
  ccy: 'GHS' | 'USD' | 'NGN' | 'EUR' | 'GBP' = 'GHS'
): string {
  const prefix = ccy === 'USD' ? '$' : `${ccy} `;
  return `${prefix}${numberFmt.format(Math.round(value))}`;
}

/** "14.20%" */
export function fmtPct(value: number, decimals = 2): string {
  return `${value.toFixed(decimals)}%`;
}

/** "+14.2%" or "-3.4%" */
export function fmtPctSigned(value: number, decimals = 1): string {
  const sign = value >= 0 ? '+' : '';
  return `${sign}${value.toFixed(decimals)}%`;
}

/** "+GHS 12.4M" */
export function fmtCurrencySigned(
  value: number,
  ccy: 'GHS' | 'USD' = 'GHS'
): string {
  const sign = value >= 0 ? '+' : '-';
  return `${sign}${fmtCurrency(Math.abs(value), ccy)}`;
}

/** "142.0% / threshold 100%" — for ratio displays */
export function fmtRatioVsThreshold(value: number, threshold: number): string {
  const variance = value - threshold;
  const sign = variance >= 0 ? '+' : '';
  return `${sign}${variance.toFixed(1)} pts`;
}

/** "31 Mar 2026" */
export function fmtDate(d: Date): string {
  return d.toLocaleDateString('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  });
}
