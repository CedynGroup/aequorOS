/**
 * Interest Rate Risk module — synthetic data.
 * Bank of Ghana CRD tenor buckets, GHS-denominated mid-tier universal bank.
 */

export type GapBucket = {
  tenor: string;
  rsa: number; // rate-sensitive assets, GHS
  rsl: number; // rate-sensitive liabilities, GHS
  gap: number; // RSA - RSL
  cumGap: number;
};

const rawBuckets: { tenor: string; rsa: number; rsl: number }[] = [
  { tenor: 'Overnight', rsa: 180_000_000, rsl: 240_000_000 },
  { tenor: '1M', rsa: 220_000_000, rsl: 290_000_000 },
  { tenor: '3M', rsa: 310_000_000, rsl: 350_000_000 },
  { tenor: '6M', rsa: 280_000_000, rsl: 240_000_000 },
  { tenor: '1Y', rsa: 360_000_000, rsl: 220_000_000 },
  { tenor: '2Y', rsa: 240_000_000, rsl: 140_000_000 },
  { tenor: '5Y', rsa: 180_000_000, rsl: 80_000_000 },
  { tenor: '10Y+', rsa: 95_000_000, rsl: 30_000_000 },
];

export const gapBuckets: GapBucket[] = (() => {
  let cum = 0;
  return rawBuckets.map((b) => {
    const gap = b.rsa - b.rsl;
    cum += gap;
    return { ...b, gap, cumGap: cum };
  });
})();

export const irrKpis = {
  niiAtRisk1Y: 18_400_000, // GHS 18.4M, +200bps shock
  niiAtRisk1YPct: 6.4,
  eveSensitivity200: -27_300_000, // GHS -27.3M, +200bps shock
  eveSensitivity200Pct: -11.3, // % of Tier 1
  eveLimitPct: 15.0,
  duration: 1.84, // years
  durationGap: 0.42,
};

export const niiHistory = [
  { month: 'Apr 25', actual: 22.4, projected: 22.0 },
  { month: 'May 25', actual: 22.8, projected: 22.5 },
  { month: 'Jun 25', actual: 23.1, projected: 22.9 },
  { month: 'Jul 25', actual: 23.4, projected: 23.2 },
  { month: 'Aug 25', actual: 23.6, projected: 23.4 },
  { month: 'Sep 25', actual: 23.9, projected: 23.6 },
  { month: 'Oct 25', actual: 24.2, projected: 23.9 },
  { month: 'Nov 25', actual: 24.5, projected: 24.2 },
  { month: 'Dec 25', actual: 24.8, projected: 24.5 },
  { month: 'Jan 26', actual: 25.1, projected: 24.7 },
  { month: 'Feb 26', actual: 25.3, projected: 25.0 },
  { month: 'Mar 26', actual: 25.6, projected: 25.4 },
];

export type RateScenario = {
  id: string;
  name: string;
  description: string;
  shockBps: number;
  niiImpactGHS: number;
  niiImpactPct: number;
  eveImpactGHS: number;
  eveImpactPct: number; // % of Tier 1
  withinPolicy: boolean;
};

export const rateScenarios: RateScenario[] = [
  {
    id: 'parallel-up-200',
    name: 'Parallel +200 bps',
    description: 'Uniform shift across all tenors. BoG ICAAP standard up-shock.',
    shockBps: 200,
    niiImpactGHS: 18_400_000,
    niiImpactPct: 6.4,
    eveImpactGHS: -27_300_000,
    eveImpactPct: -11.3,
    withinPolicy: true,
  },
  {
    id: 'parallel-down-200',
    name: 'Parallel −200 bps',
    description: 'Uniform downshift. Tests deposit floor effects and reinvestment risk.',
    shockBps: -200,
    niiImpactGHS: -16_900_000,
    niiImpactPct: -5.9,
    eveImpactGHS: 26_100_000,
    eveImpactPct: 10.8,
    withinPolicy: true,
  },
  {
    id: 'steepener',
    name: 'Steepener',
    description: 'Long rates +150bps, short rates −50bps. Curve steepens.',
    shockBps: 0,
    niiImpactGHS: 9_200_000,
    niiImpactPct: 3.2,
    eveImpactGHS: -14_800_000,
    eveImpactPct: -6.1,
    withinPolicy: true,
  },
  {
    id: 'flattener',
    name: 'Flattener',
    description: 'Short rates +150bps, long rates +25bps. Curve flattens.',
    shockBps: 0,
    niiImpactGHS: -8_700_000,
    niiImpactPct: -3.0,
    eveImpactGHS: 11_400_000,
    eveImpactPct: 4.7,
    withinPolicy: true,
  },
  {
    id: 'twist',
    name: 'Twist (short up, long down)',
    description: 'Short +200bps, long −100bps. Tests asymmetric exposure.',
    shockBps: 0,
    niiImpactGHS: -12_400_000,
    niiImpactPct: -4.3,
    eveImpactGHS: 18_900_000,
    eveImpactPct: 7.8,
    withinPolicy: true,
  },
];

export type IRSContract = {
  id: string;
  notional: number;
  tenor: string;
  payRate: string;
  receiveRate: string;
  effectiveDate: string;
  maturity: string;
  mtm: number;
  ccy: 'GHS' | 'USD';
};

export const irsPortfolio: IRSContract[] = [
  { id: 'IRS-2024-018', notional: 80_000_000, tenor: '5Y', payRate: '24.50%', receiveRate: '91d T-bill', effectiveDate: '15 Sep 2024', maturity: '15 Sep 2029', mtm: 2_850_000, ccy: 'GHS' },
  { id: 'IRS-2024-022', notional: 60_000_000, tenor: '3Y', payRate: '23.80%', receiveRate: '91d T-bill', effectiveDate: '02 Nov 2024', maturity: '02 Nov 2027', mtm: 1_120_000, ccy: 'GHS' },
  { id: 'IRS-2025-007', notional: 40_000_000, tenor: '2Y', payRate: '25.40%', receiveRate: '91d T-bill', effectiveDate: '12 Mar 2025', maturity: '12 Mar 2027', mtm: 480_000, ccy: 'GHS' },
  { id: 'IRS-2025-014', notional: 25_000_000, tenor: '1Y', payRate: '26.10%', receiveRate: '91d T-bill', effectiveDate: '08 Jul 2025', maturity: '08 Jul 2026', mtm: -190_000, ccy: 'GHS' },
  { id: 'IRS-USD-001', notional: 6_000_000, tenor: '3Y', payRate: 'SOFR+180', receiveRate: '5.20%', effectiveDate: '20 Jan 2026', maturity: '20 Jan 2029', mtm: 84_000, ccy: 'USD' },
];

export const hedgeRecommendations = [
  {
    id: 'rec-1',
    title: 'Add 6M IRS notional GHS 50M, pay fixed at 25.30%, receive 91d T-bill',
    rationale:
      'EVE sensitivity to +200bps approaching policy cap (11.3% vs 15% limit). 6M tenor closes the largest open gap (1Y bucket asset-heavy). Pay-fixed leg offsets net asset position; floating receive aligned with re-priceable liability profile.',
    expectedImpact:
      'EVE sensitivity reduces to 8.4% of Tier 1. NII sensitivity reduces to +4.8% (vs current +6.4%). Marginal hedge cost ~GHS 320K/yr against EVE buffer protection of GHS 7M.',
    confidence: 0.81,
  },
  {
    id: 'rec-2',
    title: 'Reduce 5Y IRS-2024-018 notional by GHS 20M (partial unwind)',
    rationale:
      'Long-end is over-hedged following Q1 deposit base re-pricing. Curve steepener exposure has flipped favorable; partial unwind crystallizes MTM gain and frees Tier 1 capital under SA-CCR.',
    expectedImpact:
      'GHS 0.7M MTM realized P&L. SA-CCR exposure reduces by GHS 4.2M. Net IRR exposure remains within all bucket limits.',
    confidence: 0.74,
  },
  {
    id: 'rec-3',
    title: 'Initiate 3Y receiver swaption, strike at 22%, premium GHS 180K',
    rationale:
      'Macro view (BoG MPR cut path) and NII at risk to −200bps shock (−5.9%) suggest convexity hedge value. Out-of-the-money swaption captures downside protection cheaply.',
    expectedImpact:
      'Caps NII at risk under severe −300bps scenario at −7.8% (from −9.2% unhedged). Premium recovers in Year 2 if MPR cuts ≥ 75bps occur.',
    confidence: 0.62,
  },
];
