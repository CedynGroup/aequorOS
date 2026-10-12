import fc from "fast-check";

/** engine.py MONEY=0.0001 and compute_currency_gaps: five LMTD horizons.
 * Synthetic GHS-equivalent buckets; assets/liabilities nonnegative, gaps signed.
 */
export const liquidityAmountQuanta = fc.oneof(
  fc.constant(0),
  fc.integer({ min: 1_000_000_000, max: 100_000_000_000 }),
);
export const liquiditySeries = fc.array(liquidityAmountQuanta, {
  minLength: 5,
  maxLength: 5,
});
export const decimalStrings = (values: number[]) =>
  values.map((v) => (v / 10_000).toFixed(4));

export const currencyLadders = fc
  .record({
    currency: fc.constantFrom("GHS", "USD", "EUR"),
    assetQuanta: liquiditySeries,
    liabilityQuanta: liquiditySeries,
  })
  .map(({ currency, assetQuanta, liabilityQuanta }) => {
    const netQuanta = assetQuanta.map((v, i) => v - liabilityQuanta[i]);
    let running = 0;
    const cumulativeQuanta = netQuanta.map((v) => (running += v));
    return {
      currency,
      assets: decimalStrings(assetQuanta),
      liabilities: decimalStrings(liabilityQuanta),
      net: decimalStrings(netQuanta),
      cumulative: decimalStrings(cumulativeQuanta),
      assetsTotal: assetQuanta.reduce((a, b) => a + b, 0) / 10_000,
      liabilitiesTotal: liabilityQuanta.reduce((a, b) => a + b, 0) / 10_000,
    };
  });

/** Deliberately inconsistent persisted figures exercise the decoder contract:
 * ladderData.ts accepts finite signed decimals and must never recompute columns.
 * These are adversarial decoder inputs, separate from realistic currencyLadders.
 */
const persistedSignedAmount = fc.integer({
  min: -100_000_000_000,
  max: 100_000_000_000,
});
const persistedSignedSeries = fc.array(persistedSignedAmount, {
  minLength: 5,
  maxLength: 5,
});
export const adversarialStoredStressFigures = fc
  .record({
    contractual: persistedSignedSeries,
    stressed: persistedSignedSeries,
    net: persistedSignedSeries,
    cumulative: persistedSignedSeries,
    demand: persistedSignedAmount,
    core: persistedSignedAmount,
  })
  .map(({ contractual, stressed, net, cumulative, demand, core }) => ({
    currency: "GBP",
    contractual_liabilities: decimalStrings(contractual),
    stressed_liabilities: decimalStrings(stressed),
    stressed_net: decimalStrings(net),
    stressed_cumulative: decimalStrings(cumulative),
    demand_deposits: (demand / 10_000).toFixed(4),
    stable_core: (core / 10_000).toFixed(4),
  }));
