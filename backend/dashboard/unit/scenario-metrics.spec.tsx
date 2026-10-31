// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import fc from "fast-check";
import ScenarioMetrics, {
  scenarioDelta,
  scenarioRisk,
  scenarioValue,
} from "../features/workbench/components/ScenarioMetrics";
import { currencyCode, fmtNum } from "../lib/format";

afterEach(cleanup);

it("formats money, percentage points and decimal zeros without scientific notation", () => {
  render(
    <ScenarioMetrics
      metrics={{ total_ghs: "1234567.125", lcr_pct: "98.125", reserve: "0E-8" }}
      baseline={{ total_ghs: "1000000", lcr_pct: "100", reserve: "0" }}
      statuses={{ lcr_pct: "red" }}
    />,
  );
  const table = screen.getByRole("table", { name: "Scenario metrics" });
  expect(
    within(table).getByText(`${currencyCode()} ${fmtNum(1234567.125, 2)}`),
  ).toBeVisible();
  expect(within(table).getByText("98.13%")).toBeVisible();
  expect(within(table).getByText("-1.88 pp")).toBeVisible();
  expect(within(table).getByText(/Policy breach/)).toBeVisible();
  expect(within(table).queryByText(/0E/)).not.toBeInTheDocument();
});

it("preserves absence rather than inventing a zero or delta", () => {
  fc.assert(
    fc.property(fc.constantFrom(null, undefined, ""), (missing) => {
      expect(scenarioValue("lcr_pct", missing)).toBe("Unavailable");
      expect(scenarioDelta("lcr_pct", missing, "100")).toBe("Not comparable");
      expect(scenarioDelta("lcr_pct", "100", missing)).toBe("Not comparable");
    }),
  );
});

it("keeps a percentage-point delta unchanged when both scenarios move by the same amount", () => {
  fc.assert(
    fc.property(
      fc.integer({ min: -10000, max: 10000 }),
      fc.integer({ min: -10000, max: 10000 }),
      fc.integer({ min: -1000, max: 1000 }),
      (a, b, shift) => {
        expect(scenarioDelta("nim_pct", a + shift, b + shift)).toBe(
          scenarioDelta("nim_pct", a, b),
        );
        expect(scenarioDelta("nim_pct", a, a)).toBe("0.00 pp");
      },
    ),
  );
});

it("separates successful execution from reported breaches and absent assessment", () => {
  expect(scenarioRisk({ within_single_limit: false })).toContain(
    "Risk breaches:",
  );
  expect(scenarioRisk({ within_single_limit: true })).toContain(
    "No reported breaches",
  );
  expect(scenarioRisk({ nop_ghs: "100" })).toContain(
    "Risk assessment unavailable",
  );
  expect(
    scenarioRisk({ triggers: [{ fired: true, action: "Convene ALCO" }] }),
  ).toContain("Convene ALCO");
});

it("shows refusals and policy warnings beside affected values without inventing a delta", () => {
  render(
    <ScenarioMetrics
      metrics={{ lcr_pct: null, nsfr_pct: null, car_pct: "12", count: 0 }}
      baseline={{ lcr_pct: "100", nsfr_pct: "100", car_pct: "13", count: 0 }}
      refusals={{
        lcr_pct: { reason: "Liquidity source evidence missing" },
        nsfr_pct: { reasonCode: "missing_limit" },
      }}
      statuses={{ car_pct: "amber", count: "na" }}
    />,
  );
  expect(
    screen.getByText("Unavailable: Liquidity source evidence missing"),
  ).toBeVisible();
  expect(screen.getByText("Unavailable: Missing limit")).toBeVisible();
  expect(screen.getAllByText("Not comparable")).toHaveLength(2);
  expect(
    screen.getByText(/Warning. Approaching the configured limit/),
  ).toBeVisible();
});

it("renders currency buckets and nested evidence without a JSON dump", () => {
  render(
    <ScenarioMetrics
      metrics={{
        currency_gaps: [
          {
            currency: "EUR",
            assets: ["0E-8", "1200.5"],
            evidence: { source: "Uploaded positions" },
          },
        ],
        basis: { allowance_basis: "Booked general provisions" },
      }}
    />,
  );
  const table = screen.getByRole("table", { name: "Currency gaps" });
  expect(within(table).getByText("EUR")).toBeVisible();
  expect(
    within(table).getByText(`${currencyCode()} ${fmtNum(1200.5, 2)}`),
  ).toBeVisible();
  expect(within(table).getByText("Uploaded positions")).toBeVisible();
});

it("does not treat non-finite values or nonnumeric comparisons as measured outcomes", () => {
  fc.assert(
    fc.property(
      fc.constantFrom(
        Number.NaN,
        Number.POSITIVE_INFINITY,
        Number.NEGATIVE_INFINITY,
      ),
      (value) => {
        expect(scenarioValue("car_pct", value)).toBe("Unavailable");
        expect(scenarioDelta("car_pct", value, 10)).toBe("Not comparable");
      },
    ),
  );
  expect(scenarioDelta("margin", "not reported", 0)).toBe("Not comparable");
  expect(scenarioDelta("within_limit", true, false)).toBe("Not comparable");
  expect(scenarioRisk({}, { lcr_pct: "red" })).toContain(
    "Risk breaches: LCR percentage",
  );
  expect(scenarioRisk({}, { lcr_pct: "green" })).toContain(
    "No reported breaches",
  );
  expect(scenarioRisk({ triggers: [{ fired: true }] })).toContain(
    "Capital trigger breached",
  );
});
