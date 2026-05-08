/**
 * Basel Capital module — synthetic data.
 * BoG Capital Requirements Directive, 10% minimum CAR, 2.5% conservation buffer.
 */

export const capital = {
  car: 14.2, // % CAR
  carPrior: 13.8,
  tier1: 11.8,
  tier2: 2.4,
  bogMinimum: 10.0,
  conservationBuffer: 2.5,
  countercyclicalBuffer: 0.5,
  dsibBuffer: 0.0, // not D-SIB designated
  totalRequiredRatio: 13.0,
  internalBuffer: 13.5,
  totalRwaGHS: 1_620_000_000,
  totalCapitalGHS: 230_000_000,
};

export const rwaBreakdown = [
  { category: 'Credit Risk', subcategory: 'Corporate exposures', share: 38, rwaGHS: 615_600_000, color: '#0A2540' },
  { category: 'Credit Risk', subcategory: 'SME exposures', share: 14, rwaGHS: 226_800_000, color: '#143055' },
  { category: 'Credit Risk', subcategory: 'Retail mortgages', share: 11, rwaGHS: 178_200_000, color: '#1A4D5C' },
  { category: 'Credit Risk', subcategory: 'Other retail', share: 7, rwaGHS: 113_400_000, color: '#2D7FF9' },
  { category: 'Market Risk', subcategory: 'FX risk', share: 4, rwaGHS: 64_800_000, color: '#5A8DDB' },
  { category: 'Market Risk', subcategory: 'Interest rate risk', share: 4, rwaGHS: 64_800_000, color: '#7AAEEB' },
  { category: 'Operational Risk', subcategory: 'Standardized', share: 22, rwaGHS: 356_400_000, color: '#1F6CE0' },
];

export const rwaByCategory = [
  { category: 'Credit Risk', share: 70, rwaGHS: 1_134_000_000, color: '#0A2540' },
  { category: 'Operational Risk', share: 22, rwaGHS: 356_400_000, color: '#1F6CE0' },
  { category: 'Market Risk', share: 8, rwaGHS: 129_600_000, color: '#2D7FF9' },
];

export const capitalStructure = {
  cet1: {
    items: [
      { item: 'Common equity (paid-up)', amountGHS: 95_000_000 },
      { item: 'Retained earnings', amountGHS: 78_000_000 },
      { item: 'Statutory reserves', amountGHS: 24_000_000 },
      { item: 'Other reserves', amountGHS: 14_000_000 },
    ],
    deductions: [
      { item: 'Goodwill and intangibles', amountGHS: -8_000_000 },
      { item: 'Deferred tax assets (excess over 10% CET1)', amountGHS: -4_000_000 },
      { item: 'Investments in financial entities', amountGHS: -3_000_000 },
    ],
    total: 196_000_000,
  },
  at1: {
    items: [
      { item: 'Perpetual non-cumulative preference shares', amountGHS: 0 },
    ],
    deductions: [],
    total: 0,
  },
  tier2: {
    items: [
      { item: 'Subordinated debt (10Y, qualifying)', amountGHS: 28_000_000 },
      { item: 'General loan loss reserves (capped)', amountGHS: 11_000_000 },
    ],
    deductions: [],
    total: 39_000_000,
  },
};

export const carHistory = [
  { month: 'Apr 25', value: 13.0 },
  { month: 'May 25', value: 13.2 },
  { month: 'Jun 25', value: 13.5 },
  { month: 'Jul 25', value: 13.6 },
  { month: 'Aug 25', value: 13.8 },
  { month: 'Sep 25', value: 13.9 },
  { month: 'Oct 25', value: 14.1 },
  { month: 'Nov 25', value: 14.0 },
  { month: 'Dec 25', value: 14.1 },
  { month: 'Jan 26', value: 13.9 },
  { month: 'Feb 26', value: 14.0 },
  { month: 'Mar 26', value: 14.2 },
];

export type CapitalStressScenario = {
  id: string;
  name: string;
  severity: 'success' | 'amber' | 'critical';
  description: string;
  monthsAhead: { month: string; car: number; tier1: number }[];
  endStateCar: number;
  endStateTier1: number;
  rwaGrowthPct: number;
};

export const capitalStressScenarios: CapitalStressScenario[] = [
  {
    id: 'mild',
    name: 'Mild — Baseline path',
    severity: 'success',
    description: 'GDP growth 5%, BoG MPR steady, NPL ratio stable at 4.2%, modest deposit and asset growth.',
    monthsAhead: [
      { month: '+0M', car: 14.2, tier1: 11.8 },
      { month: '+3M', car: 14.4, tier1: 12.0 },
      { month: '+6M', car: 14.5, tier1: 12.1 },
      { month: '+9M', car: 14.6, tier1: 12.2 },
      { month: '+12M', car: 14.8, tier1: 12.3 },
    ],
    endStateCar: 14.8,
    endStateTier1: 12.3,
    rwaGrowthPct: 8,
  },
  {
    id: 'moderate',
    name: 'Moderate — Slowdown',
    severity: 'amber',
    description: 'GDP growth slows to 2%, NPL ratio drifts to 6.8%, cedi depreciates 12%, RWAs rise on FX revaluation.',
    monthsAhead: [
      { month: '+0M', car: 14.2, tier1: 11.8 },
      { month: '+3M', car: 13.6, tier1: 11.4 },
      { month: '+6M', car: 13.0, tier1: 10.8 },
      { month: '+9M', car: 12.4, tier1: 10.3 },
      { month: '+12M', car: 11.8, tier1: 9.7 },
    ],
    endStateCar: 11.8,
    endStateTier1: 9.7,
    rwaGrowthPct: 16,
  },
  {
    id: 'severe',
    name: 'Severe — BoG ICAAP severe',
    severity: 'critical',
    description: 'Cedi −20%, MPR +500bps, NPL spike to 12%, sovereign downgrade, deposit run on largest counterparty.',
    monthsAhead: [
      { month: '+0M', car: 14.2, tier1: 11.8 },
      { month: '+3M', car: 12.4, tier1: 10.2 },
      { month: '+6M', car: 10.8, tier1: 8.7 },
      { month: '+9M', car: 9.6, tier1: 7.5 },
      { month: '+12M', car: 8.4, tier1: 6.4 },
    ],
    endStateCar: 8.4,
    endStateTier1: 6.4,
    rwaGrowthPct: 24,
  },
];

export const baselSubmissions = [
  { regulator: 'Bank of Ghana', form: 'CAR-Q', name: 'Capital Adequacy Return — Q1 2026', frequency: 'Quarterly', due: '15 Apr 2026', tone: 'amber' as const, status: 'Pending data' },
  { regulator: 'Bank of Ghana', form: 'BSD-2', name: 'Monthly Prudential Return', frequency: 'Monthly', due: '10 Apr 2026', tone: 'amber' as const, status: 'In review' },
  { regulator: 'Bank of Ghana', form: 'ICAAP', name: 'Internal Capital Adequacy Assessment', frequency: 'Semi-annual', due: '30 Jun 2026', tone: 'slate' as const, status: 'Drafting' },
  { regulator: 'Central Bank of Nigeria', form: 'CAR-N', name: 'Nigeria CAR Return (subsidiary view)', frequency: 'Quarterly', due: '15 Apr 2026', tone: 'success' as const, status: 'Ready' },
  { regulator: 'SARB', form: 'BA200', name: 'South Africa Capital Adequacy', frequency: 'Quarterly', due: '20 Apr 2026', tone: 'slate' as const, status: 'Drafting' },
  { regulator: 'CBK', form: 'CBK-CAR', name: 'Kenya Capital Adequacy', frequency: 'Quarterly', due: '15 May 2026', tone: 'slate' as const, status: 'Not started' },
];
