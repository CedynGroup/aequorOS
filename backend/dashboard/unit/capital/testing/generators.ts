import fc from "fast-check";

/** regulatory_parameters.SEED_PARAMETERS: CAR 13% includes CRD paragraphs 71/75
 * CCB; Tier 1 8%, CET1 6.5% have pending buffer treatment. Synthetic stored UI
 * observations use hundredths so formatting round trips are independently exact.
 */
export const capitalFloorHundredths = {
  car: 1300,
  tier1: 800,
  cet1: 650,
} as const;
export const capitalMeasurements = fc
  .tuple(
    fc.oneof(
      fc.constantFrom(-1, 0, 1, -50, 50),
      fc.integer({ min: -200, max: 500 }),
    ),
    fc.integer({ min: 0, max: 200 }),
    fc.integer({ min: 0, max: 200 }),
  )
  .map(([offset, at1, tier2]) => {
    const car = capitalFloorHundredths.car + offset;
    const tier1 = car - tier2;
    const cet1 = tier1 - at1;
    return [
      car,
      car - capitalFloorHundredths.car,
      tier1,
      tier1 - capitalFloorHundredths.tier1,
      cet1,
      cet1 - capitalFloorHundredths.cet1,
    ];
  });
