/**
 * The canonical fixture book's balance-sheet forecast, derived independently
 * for the Forecasting journeys.
 *
 * Every balance is the latest canonical period of
 * `tests/fixtures/canonical_bank_fixture.py` (`_FIXED_ASSETS_M`, `_DEPOSITS_M`,
 * `_LOAN_EXPOSURES_M`, `_SECURITIES_M`, `_OFF_BALANCE_M`, `_MARKET_RISK_M`,
 * `_OPERATIONAL_INCOME_M`, `_CAPITAL_COMPONENTS_M`), carried forward unchanged
 * to the latest month end. Every preset is the fixture's approved `forecast`
 * stress-shock register. The projection below is the documented yearly
 * recurrence of `app/domain/forecasting/engine.py` (growth, the NII/fee/opex/
 * credit-loss/tax/dividend chain, the funding plug, retained earnings into
 * CET1), and CAR is the Basel build-up the Basel journey derives: credit RWA at
 * standardised weights with commitments at a 50% CCF, the FX open position at
 * an 8% charge × 12.5, and the Basic Indicator Approach over the three most
 * recent gross-income years. Amounts are GHS, rounded to 4 dp at the points
 * the engine rounds, so the figures match its output to the cent.
 *
 * LCR and NSFR paths are NOT derived here: a journey pins them as engine
 * output and proves they are the same engine output on every surface.
 */

const M = 1_000_000;

/** Every forecast assumption, in the API's percent / percentage-point units. */
export type Assumptions = {
  loanGrowthPct: number;
  depositGrowthPct: number;
  nimPct: number;
  costToIncomePct: number;
  creditLossRatePct: number;
  fxDepreciationPct: number;
  dividendPayoutPct: number;
  feeIncomePctAssets: number;
  taxRatePct: number;
  securitiesShiftPp: number;
};

/** The engine defaults for the three fields no fixture preset sets. */
const ENGINE_DEFAULTS = {
  feeIncomePctAssets: 1.2,
  taxRatePct: 25,
  securitiesShiftPp: 0,
};

/** The fixture's approved `base` forecast preset. */
export const BASE: Assumptions = {
  loanGrowthPct: 18,
  depositGrowthPct: 16,
  nimPct: 4.8,
  costToIncomePct: 48,
  creditLossRatePct: 1.0,
  fxDepreciationPct: 0,
  dividendPayoutPct: 30,
  ...ENGINE_DEFAULTS,
};

/** The fixture's approved `adverse` forecast preset. */
export const ADVERSE: Assumptions = {
  loanGrowthPct: 8,
  depositGrowthPct: 6,
  nimPct: 4.2,
  costToIncomePct: 54,
  creditLossRatePct: 1.5,
  fxDepreciationPct: 15,
  dividendPayoutPct: 0,
  ...ENGINE_DEFAULTS,
};

/** Loan exposures: GHS millions at their standardised risk weight. */
const LOANS = [
  { amount: 560, rwPct: 100 },
  { amount: 280, rwPct: 75 },
  { amount: 250, rwPct: 75 },
  { amount: 200, rwPct: 35 },
  { amount: 60, rwPct: 100 },
  { amount: 50, rwPct: 150 },
];
/** Undrawn commitments: they grow with the loan book; EAD at a 50% CCF. */
const COMMITMENTS = [
  { amount: 80, rwPct: 75 },
  { amount: 240, rwPct: 100 },
];
const CCF_PCT = 50;
/** BoG bills and GoG bonds (0% risk weight). */
const SECURITIES = [260, 360];
/** Vault cash, required reserves, excess reserves (the funding-plug row). */
const CASH = { vault: 45, required: 175, excess: 70 };
/** Other assets: constant, 100% risk weight. */
const OTHER_ASSETS = 90;
const DEPOSITS = [700, 440, 240, 200, 320];
const SECURED_FUNDING = 60;
/** Net long / net short FX positions; the open position is the larger. */
const FX_POSITIONS = [45, 12];
/** Gross income 2023–2025, the BIA evidence the projection rolls forward. */
const GROSS_INCOME = [340, 380, 400];
const CET1 = 150 + 95 + 45 + 10 - 25 - 15;
const AT1 = 20;
/** Subordinated debt plus general provisions, inside the 1.25% credit-RWA cap. */
const TIER2 = 45 + 15;
/** `capital_total`: the balance-sheet equity the projection grows. */
const EQUITY = CET1 + AT1 + TIER2;

/** The governed CAR minimum every surface judges the projection against. */
export const CAR_MIN_PCT = 13;

export type ProjectionYear = {
  year: number;
  totalAssets: number;
  loans: number;
  securities: number;
  cash: number;
  deposits: number;
  equity: number;
  nii: number;
  fees: number;
  totalIncome: number;
  opex: number;
  creditLosses: number;
  netIncome: number;
  dividends: number;
  roePct: number | null;
  carPct: number;
};

export type Projection = {
  years: ProjectionYear[];
  summary: {
    avgRoePct: number;
    finalCarPct: number;
    minCarPct: number;
    cumulativeNetIncome: number;
  };
};

/**
 * Project the fixture book `years` years forward under `a`. A what-if MTM
 * haircut marks the securities down once in year 1, after growth.
 */
export function project(
  a: Assumptions,
  { years = 5, securitiesHaircutPct = 0 } = {},
): Projection {
  const loanFactor = 1 + a.loanGrowthPct / 100;
  const depositFactor = 1 + a.depositGrowthPct / 100;
  const securitiesFactor = 1 + (a.depositGrowthPct + a.securitiesShiftPp) / 100;

  let loans = LOANS.map((l) => l.amount * M);
  let commitments = COMMITMENTS.map((c) => c.amount * M);
  let securities = SECURITIES.map((s) => s * M);
  let cash = [CASH.vault, CASH.required, CASH.excess].map((c) => c * M);
  let deposits = DEPOSITS.map((d) => d * M);
  let fx = FX_POSITIONS.map((p) => p * M);
  let equity = EQUITY * M;
  let retained = 0;
  const grossIncome = GROSS_INCOME.map((g) => g * M);

  const assets = () =>
    sum(cash) + sum(securities) + sum(loans) + OTHER_ASSETS * M;
  const car = () => {
    const credit = money(
      sum(loans.map((l, i) => (l * LOANS[i].rwPct) / 100)) +
        OTHER_ASSETS * M +
        sum(
          commitments.map(
            (c, i) => (((c * CCF_PCT) / 100) * COMMITMENTS[i].rwPct) / 100,
          ),
        ),
    );
    const market = money(Math.max(...fx) * 0.08 * 12.5);
    const window = grossIncome.slice(-3).filter((g) => g > 0);
    const operational = money(
      money((sum(window) * 15) / (100 * window.length)) * 12.5,
    );
    const capital = (CET1 + AT1 + TIER2) * M + retained;
    return ratioPct((capital / (credit + market + operational)) * 100);
  };

  const rows: ProjectionYear[] = [
    {
      year: 0,
      totalAssets: assets(),
      loans: sum(loans),
      securities: sum(securities),
      cash: sum(cash),
      deposits: sum(deposits),
      equity,
      nii: 0,
      fees: 0,
      totalIncome: 0,
      opex: 0,
      creditLosses: 0,
      netIncome: 0,
      dividends: 0,
      roePct: null,
      carPct: car(),
    },
  ];
  let earningPrev = sum(loans) + sum(securities);
  let assetsPrev = assets();
  let equityPrev = equity;

  for (let year = 1; year <= years; year += 1) {
    loans = scale(loans, loanFactor);
    commitments = scale(commitments, loanFactor);
    deposits = scale(deposits, depositFactor);
    securities = scale(securities, securitiesFactor);
    cash = scale(cash, depositFactor);
    if (year === 1) {
      if (securitiesHaircutPct !== 0) {
        securities = scale(securities, (100 - securitiesHaircutPct) / 100);
      }
      if (a.fxDepreciationPct !== 0) {
        fx = scale(fx, 1 + a.fxDepreciationPct / 100);
      }
    }

    const earning = sum(loans) + sum(securities);
    const nii = money(((a.nimPct / 100) * (earningPrev + earning)) / 2);
    const fees = money(
      ((a.feeIncomePctAssets / 100) * (assetsPrev + assets())) / 2,
    );
    const totalIncome = nii + fees;
    const opex = money((a.costToIncomePct / 100) * totalIncome);
    const creditLosses = money((a.creditLossRatePct / 100) * sum(loans));
    const preTax = totalIncome - opex - creditLosses;
    const netIncome =
      preTax - money((a.taxRatePct / 100) * Math.max(preTax, 0));
    const dividends = money(
      (a.dividendPayoutPct / 100) * Math.max(netIncome, 0),
    );
    equity += netIncome - dividends;
    retained += netIncome - dividends;

    // Funding plug: borrowings absorb a shortfall; a surplus lands in excess
    // reserves, so assets always equal liabilities plus equity.
    const surplus = sum(deposits) + SECURED_FUNDING * M + equity - assets();
    if (surplus > 0) cash[2] += surplus;
    grossIncome.push(totalIncome);
    grossIncome.shift();

    rows.push({
      year,
      totalAssets: assets(),
      loans: sum(loans),
      securities: sum(securities),
      cash: sum(cash),
      deposits: sum(deposits),
      equity,
      nii,
      fees,
      totalIncome,
      opex,
      creditLosses,
      netIncome,
      dividends,
      roePct: ratioPct((netIncome / ((equityPrev + equity) / 2)) * 100),
      carPct: car(),
    });
    earningPrev = earning;
    assetsPrev = assets();
    equityPrev = equity;
  }

  const projected = rows.slice(1);
  return {
    years: rows,
    summary: {
      avgRoePct: ratioPct(
        sum(projected.map((r) => r.roePct ?? 0)) / projected.length,
      ),
      finalCarPct: projected[projected.length - 1].carPct,
      minCarPct: Math.min(...projected.map((r) => r.carPct)),
      cumulativeNetIncome: sum(projected.map((r) => r.netIncome)),
    },
  };
}

/** One optimizer decision and the base assumptions it overlays. */
export type Decision = {
  loanGrowthPct: number;
  securitiesShiftPp: number;
  depositPremiumBps: number;
  dividendPayoutPct: number;
};

/** Deposit premium (bps) → (deposit growth Δ pp, NIM Δ pp). */
const PREMIUM_EFFECTS: Record<number, [number, number]> = {
  0: [0, 0],
  50: [2, -0.1],
  100: [4, -0.2],
};

/** The optimizer's 4 × 3 × 3 × 3 decision grid, in the engine's order. */
export function optimizerGrid(): Decision[] {
  const grid: Decision[] = [];
  for (const loanGrowthPct of [8, 12, 16, 20])
    for (const securitiesShiftPp of [-5, 0, 5])
      for (const depositPremiumBps of [0, 50, 100])
        for (const dividendPayoutPct of [0, 30, 50])
          grid.push({
            loanGrowthPct,
            securitiesShiftPp,
            depositPremiumBps,
            dividendPayoutPct,
          });
  return grid;
}

/** The base assumptions with one optimizer decision applied. */
export function withDecision(base: Assumptions, d: Decision): Assumptions {
  const [depositDelta, nimDelta] = PREMIUM_EFFECTS[d.depositPremiumBps];
  return {
    ...base,
    loanGrowthPct: d.loanGrowthPct,
    depositGrowthPct: base.depositGrowthPct + depositDelta,
    nimPct: base.nimPct + nimDelta,
    dividendPayoutPct: d.dividendPayoutPct,
    securitiesShiftPp: d.securitiesShiftPp,
  };
}

/** The resolved assumptions a run persists, in the API's snake_case keys. */
export function apiAssumptions(a: Assumptions): Record<string, number> {
  return {
    loan_growth_pct: a.loanGrowthPct,
    deposit_growth_pct: a.depositGrowthPct,
    nim_pct: a.nimPct,
    cost_to_income_pct: a.costToIncomePct,
    credit_loss_rate_pct: a.creditLossRatePct,
    fx_depreciation_pct: a.fxDepreciationPct,
    dividend_payout_pct: a.dividendPayoutPct,
    fee_income_pct_assets: a.feeIncomePctAssets,
    tax_rate_pct: a.taxRatePct,
    securities_shift_pp: a.securitiesShiftPp,
  };
}

/** `lib/format.ts::fmtCurrency` for a full GHS amount. */
export function ghs(amount: number): string {
  const abs = Math.abs(amount);
  const scaled =
    abs >= 1e9
      ? `${(amount / 1e9).toFixed(1)}B`
      : abs >= 1e6
        ? `${(amount / 1e6).toFixed(1)}M`
        : abs >= 1e3
          ? `${(amount / 1e3).toFixed(1)}K`
          : String(amount);
  return `GHS ${scaled}`;
}

export function pct(value: number, decimals = 2): string {
  return `${value.toFixed(decimals)}%`;
}

export function signedPct(value: number, decimals = 1): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(decimals)}%`;
}

export function sum(values: number[]): number {
  return values.reduce((total, value) => total + value, 0);
}

function scale(amounts: number[], factor: number): number[] {
  return amounts.map((amount) => money(amount * factor));
}

/** The engine's 4 dp ROUND_HALF_UP money quantum. */
function money(value: number): number {
  return Math.round(value * 1e4) / 1e4;
}

/** The engine's 6 dp ROUND_HALF_UP ratio quantum. */
function ratioPct(value: number): number {
  return Math.round(value * 1e6) / 1e6;
}
