/**
 * FX Risk module — synthetic data.
 * BoG net open position framework, Ghana mid-tier bank with USD funding lines.
 */

export type CurrencyPosition = {
  ccy: 'USD' | 'EUR' | 'GBP' | 'NGN' | 'XOF';
  ccyName: string;
  assetsGHS: number;
  liabilitiesGHS: number;
  derivativesGHS: number;
  netGHS: number;
  netPctOfCapital: number;
  spot: number;
  spotChange1d: number;
};

export const fxPositions: CurrencyPosition[] = [
  {
    ccy: 'USD',
    ccyName: 'US Dollar',
    assetsGHS: 540_000_000,
    liabilitiesGHS: -440_000_000,
    derivativesGHS: -28_000_000,
    netGHS: 72_000_000,
    netPctOfCapital: 3.13,
    spot: 12.5,
    spotChange1d: 0.04,
  },
  {
    ccy: 'EUR',
    ccyName: 'Euro',
    assetsGHS: 96_000_000,
    liabilitiesGHS: -120_000_000,
    derivativesGHS: 0,
    netGHS: -24_000_000,
    netPctOfCapital: -1.04,
    spot: 13.55,
    spotChange1d: -0.02,
  },
  {
    ccy: 'GBP',
    ccyName: 'British Pound',
    assetsGHS: 38_000_000,
    liabilitiesGHS: -50_000_000,
    derivativesGHS: 0,
    netGHS: -12_000_000,
    netPctOfCapital: -0.52,
    spot: 15.8,
    spotChange1d: 0.01,
  },
  {
    ccy: 'NGN',
    ccyName: 'Nigerian Naira',
    assetsGHS: 22_000_000,
    liabilitiesGHS: -16_000_000,
    derivativesGHS: -2_000_000,
    netGHS: 4_000_000,
    netPctOfCapital: 0.17,
    spot: 0.0083,
    spotChange1d: -0.0001,
  },
  {
    ccy: 'XOF',
    ccyName: 'CFA Franc',
    assetsGHS: 8_000_000,
    liabilitiesGHS: -6_000_000,
    derivativesGHS: 0,
    netGHS: 2_000_000,
    netPctOfCapital: 0.09,
    spot: 0.0207,
    spotChange1d: 0.0001,
  },
];

export const fxKpis = {
  netOpenPositionGHS: 110_000_000,
  netOpenPositionPctCapital: 4.78,
  bogLimitPct: 5.0,
  varDaily99: 6_400_000, // GHS 1d, 99%
  expectedShortfall: 8_900_000,
  hedgeRatio: 0.74,
};

export type FxScenario = {
  id: string;
  name: string;
  ghsUsdShock: number; // % depreciation
  pnlImpactGHS: number;
  capitalImpactPct: number;
  description: string;
};

export const fxScenarios: FxScenario[] = [
  {
    id: 'mild',
    name: 'Mild — 5% cedi depreciation',
    ghsUsdShock: 5,
    pnlImpactGHS: 3_600_000,
    capitalImpactPct: 0.16,
    description: 'GHS/USD moves from 12.50 to 13.13. Short EUR/GBP partial offset.',
  },
  {
    id: 'moderate',
    name: 'Moderate — 10% cedi depreciation',
    ghsUsdShock: 10,
    pnlImpactGHS: 7_200_000,
    capitalImpactPct: 0.31,
    description: 'GHS/USD to 13.75. Comparable to 2022 H2 stress episode.',
  },
  {
    id: 'severe',
    name: 'Severe — 20% cedi depreciation',
    ghsUsdShock: 20,
    pnlImpactGHS: 14_400_000,
    capitalImpactPct: 0.63,
    description: 'GHS/USD to 15.00. Comparable to 2014 emerging-market sudden-stop.',
  },
];

export const fxRatePrediction = (() => {
  const points: { day: number; actual: number | null; predicted: number; upper: number; lower: number; forward: number }[] = [];
  let actual = 12.50;
  for (let d = -90; d <= 0; d++) {
    actual += (Math.sin(d * 0.07) * 0.01 + (Math.random() - 0.5) * 0.012);
    actual = Math.max(12.10, Math.min(12.85, actual));
    points.push({
      day: d,
      actual: Number(actual.toFixed(3)),
      predicted: Number(actual.toFixed(3)),
      upper: Number(actual.toFixed(3)),
      lower: Number(actual.toFixed(3)),
      forward: Number(actual.toFixed(3)),
    });
  }
  // Forward 90-day prediction
  let pred = 12.5;
  let fwd = 12.5;
  for (let d = 1; d <= 90; d++) {
    pred += 0.0048 + (Math.sin(d * 0.12) * 0.006);
    fwd += 0.0023 + (Math.cos(d * 0.08) * 0.003);
    const band = 0.04 + d * 0.005;
    points.push({
      day: d,
      actual: null,
      predicted: Number(pred.toFixed(3)),
      upper: Number((pred + band).toFixed(3)),
      lower: Number((pred - band).toFixed(3)),
      forward: Number(fwd.toFixed(3)),
    });
  }
  return points;
})();

export const fxModelAccuracy = {
  mape: 1.4, // %
  rmse: 0.18, // GHS
  forwardImpliedMape: 2.6,
  hitRate: 0.71, // direction correct
  modelType: 'Ensemble (XGBoost + LSTM)',
};

export type FxHedge = {
  id: string;
  type: 'Forward' | 'IRS' | 'Cross-currency swap' | 'Option';
  pair: string;
  notional: number;
  ccy: 'USD' | 'EUR' | 'GBP';
  rate: number;
  effectiveDate: string;
  maturity: string;
  daysToMaturity: number;
  mtmGHS: number;
  status: 'Active' | 'Expiring' | 'Drift';
};

export const fxHedges: FxHedge[] = [
  { id: 'FX-2025-091', type: 'Forward', pair: 'GHS/USD', notional: 4_000_000, ccy: 'USD', rate: 12.62, effectiveDate: '12 Aug 2025', maturity: '12 Apr 2026', daysToMaturity: 11, mtmGHS: 280_000, status: 'Expiring' },
  { id: 'FX-2025-104', type: 'Forward', pair: 'GHS/USD', notional: 3_000_000, ccy: 'USD', rate: 12.78, effectiveDate: '02 Oct 2025', maturity: '02 Jul 2026', daysToMaturity: 92, mtmGHS: 90_000, status: 'Active' },
  { id: 'FX-2025-118', type: 'Cross-currency swap', pair: 'GHS/USD', notional: 8_000_000, ccy: 'USD', rate: 12.55, effectiveDate: '15 Nov 2025', maturity: '15 Nov 2027', daysToMaturity: 593, mtmGHS: 410_000, status: 'Active' },
  { id: 'FX-2026-002', type: 'Forward', pair: 'GHS/EUR', notional: 1_500_000, ccy: 'EUR', rate: 13.40, effectiveDate: '08 Jan 2026', maturity: '08 Jul 2026', daysToMaturity: 98, mtmGHS: -120_000, status: 'Drift' },
  { id: 'FX-2026-007', type: 'Option', pair: 'GHS/USD put', notional: 2_000_000, ccy: 'USD', rate: 12.30, effectiveDate: '14 Feb 2026', maturity: '14 May 2026', daysToMaturity: 43, mtmGHS: 38_000, status: 'Active' },
];

export const correlationMatrix = [
  { row: 'GHS/USD', cols: { 'GHS/USD': 1.00, 'GHS/EUR': 0.78, 'GHS/GBP': 0.69, 'NGN/USD': 0.42, 'XOF/USD': 0.81 } },
  { row: 'GHS/EUR', cols: { 'GHS/USD': 0.78, 'GHS/EUR': 1.00, 'GHS/GBP': 0.85, 'NGN/USD': 0.31, 'XOF/USD': 0.92 } },
  { row: 'GHS/GBP', cols: { 'GHS/USD': 0.69, 'GHS/EUR': 0.85, 'GHS/GBP': 1.00, 'NGN/USD': 0.28, 'XOF/USD': 0.79 } },
  { row: 'NGN/USD', cols: { 'GHS/USD': 0.42, 'GHS/EUR': 0.31, 'GHS/GBP': 0.28, 'NGN/USD': 1.00, 'XOF/USD': 0.34 } },
  { row: 'XOF/USD', cols: { 'GHS/USD': 0.81, 'GHS/EUR': 0.92, 'GHS/GBP': 0.79, 'NGN/USD': 0.34, 'XOF/USD': 1.00 } },
];
