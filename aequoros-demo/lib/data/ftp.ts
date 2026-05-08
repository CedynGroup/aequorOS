/**
 * Funds Transfer Pricing module — synthetic data.
 * Ghana yield curve, branch profitability matrix, product spread analysis.
 */

export type CurvePoint = { tenor: string; days: number; bog: number; deposit: number; lending: number; ftp: number };

export const yieldCurve: CurvePoint[] = [
  { tenor: '7d', days: 7, bog: 21.5, deposit: 18.0, lending: 26.5, ftp: 22.5 },
  { tenor: '14d', days: 14, bog: 22.0, deposit: 18.5, lending: 27.0, ftp: 22.8 },
  { tenor: '1M', days: 30, bog: 23.4, deposit: 19.2, lending: 28.4, ftp: 23.9 },
  { tenor: '3M', days: 91, bog: 25.4, deposit: 21.5, lending: 30.5, ftp: 25.8 },
  { tenor: '6M', days: 182, bog: 26.1, deposit: 22.4, lending: 31.2, ftp: 26.6 },
  { tenor: '1Y', days: 364, bog: 27.0, deposit: 23.4, lending: 32.2, ftp: 27.5 },
  { tenor: '2Y', days: 730, bog: 27.8, deposit: 24.0, lending: 33.0, ftp: 28.2 },
  { tenor: '3Y', days: 1095, bog: 28.4, deposit: 24.6, lending: 33.6, ftp: 28.8 },
  { tenor: '5Y', days: 1825, bog: 28.9, deposit: 25.0, lending: 34.1, ftp: 29.3 },
];

export type Branch = {
  id: string;
  name: string;
  region: string;
  depositsGHS: number;
  loansGHS: number;
  niiGHS: number;
  ftpAdjustedNimPct: number;
  rank: number;
  trend: 'up' | 'down' | 'flat';
};

export const branches: Branch[] = [
  { id: 'B-001', name: 'Accra Central', region: 'Greater Accra', depositsGHS: 180_000_000, loansGHS: 145_000_000, niiGHS: 8_400_000, ftpAdjustedNimPct: 4.92, rank: 1, trend: 'up' },
  { id: 'B-002', name: 'Tema Industrial', region: 'Greater Accra', depositsGHS: 160_000_000, loansGHS: 138_000_000, niiGHS: 7_650_000, ftpAdjustedNimPct: 4.78, rank: 2, trend: 'up' },
  { id: 'B-003', name: 'Kumasi Adum', region: 'Ashanti', depositsGHS: 140_000_000, loansGHS: 110_000_000, niiGHS: 6_500_000, ftpAdjustedNimPct: 4.65, rank: 3, trend: 'up' },
  { id: 'B-004', name: 'Takoradi Market', region: 'Western', depositsGHS: 110_000_000, loansGHS: 88_000_000, niiGHS: 5_120_000, ftpAdjustedNimPct: 4.51, rank: 4, trend: 'up' },
  { id: 'B-005', name: 'Cape Coast', region: 'Central', depositsGHS: 95_000_000, loansGHS: 70_000_000, niiGHS: 4_280_000, ftpAdjustedNimPct: 4.42, rank: 5, trend: 'flat' },
  { id: 'B-006', name: 'Kumasi Asafo', region: 'Ashanti', depositsGHS: 92_000_000, loansGHS: 68_000_000, niiGHS: 4_150_000, ftpAdjustedNimPct: 4.38, rank: 6, trend: 'flat' },
  { id: 'B-007', name: 'Ho Central', region: 'Volta', depositsGHS: 85_000_000, loansGHS: 64_000_000, niiGHS: 3_870_000, ftpAdjustedNimPct: 4.31, rank: 7, trend: 'up' },
  { id: 'B-008', name: 'Tamale', region: 'Northern', depositsGHS: 78_000_000, loansGHS: 56_000_000, niiGHS: 3_540_000, ftpAdjustedNimPct: 4.22, rank: 8, trend: 'flat' },
  { id: 'B-009', name: 'Sunyani', region: 'Bono', depositsGHS: 72_000_000, loansGHS: 52_000_000, niiGHS: 3_240_000, ftpAdjustedNimPct: 4.18, rank: 9, trend: 'flat' },
  { id: 'B-010', name: 'Koforidua', region: 'Eastern', depositsGHS: 68_000_000, loansGHS: 48_000_000, niiGHS: 3_080_000, ftpAdjustedNimPct: 4.14, rank: 10, trend: 'flat' },
  { id: 'B-011', name: 'Wa', region: 'Upper West', depositsGHS: 58_000_000, loansGHS: 38_000_000, niiGHS: 2_440_000, ftpAdjustedNimPct: 3.98, rank: 11, trend: 'flat' },
  { id: 'B-012', name: 'Bolgatanga', region: 'Upper East', depositsGHS: 55_000_000, loansGHS: 36_000_000, niiGHS: 2_280_000, ftpAdjustedNimPct: 3.92, rank: 12, trend: 'down' },
  { id: 'B-013', name: 'Accra East', region: 'Greater Accra', depositsGHS: 52_000_000, loansGHS: 42_000_000, niiGHS: 2_180_000, ftpAdjustedNimPct: 3.86, rank: 13, trend: 'down' },
  { id: 'B-014', name: 'Spintex', region: 'Greater Accra', depositsGHS: 48_000_000, loansGHS: 40_000_000, niiGHS: 1_960_000, ftpAdjustedNimPct: 3.74, rank: 14, trend: 'down' },
  { id: 'B-015', name: 'Madina', region: 'Greater Accra', depositsGHS: 46_000_000, loansGHS: 36_000_000, niiGHS: 1_820_000, ftpAdjustedNimPct: 3.62, rank: 15, trend: 'down' },
  { id: 'B-016', name: 'Obuasi', region: 'Ashanti', depositsGHS: 42_000_000, loansGHS: 32_000_000, niiGHS: 1_640_000, ftpAdjustedNimPct: 3.48, rank: 16, trend: 'down' },
  { id: 'B-017', name: 'Techiman', region: 'Bono East', depositsGHS: 38_000_000, loansGHS: 28_000_000, niiGHS: 1_420_000, ftpAdjustedNimPct: 3.32, rank: 17, trend: 'down' },
  { id: 'B-018', name: 'Bawku', region: 'Upper East', depositsGHS: 32_000_000, loansGHS: 22_000_000, niiGHS: 1_180_000, ftpAdjustedNimPct: 3.04, rank: 18, trend: 'down' },
];

export type ProductLine = {
  product: string;
  category: 'Asset' | 'Liability';
  balanceGHS: number;
  yieldPct: number;
  ftpRatePct: number;
  spreadPct: number;
  contributionGHS: number;
};

export const productLines: ProductLine[] = [
  { product: 'Corporate term loans', category: 'Asset', balanceGHS: 540_000_000, yieldPct: 30.5, ftpRatePct: 26.6, spreadPct: 3.9, contributionGHS: 21_060_000 },
  { product: 'SME loans', category: 'Asset', balanceGHS: 320_000_000, yieldPct: 32.4, ftpRatePct: 27.5, spreadPct: 4.9, contributionGHS: 15_680_000 },
  { product: 'Residential mortgages', category: 'Asset', balanceGHS: 380_000_000, yieldPct: 28.2, ftpRatePct: 26.6, spreadPct: 1.6, contributionGHS: 6_080_000 },
  { product: 'Other retail loans', category: 'Asset', balanceGHS: 160_000_000, yieldPct: 33.8, ftpRatePct: 27.5, spreadPct: 6.3, contributionGHS: 10_080_000 },
  { product: 'GoG securities', category: 'Asset', balanceGHS: 580_000_000, yieldPct: 25.4, ftpRatePct: 25.8, spreadPct: -0.4, contributionGHS: -2_320_000 },
  { product: 'Interbank placements', category: 'Asset', balanceGHS: 220_000_000, yieldPct: 24.6, ftpRatePct: 23.9, spreadPct: 0.7, contributionGHS: 1_540_000 },
  { product: 'Current accounts (NMD)', category: 'Liability', balanceGHS: 720_000_000, yieldPct: 0.0, ftpRatePct: 22.5, spreadPct: 22.5, contributionGHS: 162_000_000 / 10 }, // contribution scaled
  { product: 'Savings accounts (NMD)', category: 'Liability', balanceGHS: 480_000_000, yieldPct: 8.5, ftpRatePct: 23.9, spreadPct: 15.4, contributionGHS: 73_920_000 / 10 },
  { product: 'Term deposits 3M', category: 'Liability', balanceGHS: 280_000_000, yieldPct: 21.5, ftpRatePct: 25.8, spreadPct: 4.3, contributionGHS: 12_040_000 / 10 },
  { product: 'Term deposits 1Y', category: 'Liability', balanceGHS: 220_000_000, yieldPct: 23.4, ftpRatePct: 27.5, spreadPct: 4.1, contributionGHS: 9_020_000 / 10 },
  { product: 'Wholesale funding', category: 'Liability', balanceGHS: 200_000_000, yieldPct: 25.5, ftpRatePct: 26.6, spreadPct: 1.1, contributionGHS: 2_200_000 / 10 },
];

export type FtpRateEntry = {
  product: string;
  tenor: string;
  rate: number;
  effectiveFrom: string;
  prevRate: number;
};

export const ftpRates: FtpRateEntry[] = [
  { product: 'Demand deposit', tenor: 'NMD', rate: 22.5, effectiveFrom: '01 Apr 2026', prevRate: 22.0 },
  { product: 'Savings deposit', tenor: 'NMD', rate: 23.9, effectiveFrom: '01 Apr 2026', prevRate: 23.4 },
  { product: 'Term deposit', tenor: '1M', rate: 23.9, effectiveFrom: '01 Apr 2026', prevRate: 23.5 },
  { product: 'Term deposit', tenor: '3M', rate: 25.8, effectiveFrom: '01 Apr 2026', prevRate: 25.4 },
  { product: 'Term deposit', tenor: '6M', rate: 26.6, effectiveFrom: '01 Apr 2026', prevRate: 26.2 },
  { product: 'Term deposit', tenor: '1Y', rate: 27.5, effectiveFrom: '01 Apr 2026', prevRate: 27.0 },
  { product: 'Lending — Corporate', tenor: '5Y', rate: 29.3, effectiveFrom: '01 Apr 2026', prevRate: 28.8 },
  { product: 'Lending — SME', tenor: '3Y', rate: 28.8, effectiveFrom: '01 Apr 2026', prevRate: 28.4 },
  { product: 'Lending — Mortgage', tenor: '10Y', rate: 29.6, effectiveFrom: '01 Apr 2026', prevRate: 29.1 },
];

export const ftpRateHistory = (() => {
  const months = [
    'Apr 25', 'May 25', 'Jun 25', 'Jul 25', 'Aug 25', 'Sep 25',
    'Oct 25', 'Nov 25', 'Dec 25', 'Jan 26', 'Feb 26', 'Mar 26',
  ];
  const base3M = 24.4;
  const base1Y = 26.2;
  return months.map((m, i) => ({
    month: m,
    '3M': Number((base3M + i * 0.12 + Math.sin(i * 0.5) * 0.18).toFixed(2)),
    '1Y': Number((base1Y + i * 0.11 + Math.cos(i * 0.4) * 0.20).toFixed(2)),
  }));
})();
