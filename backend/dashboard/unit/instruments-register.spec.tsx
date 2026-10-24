// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import InstrumentsRegister from "@/components/submissions/InstrumentsRegister";
import { ReturnTemplateReadFromJSON } from "@aequoros/risk-service-api";

const fixture = vi.hoisted(() => ({ templates: [] as unknown[] }));
vi.mock("@/components/shell/BankContext", () => ({
  useBankContext: () => ({ bank: { id: "BK-SYNTH001" } }),
}));
vi.mock("@/lib/api/hooks", () => ({
  useReturnTemplates: () => ({
    data: { templates: fixture.templates },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
}));
afterEach(cleanup);

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
    required_data: ["synthetic_book"],
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
