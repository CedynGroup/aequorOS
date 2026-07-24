/**
 * Numerical formatters for banking displays.
 * Per design brief: thousands separators, consistent decimals, currency prefixes.
 *
 * JURISDICTION-AWARE: the active bank's country identity (currency, locale,
 * regulator names) is resolved by the backend from the jurisdictions registry
 * and bound here by BankContext via `setActiveJurisdiction`. Formatters and the
 * `regShort()`/`centralBankName()`/`countryName()` getters read that binding —
 * NEVER hardcode 'GHS', 'en-GH', 'BoG', or 'Bank of Ghana' in display code.
 * The GH values below are only the pre-resolution defaults.
 */

export interface ActiveJurisdiction {
  currencyCode: string;
  locale: string;
  regulatorShort: string;
  centralBankName: string;
  countryName: string;
  submissionPortal: string | null;
}

const DEFAULT_JURISDICTION: ActiveJurisdiction = {
  currencyCode: 'GHS',
  locale: 'en-GH',
  regulatorShort: 'BoG',
  centralBankName: 'Bank of Ghana',
  countryName: 'Ghana',
  submissionPortal: 'ORASS',
};

let active: ActiveJurisdiction = DEFAULT_JURISDICTION;
let localeNumberFmt = new Intl.NumberFormat(active.locale);

/** Bound by BankContext whenever the selected bank (or its registry row) changes. */
export function setActiveJurisdiction(next: Partial<ActiveJurisdiction>): void {
  active = { ...DEFAULT_JURISDICTION, ...next };
  localeNumberFmt = new Intl.NumberFormat(active.locale);
}

export function currencyCode(): string {
  return active.currencyCode;
}
export function fmtLocale(): string {
  return active.locale;
}
export function regShort(): string {
  return active.regulatorShort;
}
export function centralBankName(): string {
  return active.centralBankName;
}
export function countryName(): string {
  return active.countryName;
}
export function submissionPortal(): string | null {
  return active.submissionPortal;
}

/** "1,234,567" in the bank's locale */
export function fmtNum(value: number, fractionDigits = 0): string {
  return value.toLocaleString(active.locale, {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  });
}

/** Locale-formatted integer — replaces ad-hoc toLocaleString('en-GH') calls. */
export function fmtInt(value: number): string {
  return localeNumberFmt.format(value);
}

/** "GHS 2.4B", "GHS 180.0M", "GHS 1,250" — currency defaults to the bank's. */
export function fmtCurrency(
  value: number,
  ccy?: string,
  opts?: { compact?: boolean; decimals?: number }
): string {
  const code = ccy ?? active.currencyCode;
  const decimals = opts?.decimals ?? 1;
  const prefix = code === 'USD' ? '$' : `${code} `;

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
  return `${prefix}${localeNumberFmt.format(value)}`;
}

/** Full numeric without compact suffix. "GHS 2,400,000,000" */
export function fmtCurrencyFull(value: number, ccy?: string): string {
  const code = ccy ?? active.currencyCode;
  const prefix = code === 'USD' ? '$' : `${code} `;
  return `${prefix}${localeNumberFmt.format(Math.round(value))}`;
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
export function fmtCurrencySigned(value: number, ccy?: string): string {
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
