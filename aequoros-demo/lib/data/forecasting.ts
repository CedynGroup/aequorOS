/**
 * Balance Sheet Forecasting module — synthetic data.
 * 12/24/36-month projections; RL strategic optimizer; macro what-if scenarios.
 */

export type BalanceSheetProjection = {
  month: string;
  monthIdx: number;
  assets: number; // GHS M
  loans: number;
  govSecs: number;
  cashAndBoG: number;
  liabilities: number;
  deposits: number;
  wholesaleFunding: number;
  equity: number;
  car: number;
  ldr: number;
  nim: number;
};

export const projection: BalanceSheetProjection[] = (() => {
  const points: BalanceSheetProjection[] = [];
  let assets = 2400;
  let loans = 1400;
  let govSecs = 580;
  let cash = 220;
  let deposits = 1900;
  let wholesale = 270;
  let equity = 230;
  let car = 14.2;
  for (let m = 0; m <= 36; m++) {
    if (m > 0) {
      const annualGrowth = m <= 12 ? 0.18 : m <= 24 ? 0.22 : 0.25;
      const monthly = annualGrowth / 12;
      assets *= 1 + monthly;
      loans *= 1 + monthly * 1.05;
      govSecs *= 1 + monthly * 0.85;
      cash *= 1 + monthly * 0.6;
      deposits *= 1 + monthly * 0.95;
      wholesale *= 1 + monthly * 1.2;
      equity *= 1 + monthly * 0.6;
      car = 9.5 + 4.5 * Math.exp(-m / 18) + 0.3 * Math.sin(m / 4);
    }
    const yearOffset = Math.floor(m / 12);
    const monthOffset = m % 12;
    const monthLabel = `${['Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec', 'Jan', 'Feb', 'Mar'][monthOffset]} ${(26 + yearOffset + (monthOffset >= 9 ? 1 : 0)) % 100}`;
    points.push({
      month: monthLabel,
      monthIdx: m,
      assets: Number(assets.toFixed(0)),
      loans: Number(loans.toFixed(0)),
      govSecs: Number(govSecs.toFixed(0)),
      cashAndBoG: Number(cash.toFixed(0)),
      liabilities: Number((deposits + wholesale).toFixed(0)),
      deposits: Number(deposits.toFixed(0)),
      wholesaleFunding: Number(wholesale.toFixed(0)),
      equity: Number(equity.toFixed(0)),
      car: Number(car.toFixed(2)),
      ldr: Number(((loans / deposits) * 100).toFixed(1)),
      nim: Number((4.8 + Math.sin(m / 6) * 0.3).toFixed(2)),
    });
  }
  return points;
})();

export const strategicAssumptions = [
  { label: 'Asset growth Y1', value: 18, suffix: '%', planValue: 16, variance: 2 },
  { label: 'Asset growth Y2', value: 22, suffix: '%', planValue: 20, variance: 2 },
  { label: 'Asset growth Y3', value: 25, suffix: '%', planValue: 22, variance: 3 },
  { label: 'Loan-to-deposit target', value: 70, suffix: '%', planValue: 72, variance: -2 },
  { label: 'NIM target', value: 4.8, suffix: '%', planValue: 5.0, variance: -0.2 },
  { label: 'Cost-to-income target', value: 48, suffix: '%', planValue: 50, variance: -2 },
];

export const rlRecommendations = [
  {
    id: 'rl-1',
    title: 'Increase GoG securities allocation from 25% to 32% over next 6 months',
    rationale: 'Current high T-bill yields (25.4% on 91d, 27.0% on 1Y) materially exceed loan portfolio risk-adjusted yield given elevated NPL trajectory. RL agent identified asymmetric upside: GoG securities are zero-RWA at BoG, freeing capital while improving NII. Optimal allocation rebalances maturity ladder to extend duration moderately.',
    expectedImpact: 'NII +GHS 18.4M annualized. CAR uplift +0.6 pts via RWA reduction. NSFR neutral (Level 1 reclassification). Drawdown risk: yield curve steepener could reduce MTM if 5Y rates fall >150bps; partially hedged via existing IRS book.',
    confidence: 0.83,
  },
  {
    id: 'rl-2',
    title: 'Shift wholesale funding mix: extend 30% of <6M maturity to 12-18M tenor',
    rationale: 'Current wholesale book is concentrated in <6M maturity (62% of GHS 270M). NSFR sensitivity to rollover risk is high. Extending tenor reduces RSF/ASF imbalance and stabilizes funding cost under stressed scenarios. RL agent quantifies term premium cost vs liquidity risk reduction.',
    expectedImpact: 'NSFR uplift +4.2 pts (118% → 122%). Funding cost +35bps annually on extended portion (~GHS 285K). LCR neutral. Improves stress test combined-scenario survival horizon by 12 days.',
    confidence: 0.78,
  },
  {
    id: 'rl-3',
    title: 'Rebalance branch deposit pricing: lift NMD rates in Tier-3 branches by 50bps',
    rationale: 'RL agent identifies sub-optimal deposit pricing across 6 underperforming branches (B-013 to B-018). Modest rate increase recovers customer churn risk and re-anchors the branch P&L within target NIM band. Optimal pricing varies by region and product mix; agent recommends granular adjustments.',
    expectedImpact: 'Branch deposit base recovery +GHS 18M. Net NIM -8bps in those branches; offset by GHS 6M additional NII via volume. Branch ranking expected to recover within 6 months.',
    confidence: 0.71,
  },
  {
    id: 'rl-4',
    title: 'Activate FX forward overlay: hedge 40% of USD long position via 6M forwards',
    rationale: 'Macro signal from FX module ensemble: GHS depreciation 90d projected +3.5% vs forward implied +1.7%. Current hedge ratio 74% leaves residual exposure. Agent recommends incremental hedging at favorable forward levels before BoG mid-April policy meeting.',
    expectedImpact: 'NOP/capital reduction 4.78% → 3.92%. Hedge cost ~GHS 480K over 6 months. Caps tail risk on 20% cedi depreciation scenario by GHS 2.8M.',
    confidence: 0.69,
  },
];

export type WhatIfScenario = {
  id: string;
  name: string;
  description: string;
  assets36M: number;
  car36M: number;
  nim36M: number;
  npl36M: number;
  severity: 'success' | 'amber' | 'critical';
};

export const whatIfScenarios: WhatIfScenario[] = [
  {
    id: 'baseline',
    name: 'Baseline',
    description: 'Strategic plan assumptions held; no macro shock; BoG MPR steady.',
    assets36M: 4280,
    car36M: 14.8,
    nim36M: 4.8,
    npl36M: 4.4,
    severity: 'success',
  },
  {
    id: 'rate-shock',
    name: 'Rate shock — BoG MPR +400bps',
    description: 'Sustained policy tightening; deposit costs rise faster than loan repricing in early periods.',
    assets36M: 3960,
    car36M: 13.2,
    nim36M: 3.9,
    npl36M: 6.8,
    severity: 'amber',
  },
  {
    id: 'cedi-depreciation',
    name: 'Cedi depreciation 20%',
    description: 'GHS/USD moves to 15.00; FX-denominated loans show RWA inflation; hedge book gains partially offset.',
    assets36M: 4180,
    car36M: 12.4,
    nim36M: 4.5,
    npl36M: 5.9,
    severity: 'amber',
  },
  {
    id: 'default-spike',
    name: 'NPL spike to 12%',
    description: 'Sectoral concentration risk in cocoa and oil services materializes; provisioning increases.',
    assets36M: 3760,
    car36M: 10.8,
    nim36M: 4.1,
    npl36M: 12.0,
    severity: 'critical',
  },
  {
    id: 'mpr-cut',
    name: 'BoG MPR −200bps',
    description: 'Easing cycle begins early; loan repricing lags deposits; reinvestment risk on T-bill book.',
    assets36M: 4360,
    car36M: 14.4,
    nim36M: 4.4,
    npl36M: 4.0,
    severity: 'success',
  },
  {
    id: 'combined',
    name: 'Combined — BoG severe',
    description: 'Concurrent rate shock, cedi depreciation, and NPL spike per BoG ICAAP severe.',
    assets36M: 3520,
    car36M: 8.4,
    nim36M: 3.6,
    npl36M: 14.5,
    severity: 'critical',
  },
];
