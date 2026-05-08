/**
 * Liquidity Risk module — synthetic data anchored to Figma Design Brief:
 * - LCR ratio: 142%, NSFR: 118%
 * - HQLA Level 1: 65%, Level 2A: 25%, Level 2B: 10%
 * - 30-day net cash outflow: GHS 180M
 * - Threshold: 100% (BoG/Basel III)
 */

export const lcr = {
  current: 142.0,
  prior: 138.5,
  threshold: 100,
  internalBuffer: 110,
  netOutflowsGHS: 180_000_000,
  hqlaTotalGHS: 256_000_000, // 142% × 180M
  hqlaBreakdown: [
    {
      level: 'Level 1',
      label: 'BoG bills, GoG securities, central bank reserves',
      shareGHS: 166_400_000,
      pct: 65,
      haircut: 0,
      color: '#0E8A4F',
    },
    {
      level: 'Level 2A',
      label: 'Sovereign-backed, qualifying corporate bonds',
      shareGHS: 64_000_000,
      pct: 25,
      haircut: 15,
      color: '#2D7FF9',
    },
    {
      level: 'Level 2B',
      label: 'Lower-rated corporates, qualifying equities',
      shareGHS: 25_600_000,
      pct: 10,
      haircut: 50,
      color: '#1A4D5C',
    },
  ],
  outflows: [
    { item: 'Retail deposits — stable', balanceGHS: 940_000_000, runoffPct: 5, outflowGHS: 47_000_000 },
    { item: 'Retail deposits — less stable', balanceGHS: 280_000_000, runoffPct: 10, outflowGHS: 28_000_000 },
    { item: 'Operational wholesale deposits', balanceGHS: 320_000_000, runoffPct: 25, outflowGHS: 80_000_000 },
    { item: 'Non-operational wholesale, financial', balanceGHS: 140_000_000, runoffPct: 100, outflowGHS: 140_000_000 },
    { item: 'Secured funding (HQLA-backed)', balanceGHS: 90_000_000, runoffPct: 0, outflowGHS: 0 },
    { item: 'Committed credit & liquidity facilities', balanceGHS: 60_000_000, runoffPct: 30, outflowGHS: 18_000_000 },
    { item: 'Other contractual obligations', balanceGHS: 28_000_000, runoffPct: 100, outflowGHS: 28_000_000 },
  ],
  inflows: [
    { item: 'Performing retail loan repayments', balanceGHS: 110_000_000, inflowPct: 50, inflowGHS: 55_000_000 },
    { item: 'Wholesale counterparty inflows', balanceGHS: 60_000_000, inflowPct: 100, inflowGHS: 60_000_000 },
    { item: 'Reverse repo collateral release', balanceGHS: 30_000_000, inflowPct: 100, inflowGHS: 30_000_000 },
    { item: 'Other contractual receivables', balanceGHS: 16_000_000, inflowPct: 100, inflowGHS: 16_000_000 },
  ],
  history: [
    { month: 'Apr 25', value: 128.4 },
    { month: 'May 25', value: 131.2 },
    { month: 'Jun 25', value: 129.8 },
    { month: 'Jul 25', value: 134.5 },
    { month: 'Aug 25', value: 136.7 },
    { month: 'Sep 25', value: 134.1 },
    { month: 'Oct 25', value: 137.2 },
    { month: 'Nov 25', value: 139.5 },
    { month: 'Dec 25', value: 141.0 },
    { month: 'Jan 26', value: 138.8 },
    { month: 'Feb 26', value: 138.5 },
    { month: 'Mar 26', value: 142.0 },
  ],
};

export const nsfr = {
  current: 118.0,
  prior: 116.2,
  threshold: 100,
  asfGHS: 1_530_000_000,
  rsfGHS: 1_297_000_000,
  asfBreakdown: [
    { item: 'Tier 1 + Tier 2 capital', balanceGHS: 230_000_000, factor: 100, asfGHS: 230_000_000 },
    { item: 'Stable retail deposits (>1Y or sticky)', balanceGHS: 660_000_000, factor: 95, asfGHS: 627_000_000 },
    { item: 'Less stable retail deposits', balanceGHS: 280_000_000, factor: 90, asfGHS: 252_000_000 },
    { item: 'Operational wholesale (>1Y)', balanceGHS: 220_000_000, factor: 50, asfGHS: 110_000_000 },
    { item: 'Operational wholesale (<1Y)', balanceGHS: 320_000_000, factor: 50, asfGHS: 160_000_000 },
    { item: 'Non-operational wholesale (<6M)', balanceGHS: 215_000_000, factor: 70, asfGHS: 150_500_000 },
    { item: 'Other liabilities', balanceGHS: 75_000_000, factor: 0, asfGHS: 0 },
  ],
  rsfBreakdown: [
    { item: 'Cash and BoG reserves', balanceGHS: 220_000_000, factor: 0, rsfGHS: 0 },
    { item: 'Level 1 HQLA (excl. cash)', balanceGHS: 280_000_000, factor: 5, rsfGHS: 14_000_000 },
    { item: 'Performing loans to financials (<6M)', balanceGHS: 90_000_000, factor: 15, rsfGHS: 13_500_000 },
    { item: 'Performing loans to retail (<1Y)', balanceGHS: 540_000_000, factor: 50, rsfGHS: 270_000_000 },
    { item: 'Performing residential mortgages', balanceGHS: 380_000_000, factor: 65, rsfGHS: 247_000_000 },
    { item: 'Other performing loans (>1Y)', balanceGHS: 480_000_000, factor: 85, rsfGHS: 408_000_000 },
    { item: 'Non-performing assets, fixed assets', balanceGHS: 410_000_000, factor: 100, rsfGHS: 344_500_000 },
  ],
};

export type CashFlowPoint = {
  day: number;
  inflow: number;
  outflow: number;
  netLstm: number;
  netStatic: number;
  upper: number;
  lower: number;
};

/** 90-day projected daily net cash flow — LSTM forecast vs static behavioral */
export const cashFlowForecast: CashFlowPoint[] = (() => {
  const points: CashFlowPoint[] = [];
  for (let d = 1; d <= 90; d++) {
    // Base outflow with weekly seasonality (Mon-Fri higher)
    const weekday = d % 7;
    const isBusiness = weekday >= 1 && weekday <= 5;
    const baseOutflow = isBusiness ? 8.2 : 1.4; // GHS M
    const baseInflow = isBusiness ? 7.8 : 0.9;
    // Salary effect at month-end: outflow spike
    const monthEnd = d % 30 >= 27 || d % 30 <= 1;
    const salaryBump = monthEnd ? 12 : 0;
    // LSTM picks up seasonality; static misses it and over-estimates outflows on weekends
    const noise = Math.sin(d * 0.6) * 0.8 + Math.cos(d * 0.15) * 0.5;
    const inflow = baseInflow + noise * 0.4;
    const outflow = baseOutflow + salaryBump + noise * 0.6;
    const netLstm = inflow - outflow;
    // Static method assumes uniform daily decay — overestimates outflows
    const netStatic = netLstm - 1.6 - (monthEnd ? 0.4 : 0);
    points.push({
      day: d,
      inflow: Number(inflow.toFixed(2)),
      outflow: Number(outflow.toFixed(2)),
      netLstm: Number(netLstm.toFixed(2)),
      netStatic: Number(netStatic.toFixed(2)),
      upper: Number((netLstm + 1.4).toFixed(2)),
      lower: Number((netLstm - 1.4).toFixed(2)),
    });
  }
  return points;
})();

export const lstmAccuracy = {
  mape: 8.6, // mean absolute percent error %
  rmse: 1.42, // GHS M
  staticMape: 14.2,
  staticRmse: 2.31,
  improvementPct: 39.4,
};

export const stressScenarios = [
  {
    id: 'idiosyncratic',
    name: 'Idiosyncratic stress',
    description:
      'Counterparty-specific shock: 25% retail deposit run, 100% wholesale withdrawal, no contingent inflows from credit lines.',
    lcrAfter: 96.2,
    lcrChange: -45.8,
    nsfrAfter: 102.1,
    nsfrChange: -15.9,
    severity: 'amber',
    breachDay: null,
    notes:
      'LCR breaches 100% threshold but remains within BoG’s 30-day survival horizon. Recommend pre-positioned RSA at BoG and BoG repo facility activation.',
  },
  {
    id: 'market-wide',
    name: 'Market-wide stress',
    description:
      'System-wide shock: cedi depreciation 15%, T-bill yields +400bps, partial freeze in interbank market, BoG MPR cut 200bps.',
    lcrAfter: 119.4,
    lcrChange: -22.6,
    nsfrAfter: 109.8,
    nsfrChange: -8.2,
    severity: 'success',
    breachDay: null,
    notes:
      'LCR remains compliant. Slight NSFR pressure from RSF revaluation. HQLA composition shifts to higher Level 1 share due to cedi-denominated GoG securities.',
  },
  {
    id: 'combined',
    name: 'Combined stress (BoG severe)',
    description:
      'Concurrent idiosyncratic and market-wide shock per BoG ILAAP severe scenario. 30% deposit outflow, 200bps cedi depreciation amplification, no central bank backstop assumption.',
    lcrAfter: 81.6,
    lcrChange: -60.4,
    nsfrAfter: 94.7,
    nsfrChange: -23.3,
    severity: 'critical',
    breachDay: 22,
    notes:
      'LCR breaches both threshold and internal buffer at Day 22. NSFR drops below 100% by Day 30. Action plan: activate BoG ELA facility, restructure short-term wholesale funding, suspend non-essential contingent commitments.',
  },
];

export const submissionFormFields = [
  // Mirrors BoG LCR Form (BSD/2/2024 reporting standard) — selected line items
  { row: '1.1', item: 'Cash and balances with Bank of Ghana', amount: 220_000_000 },
  { row: '1.2', item: 'Government of Ghana Treasury bills', amount: 156_400_000 },
  { row: '1.3', item: 'BoG bills', amount: 64_000_000 },
  { row: '1.4', item: 'Other Level 1 sovereign claims', amount: 26_000_000 },
  { row: '2.1', item: 'Level 2A — qualifying corporate bonds (post-haircut)', amount: 54_400_000 },
  { row: '2.2', item: 'Level 2B — listed equities (post-haircut)', amount: 12_800_000 },
  { row: '3.0', item: 'TOTAL HIGH QUALITY LIQUID ASSETS', amount: 256_000_000 },
  { row: '4.1', item: 'Retail deposits — stable runoff', amount: 47_000_000 },
  { row: '4.2', item: 'Retail deposits — less stable runoff', amount: 28_000_000 },
  { row: '4.3', item: 'Operational wholesale deposits', amount: 80_000_000 },
  { row: '4.4', item: 'Non-operational wholesale outflows', amount: 140_000_000 },
  { row: '4.5', item: 'Committed facility drawdowns', amount: 18_000_000 },
  { row: '4.6', item: 'Other contractual outflows', amount: 28_000_000 },
  { row: '5.0', item: 'TOTAL CASH OUTFLOWS', amount: 341_000_000 },
  { row: '6.1', item: 'Performing retail loan inflows', amount: 55_000_000 },
  { row: '6.2', item: 'Wholesale counterparty inflows', amount: 60_000_000 },
  { row: '6.3', item: 'Other inflows', amount: 46_000_000 },
  { row: '7.0', item: 'TOTAL CASH INFLOWS (capped at 75%)', amount: 161_000_000 },
  { row: '8.0', item: 'NET CASH OUTFLOWS (Outflows − min(Inflows, 75%))', amount: 180_000_000 },
  { row: '9.0', item: 'LIQUIDITY COVERAGE RATIO (HQLA / Net Outflows)', amount: 142.2, isRatio: true },
];
