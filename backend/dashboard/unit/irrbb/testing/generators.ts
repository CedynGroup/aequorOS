import fc from "fast-check";

/** Source: tests/domain/irr/test_sf_fixtures.py governed shock calibration;
 * standardised.py loss-positive convention and six-place reporting quantum.
 * These are synthetic stored single-currency observations, not a second SF engine.
 * Threshold source: 202609190056_icaap_pillar2_parameters.py, pending exposure draft.
 */
export const irrbbSnapshots = fc
  .tuple(
    fc.constantFrom("GHS", "USD", "EUR"),
    fc.integer({ min: 1_000_000, max: 50_000_000 }),
    fc.oneof(
      fc.constantFrom(14_999_999, 15_000_000, 15_000_001),
      fc.integer({ min: 10_000_000, max: 20_000_000 }),
    ),
  )
  .map(([currency, capital, ratioMicros]) => {
    const ratio = ratioMicros / 1_000_000;
    const loss = Number(((capital * ratio) / 100).toFixed(6));
    const codes = [
      "parallel_up",
      "parallel_down",
      "steepener",
      "flattener",
      "short_up",
      "short_down",
    ];
    const scenarios = codes.map((code, index) => {
      const delta = index === 0 ? loss : index % 2 ? -loss / 2 : loss / 2;
      return {
        code,
        label: `Synthetic ${code}`,
        mandatory: index < 2,
        inOutlierSet: index < 2,
        byCurrency: [
          {
            currency,
            eveBaseNative: capital.toFixed(6),
            eveScenarioNative: (capital - delta).toFixed(6),
            deltaEveNative: delta.toFixed(6),
            deltaEveReporting: delta.toFixed(6),
            automaticOptionAddonNative: "0.000000",
            deltaNiiNative: "0.000000",
            deltaNiiReporting: "0.000000",
          },
        ],
        loss: Math.max(delta, 0).toFixed(6),
        net: delta.toFixed(6),
        deltaNii: "0.000000",
      };
    });
    const measure = {
      name: "synthetic",
      label: "Synthetic measure",
      scenarios: codes,
      measure: loss.toFixed(6),
      worstScenario: codes[0],
      worstScenarioLabel: scenarios[0].label,
    };
    return {
      tier1: capital.toFixed(6),
      pctTier1: ratio.toFixed(6),
      outlierThresholdPct: "15.000000",
      outlier: ratio > 15,
      scenarios,
      measures: {
        allScenarios: measure,
        mandatory: {
          ...measure,
          scenarios: scenarios
            .filter((row) => row.mandatory)
            .map((row) => row.code),
        },
        outlierSet: {
          ...measure,
          scenarios: scenarios
            .filter((row) => row.inOutlierSet)
            .map((row) => row.code),
        },
      },
    };
  });
