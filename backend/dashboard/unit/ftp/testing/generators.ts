import fc from "fast-check";
import type { FtpProductRead } from "@aequoros/risk-service-api";

/** Source: parameter_register.BANK_FTP_THRESHOLDS, bank policy, not BoG minima. */
export const ftpPolicy = {
  roe: 15,
  margin: 0,
  liquidity: 50,
  funding: 200,
  coreMin: 60,
  coreMax: 70,
} as const;
export const ftpCategories = [
  ["Corporate lending", "asset", "corporate_sme"],
  ["Retail mortgage", "asset", "retail_lending"],
  ["Treasury bills", "asset", "treasury"],
  ["Special facility", "asset", "other_assets"],
  ["Savings account", "liability", "transactional_deposits"],
  ["Wholesale borrowing", "liability", "term_wholesale"],
  ["Special facility", "liability", "other_funding"],
] as const;

/** Source: domain/ftp/engine.py ProductProfit. Synthetic whole-unit 100k–10m
 * balances and 20–30% curve rates surround the hand-built test_ftp_engine fixture.
 * Contribution carries 4dp, margin 6dp; near-floor values are deliberate edges.
 */
export const ftpProducts = fc.array(
  fc
    .tuple(
      fc.integer({ min: 0, max: ftpCategories.length - 1 }),
      fc.integer({ min: 100_000, max: 10_000_000 }),
      fc.oneof(
        fc.constantFrom(-1, 0, 1),
        fc.integer({ min: -2_000_000, max: 5_000_000 }),
      ),
      fc.integer({ min: 2000, max: 3000 }),
    )
    .map(([group, balance, marginMicros, curveHundredths]): FtpProductRead => {
      const [product, category] = ftpCategories[group];
      const margin = marginMicros / 1_000_000;
      const ftp = curveHundredths / 100;
      return {
        product,
        category,
        balanceGhs: balance.toFixed(4),
        tenorYears: "1",
        customerRatePct: (category === "asset"
          ? ftp + 0.3 + margin
          : ftp - 0.3 - margin
        ).toFixed(6),
        ftpRatePct: ftp.toFixed(6),
        operatingCostPct: "0.300000",
        expectedCreditLossPct: "0",
        capitalChargePct: "0",
        netMarginPct: margin.toFixed(6),
        contributionGhs: ((balance * margin) / 100).toFixed(4),
        belowMinMargin: margin < ftpPolicy.margin,
      };
    }),
  { minLength: 1, maxLength: 25 },
);

/** Independent stored figures deliberately challenge the view's authority.
 * Source: components/ftp/businessLines.ts sums saved figures, never reprices.
 */
export const adversarialFtpRows = fc.array(
  fc.record({
    group: fc.integer({ min: 0, max: ftpCategories.length - 1 }),
    balance: fc.integer({ min: 1, max: 10_000_000 }),
    contribution: fc.integer({ min: -1_000_000, max: 1_000_000 }),
    flagged: fc.boolean(),
    uppercase: fc.boolean(),
  }),
  { minLength: 1, maxLength: 25 },
);

/** Decoder contract accepts independent saved policy values, including negatives. */
export const adversarialFtpParameters = fc.array(
  fc.integer({ min: -100_000, max: 100_000 }),
  { minLength: 8, maxLength: 8 },
);
/** Source: BANK_FTP_THRESHOLDS plus tests/domain/test_ftp_engine.py stress 200bp.
 * Core band endpoints ±1ppm and premium caps ±1bp are valid policy test inputs.
 */
export const ftpParameters = fc.tuple(
  fc.constant(15),
  fc.constant(0),
  fc.constantFrom(49, 50, 51),
  fc.constantFrom(199, 200, 201),
  fc.constantFrom(59.999999, 60, 60.000001),
  fc.constantFrom(69.999999, 70, 70.000001),
  fc.constantFrom(0, 200),
  fc.constantFrom(0, 50),
);
