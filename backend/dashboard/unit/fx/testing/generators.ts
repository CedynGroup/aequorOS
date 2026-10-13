import fc from "fast-check";

/** Limits: backend parameter_register.BANK_FX_THRESHOLDS. Decoder amounts use
 * engine MONEY=0.0001; integer cents are a coarser exactly stored synthetic unit.
 */
export const fxLimits = { single: 10, aggregate: 20 } as const;
export const fxCurrency = fc.constantFrom("USD", "EUR", "GBP");
const cents = fc.oneof(
  fc.constant(0),
  fc.integer({ min: 1_000_000, max: 1_000_000_000 }),
);
export const fxPositions = fc.uniqueArray(
  fc
    .tuple(
      fxCurrency,
      cents,
      cents,
      fc.integer({ min: -100_000, max: 100_000 }),
    )
    .map(([currency, assets, liabilities, derivatives]) => ({
      currency,
      assets,
      liabilities,
      derivatives,
      net: assets - liabilities + derivatives,
    })),
  { selector: (row) => row.currency, minLength: 1, maxLength: 3 },
);

/** Deliberately independent signed fields prove the saved net is not derived.
 * Source: components/fx/params.ts FxPositionSplit reads stored figures directly.
 */
const signedCents = fc.integer({ min: -100_000_000, max: 100_000_000 });
export const adversarialFxPositions = fc.uniqueArray(
  fc.tuple(
    fc.constantFrom("EUR", "JPY", "GBP", "USD", "CHF"),
    signedCents,
    signedCents,
    signedCents,
    signedCents,
    fc.boolean(),
  ),
  { selector: (row) => row[0], minLength: 1, maxLength: 5 },
);

/** Synthetic scenario observations near the source aggregate limit; hundredths
 * match ScenarioStrip's 2dp ratio and tenths its 1dp shock display.
 */
export const fxScenarios = fc
  .tuple(
    fc.oneof(
      fc.constantFrom(1999, 2000, 2001),
      fc.integer({ min: 1500, max: 2500 }),
    ),
    fc.integer({ min: 0, max: 300 }),
  )
  .map(
    ([hundredths, tenths]) =>
      [
        hundredths,
        tenths,
        fxLimits.aggregate,
        hundredths <= fxLimits.aggregate * 100,
      ] as const,
  );

/** Explicit stored-verdict and signed-value adversaries for display fidelity. */
export const adversarialFxScenarios = fc.tuple(
  fc.integer({ min: -20_000, max: 20_000 }),
  fc.integer({ min: 0, max: 1000 }),
  fc.integer({ min: 1, max: 100 }),
  fc.boolean(),
);

/** Snapshot source: params.ts; hedge bands from BANK_FX_THRESHOLDS, shocks from
 * test_fx_engine.py's synthetic scenario table. Crisis indices are synthetic.
 */
export const fxParameterSnapshots = fc.tuple(
  fc.integer({ min: 0, max: 250 }),
  fc.integer({ min: 1, max: 250 }),
  fc.constantFrom(0, 25, 30),
  fc.constant(80),
  fc.constant(80),
  fc.constant(125),
  fc.constantFrom(0, 10, 20, 30),
  fc.boolean(),
);

/** Deliberate alternate saved register profiles, including appreciation shocks.
 * Source: params.ts preserves independent stored parameters; no regulatory
 * authority is claimed for these synthetic institution-specific values.
 */
export const alternateFxParameterSnapshots = fc.tuple(
  fc.integer({ min: 0, max: 500 }),
  fc.integer({ min: 1, max: 500 }),
  fc.integer({ min: 0, max: 100 }),
  fc.integer({ min: 0, max: 100 }),
  fc.integer({ min: 0, max: 100 }),
  fc.integer({ min: 101, max: 200 }),
  fc.integer({ min: -100, max: 100 }),
  fc.boolean(),
);
