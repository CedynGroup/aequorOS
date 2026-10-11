// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import fc from "fast-check";
import type {
  CapitalFloorRead,
  CapitalPlanProjectionScenario,
  CapitalPlanProjectionYear,
  CapitalPlanSummaryReadProjection,
} from "@aequoros/risk-service-api";
import CapitalPlanProjection, {
  CapitalPlanProjectionUnavailable,
} from "../components/basel/CapitalPlanProjection";

afterEach(cleanup);

const minimum: CapitalFloorRead = {
  paramCode: "car_min",
  source: "control_plane",
  valuePct: "13.125",
  regulatoryValuePct: "13.125",
  conservationBuffer: "included",
  sourceCitation: "Fixture capital instrument, paragraph 71",
};

function scenario(
  code: string,
  values: Partial<CapitalPlanProjectionYear> = {},
): CapitalPlanProjectionScenario {
  return {
    scenarioCode: code,
    runId: `fixture-${code}`,
    inputHash: `stored-${code}`,
    years: [
      {
        year: 1,
        periodLabel: "FY 2027",
        carPct: "15.25",
        headroomPp: "2.125",
        ...values,
      },
    ],
  };
}

function projection(
  overrides: Partial<CapitalPlanSummaryReadProjection> = {},
): CapitalPlanSummaryReadProjection {
  return {
    asOfDate: new Date("2026-12-31T00:00:00Z"),
    yearEndAligned: true,
    baselRatiosApplicable: true,
    pillar1Min: minimum,
    pillar1MinPct: "13.125",
    pillar2AddonPct: "1.125",
    totalRequirementPct: "14.25",
    scenarios: [scenario("base")],
    ...overrides,
  };
}

function yearCells() {
  const table = screen.getByRole("table");
  return within(within(table).getAllByRole("row")[1]).getAllByRole("cell");
}

describe("capital plan projection behavior", () => {
  it("distinguishes unavailable ratios and headroom from real zero and deficits", () => {
    render(
      <CapitalPlanProjection
        projection={projection({
          scenarios: [
            scenario("base", {
              carPct: "0",
              headroomPp: "0",
              tier1Pct: null,
              tier1HeadroomPp: null,
              cet1Pct: "6.125",
              cet1HeadroomPp: "-0.375",
            }),
          ],
        })}
      />,
    );
    expect(yearCells().map((cell) => cell.textContent)).toEqual([
      "FY 2027",
      "0.00%",
      "0.00 pp",
      "Not projected",
      "—",
      "6.13%",
      "−0.38 pp",
    ]);
    expect(screen.getAllByText("Not configured")).toHaveLength(2);
    expect(
      screen.getByText(/buffer compliance is not assessed here/),
    ).toBeVisible();
  });

  it("preserves independently stored ratios and percentage-point headroom across signed decimals", () => {
    const hundredths = fc.integer({ min: -20_000, max: 20_000 });
    fc.assert(
      fc.property(
        hundredths,
        hundredths,
        hundredths,
        hundredths,
        hundredths,
        hundredths,
        (car, headroom, tier1, tier1Headroom, cet1, cet1Headroom) => {
          const values = [
            car,
            headroom,
            tier1,
            tier1Headroom,
            cet1,
            cet1Headroom,
          ];
          render(
            <CapitalPlanProjection
              projection={projection({
                scenarios: [
                  scenario("base", {
                    carPct: String(car / 100),
                    headroomPp: String(headroom / 100),
                    tier1Pct: String(tier1 / 100),
                    tier1HeadroomPp: String(tier1Headroom / 100),
                    cet1Pct: String(cet1 / 100),
                    cet1HeadroomPp: String(cet1Headroom / 100),
                  }),
                ],
              })}
            />,
          );
          try {
            const displayed = yearCells()
              .slice(1)
              .map((cell) => cell.textContent ?? "");
            // Independent round-trip oracle: displayed units and stored values survive formatting.
            displayed.forEach((text, index) => {
              const point = index % 2 === 1;
              expect(text.endsWith(point ? " pp" : "%")).toBe(true);
              expect(
                Number(text.replace("−", "-").replace(point ? " pp" : "%", "")),
              ).toBe(values[index] / 100);
            });
          } finally {
            cleanup();
          }
        },
      ),
      { numRuns: 100 },
    );
  });

  it("never invents a ratio or headroom when stored measurements are missing or non-finite", () => {
    fc.assert(
      fc.property(
        fc.constantFrom(null, undefined, "NaN", "Infinity", "not measured"),
        (missing) => {
          render(
            <CapitalPlanProjection
              projection={projection({
                scenarios: [
                  scenario("base", {
                    carPct: missing,
                    headroomPp: missing,
                    tier1Pct: missing,
                    tier1HeadroomPp: missing,
                    cet1Pct: missing,
                    cet1HeadroomPp: missing,
                  }),
                ],
              })}
            />,
          );
          try {
            expect(yearCells().map((cell) => cell.textContent)).toEqual([
              "FY 2027",
              "Not projected",
              "—",
              "Not projected",
              "—",
              "Not projected",
              "—",
            ]);
          } finally {
            cleanup();
          }
        },
      ),
      { numRuns: 100 },
    );
  });

  it("selects the stored scenario and falls back when refreshed data removes that choice", async () => {
    const user = userEvent.setup();
    const { rerender } = render(
      <CapitalPlanProjection
        projection={projection({
          scenarios: [
            scenario("base", { carPct: "17.25" }),
            scenario("adverse", { carPct: "8.5", headroomPp: "-5.75" }),
          ],
        })}
      />,
    );
    expect(screen.getByRole("cell", { name: "17.25%" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Adverse scenario" }));
    expect(screen.getByRole("cell", { name: "8.50%" })).toBeVisible();
    expect(screen.getByRole("cell", { name: "−5.75 pp" })).toBeVisible();
    expect(
      screen.queryByRole("cell", { name: "17.25%" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(/stored Adverse scenario forecast run/),
    ).toBeVisible();
    rerender(
      <CapitalPlanProjection
        projection={projection({
          scenarios: [scenario("custom", { carPct: "19" })],
        })}
      />,
    );
    expect(screen.getByRole("cell", { name: "19.00%" })).toBeVisible();
    expect(
      screen.getByText(/stored Custom scenario forecast run/),
    ).toBeVisible();
  });

  it("uses the institution regime while keeping unconfigured bank sub-tier ratios visible", () => {
    const { rerender } = render(
      <CapitalPlanProjection
        projection={projection({
          baselRatiosApplicable: false,
          scenarios: [scenario("base", { tier1Pct: "9", cet1Pct: "7" })],
        })}
      />,
    );
    expect(
      screen.queryByRole("columnheader", { name: "Tier 1" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("columnheader", { name: "CET1" }),
    ).not.toBeInTheDocument();
    expect(yearCells()).toHaveLength(3);
    rerender(
      <CapitalPlanProjection
        projection={projection({
          baselRatiosApplicable: undefined,
          scenarios: [scenario("base", { tier1Pct: "9", cet1Pct: "7" })],
        })}
      />,
    );
    expect(screen.getByRole("columnheader", { name: "Tier 1" })).toBeVisible();
    expect(screen.getByRole("columnheader", { name: "CET1" })).toBeVisible();
    expect(screen.getByRole("cell", { name: "9.00%" })).toBeVisible();
    expect(screen.getAllByText("Not configured")).toHaveLength(2);
  });

  it("discloses governed floors, their source and provisional confirmation", () => {
    render(
      <CapitalPlanProjection
        projection={projection({
          pillar1Min: {
            ...minimum,
            raisedToRegulatoryFloor: true,
            boardRegisterPct: "10",
            confirmationStatus: "pending",
            effectiveFrom: "1999-01-01",
          },
          tier1Min: {
            ...minimum,
            paramCode: "tier1_min",
            valuePct: "9.375",
            source: "board_register",
            regulatoryValuePct: "8",
            conservationBuffer: "excluded",
          },
          cet1Min: {
            ...minimum,
            paramCode: "cet1_min",
            valuePct: "7.125",
            source: "board_register",
            regulatoryValuePct: null,
            conservationBuffer: undefined,
            confirmationStatus: "confirmed",
          },
        })}
      />,
    );
    expect(screen.getByText("14.25%")).toBeVisible();
    expect(
      screen.getByText(/Pillar 1 minimum 13.13% \+ Pillar 2 add-ons 1.13%/),
    ).toBeVisible();
    expect(
      screen.getByText(/register value of 10% is below it and is raised to it/),
    ).toHaveTextContent(
      /includes the capital conservation buffer.*Fixture capital instrument.*pending confirmation/,
    );
    expect(
      screen.getByText(/stricter than the regulatory 8%/),
    ).toHaveTextContent(/9.38%.*excludes the capital conservation buffer/);
    expect(
      screen.getByText(/no regulatory value is configured for it/),
    ).toHaveTextContent("7.13%");
    expect(screen.getByText("Pending confirmation")).toBeVisible();
    expect(
      screen.getByText(/as of 31 Dec 2026 \(financial year-end\)/),
    ).toBeVisible();
    expect(screen.queryByText(/1999/)).not.toBeInTheDocument();
  });

  it("explains unavailable projections and empty scenario collections without fabricating a table", () => {
    const { unmount } = render(
      <CapitalPlanProjection projection={projection({ scenarios: [] })} />,
    );
    expect(
      screen.getByText("No forecast scenario is available to project."),
    ).toBeVisible();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    unmount();
    render(
      <CapitalPlanProjectionUnavailable
        unavailable={{
          errorCode: "missing_parameter",
          reason:
            "No governed capital minimum is configured for this institution.",
        }}
      />,
    );
    expect(
      screen.getByText(
        "No governed capital minimum is configured for this institution.",
      ),
    ).toBeVisible();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});
