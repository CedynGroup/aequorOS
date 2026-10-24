// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import InstrumentsRegister from "@/components/submissions/InstrumentsRegister";
import { ReturnTemplateReadFromJSON } from "@aequoros/risk-service-api";

const fixture = vi.hoisted(() => ({
  templates: [] as unknown[],
  bankId: "",
  queriedBankId: "",
}));
vi.mock("next/navigation", () => ({
  useSearchParams: () =>
    new URLSearchParams(fixture.bankId ? `bankId=${fixture.bankId}` : ""),
}));
vi.mock("@/components/shell/BankContext", () => ({
  useBankContext: () => ({ bank: { id: "BK-SYNTH001" } }),
  useModuleScope: () => ({
    modules: new Set(["liquidity", "irrbb"]),
    organizationModules: new Set(),
    institutionClass: "sdi",
    hasInstitutionAuthority: true,
    isResolved: true,
    liquidityAggregatedView: true,
    irrbbAggregatedView: true,
  }),
}));
vi.mock("@/lib/api/hooks", () => ({
  useReturnTemplates: (bankId: string) => {
    fixture.queriedBankId = bankId;
    return {
      data: { templates: fixture.templates },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    };
  },
}));
afterEach(() => {
  cleanup();
  fixture.bankId = "";
});

function rule(code: string, status: string) {
  return ReturnTemplateReadFromJSON({
    code,
    instrument_status: status,
    title: `Synthetic ${code}`,
    regulator: "Synthetic regulator",
    family: "capital",
    frequency: "annual",
    fidelity: "PARTIAL",
    generator: "synthetic",
    template_id: code,
    default_channel: "manual",
    supports_working_copy: false,
    directive_citation: "Synthetic citation",
    effective_from: "2027-01-01",
    default_effective_from: "2026-12-31",
    source_url: "https://example.test/instrument",
    commencement_parameter: "synthetic_commencement",
    commencement_approved_by: "Synthetic checker",
    calculations: ["synthetic_ratio: synthetic_method"],
  });
}

describe("instruments register", () => {
  it("shows every status, dates, dependencies and governed approval evidence together", () => {
    fixture.templates = [
      rule("LIVE", "in_force"),
      rule("DRAFT", "exposure_draft"),
      rule("FINAL", "final_not_in_force"),
      rule("REF", "unpublished"),
    ];
    render(<InstrumentsRegister />);
    for (const label of ["In force", "Draft", "Not yet in force", "Reference"])
      expect(screen.getByText(label)).toBeVisible();
    const live = screen
      .getByRole("heading", { name: "Synthetic LIVE" })
      .closest("section")!;
    expect(within(live).getByText(/Effective date: 2027-01-01/)).toBeVisible();
    expect(
      within(live).getByText(/Governed date differs from the default/),
    ).toHaveTextContent("2026-12-31");
    expect(
      within(live).getByText(/Approved by: Synthetic checker/),
    ).toBeVisible();
    expect(
      within(live).getByText("synthetic_ratio: synthetic_method"),
    ).toBeVisible();
    expect(within(live).getByRole("link", { name: "LIVE" })).toHaveAttribute(
      "href",
      "/submissions/returns?code=LIVE",
    );
  });

  it("separates preparation from in-force duties and reference monitoring", () => {
    fixture.templates = [
      rule("LIVE", "in_force"),
      rule("DRAFT", "exposure_draft"),
      rule("FINAL", "final_not_in_force"),
      rule("REF", "unpublished"),
    ];
    render(<InstrumentsRegister upcoming />);
    expect(
      screen.getByRole("heading", { name: "Preparing for upcoming rules" }),
    ).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "Synthetic DRAFT" }),
    ).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "Synthetic FINAL" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("heading", { name: "Synthetic LIVE" }),
    ).toBeNull();
    expect(screen.queryByRole("heading", { name: "Synthetic REF" })).toBeNull();
    expect(screen.getByText(/Expected start if made final/)).toBeVisible();
  });
});

it("offers the supported SDI IRRBB preparation workspace", () => {
  fixture.templates = [rule("SDI-IRRBB-QUARTERLY", "exposure_draft")];
  render(<InstrumentsRegister upcoming />);
  expect(
    screen.getByRole("link", { name: "Open preparation workspace" }),
  ).toHaveAttribute("href", "/irr?preparing=1");
});

it("loads the institution named by a notification rather than the active bank", () => {
  fixture.bankId = "BK-SYNTH002";
  fixture.templates = [rule("SDI-LMT-MONTHLY", "exposure_draft")];
  render(<InstrumentsRegister upcoming />);
  expect(fixture.queriedBankId).toBe("BK-SYNTH002");
  expect(screen.getByText("Institution: BK-SYNTH002")).toBeVisible();
  expect(
    screen.queryByRole("link", { name: "Open preparation workspace" }),
  ).toBeNull();
  expect(
    screen.getByRole("link", { name: "All regulatory instruments" }),
  ).toHaveAttribute("href", "/submissions/instruments?bankId=BK-SYNTH002");
});

it.each([
  ["FX-NOP", ["fx_nop_single_limit_pct", "fx_nop_aggregate_limit_pct"]],
  ["DBK-DAILY", ["fx_nop_single_limit_pct", "fx_nop_aggregate_limit_pct"]],
  ["NPL-MONTHLY", ["npl_limit_pct", "npl_dividend_restriction_pct"]],
] as const)("shows the source-backed limits for %s", (code, limits) => {
  fixture.templates = [
    {
      ...rule(code, "in_force"),
      calculations: limits.map((limit) => `limit parameter: ${limit}`),
    },
  ];
  render(<InstrumentsRegister />);
  const instrument = screen
    .getByRole("heading", { name: `Synthetic ${code}` })
    .closest("section")!;
  for (const limit of limits)
    expect(
      within(instrument).getByText(`limit parameter: ${limit}`, {
        exact: false,
      }),
    ).toBeVisible();
  expect(
    within(instrument).queryByText("No calculation or limit mapping recorded"),
  ).toBeNull();
});
