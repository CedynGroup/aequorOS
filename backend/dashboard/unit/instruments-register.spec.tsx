// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import InstrumentsRegister from "@/components/submissions/InstrumentsRegister";
import { ReturnTemplateReadFromJSON } from "@aequoros/risk-service-api";
import userEvent from "@testing-library/user-event";
import { setActiveJurisdiction } from "@/lib/format";

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
  useBankContext: () => ({
    bank: { id: "BK-SYNTH001", name: "Synthetic Bank" },
    banks: [{ id: "BK-SYNTH002", name: "Other Bank" }],
  }),
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
  vi.useRealTimers();
  fixture.bankId = "";
  setActiveJurisdiction({});
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
  it("shows every status, dates, dependencies and governed approval evidence together", async () => {
    fixture.templates = [
      rule("LIVE", "in_force"),
      rule("DRAFT", "exposure_draft"),
      rule("FINAL", "final_not_in_force"),
      rule("REF", "unpublished"),
    ];
    render(<InstrumentsRegister />);
    for (const label of ["In force", "Draft", "Not yet in force", "Reference"])
      expect(screen.getByText(label, { selector: "summary" })).toBeVisible();
    const live = screen
      .getByRole("heading", { name: "Synthetic LIVE" })
      .closest("section")!;
    expect(within(live).getByText(/Effective date: 2027-01-01/)).toBeVisible();
    await userEvent.click(within(live).getByText("Citation and calculations"));
    expect(
      within(live).getByText(/Governed date differs from the default/),
    ).toHaveTextContent("2026-12-31");
    expect(
      within(live).getByText(/Approved by: Synthetic checker/),
    ).toBeVisible();
    expect(
      within(live).getByText("Additional governed calculation or limit"),
    ).toBeVisible();
    expect(
      within(live).getByRole("link", { name: "Synthetic LIVE" }),
    ).toHaveAttribute("href", "/submissions/returns?code=LIVE");
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

it("offers the supported SDI IRRBB preparation workspace", async () => {
  fixture.templates = [rule("SDI-IRRBB-QUARTERLY", "exposure_draft")];
  render(<InstrumentsRegister upcoming />);
  await userEvent.click(screen.getByText("Citation and calculations"));
  expect(
    screen.getByRole("link", { name: "Open preparation workspace" }),
  ).toHaveAttribute("href", "/irr?preparing=1");
});

it("loads the institution named by a notification rather than the active bank", () => {
  fixture.bankId = "BK-SYNTH002";
  fixture.templates = [rule("SDI-LMT-MONTHLY", "exposure_draft")];
  render(<InstrumentsRegister upcoming />);
  expect(fixture.queriedBankId).toBe("BK-SYNTH002");
  expect(screen.getByText("Institution: Other Bank")).toBeVisible();
  expect(
    screen.queryByRole("link", { name: "Open preparation workspace" }),
  ).toBeNull();
  expect(
    screen.getByRole("link", { name: "All regulatory instruments" }),
  ).toHaveAttribute("href", "/submissions/instruments?bankId=BK-SYNTH002");
});

it.each([
  ["", "exposure_draft", "Draft", "Proposed by BoG"],
  ["BK-SYNTH002", "exposure_draft", "Draft", "Proposed by BoG"],
  ["", "unpublished", "Reference", "No BoG filing duty"],
  ["BK-SYNTH002", "unpublished", "Reference", "No BoG filing duty"],
])(
  "attributes %s %s to the recorded issuer",
  async (bankId, status, label, wording) => {
    setActiveJurisdiction({ regulatorShort: "CBK" });
    fixture.bankId = bankId;
    fixture.templates = [{ ...rule("ISSUER", status), regulator: "BoG" }];
    render(<InstrumentsRegister />);
    expect(fixture.queriedBankId).toBe(bankId || "BK-SYNTH001");
    const badge = screen.getByText(label, { selector: "summary" });
    expect(badge).toHaveAttribute("title", expect.stringContaining(wording));
    expect(badge.getAttribute("title")).not.toContain("CBK");
    await userEvent.setup().click(badge);
    const tooltip = within(screen.getByRole("tooltip"));
    expect(tooltip.getByText("Synthetic citation")).toBeVisible();
    expect(
      tooltip.getByRole("link", { name: "Read the source" }),
    ).toHaveAttribute("href", "https://example.test/instrument");
  },
);

it.each([
  ["FX-NOP", ["fx_nop_single_limit_pct", "fx_nop_aggregate_limit_pct"]],
  ["DBK-DAILY", ["fx_nop_single_limit_pct", "fx_nop_aggregate_limit_pct"]],
  ["NPL-MONTHLY", ["npl_limit_pct", "npl_dividend_restriction_pct"]],
] as const)("shows the source-backed limits for %s", async (code, limits) => {
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
  await userEvent.click(
    within(instrument).getByText("Citation and calculations"),
  );
  expect(
    within(instrument).getByText(/Calculations and limits:/).parentElement,
  ).toHaveTextContent(
    code === "NPL-MONTHLY"
      ? "Non-performing loan limit"
      : "Single-currency net open position limit",
  );
  for (const limit of limits) expect(instrument).not.toHaveTextContent(limit);
  expect(
    within(instrument).queryByText("No calculation or limit mapping recorded"),
  ).toBeNull();
});

it("combines name search and status filtering, then recovers from no matches", async () => {
  fixture.templates = [
    rule("LIVE", "in_force"),
    rule("DRAFT", "exposure_draft"),
    rule("FINAL", "final_not_in_force"),
  ];
  render(<InstrumentsRegister />);
  const user = userEvent.setup();
  await user.selectOptions(screen.getByLabelText("Status"), "exposure_draft");
  expect(
    screen.getByRole("heading", { name: "Synthetic DRAFT" }),
  ).toBeVisible();
  expect(screen.queryByRole("heading", { name: "Synthetic LIVE" })).toBeNull();
  await user.type(screen.getByLabelText("Search instruments"), "LIVE");
  expect(
    screen.getByText("No instruments match your search and status filter."),
  ).toBeVisible();
  await user.selectOptions(screen.getByLabelText("Status"), "");
  expect(screen.getByRole("heading", { name: "Synthetic LIVE" })).toBeVisible();
  await user.clear(screen.getByLabelText("Search instruments"));
  expect(
    screen.getByRole("heading", { name: "Synthetic FINAL" }),
  ).toBeVisible();
});

it("keeps in-force status honest when the only citation is provisional", async () => {
  fixture.templates = [
    {
      ...rule("LIVE", "in_force"),
      effectiveFrom: null,
      sourceUrl: null,
      directiveCitation: "Reporting guide (Sept 2020, draft)",
      generator: "lrt_profile",
      calculations: ["limit parameter: narrow_to_volatile"],
    },
  ];
  render(<InstrumentsRegister />);
  expect(
    screen.getByText("Start date not published by Synthetic regulator"),
  ).toBeVisible();
  await userEvent.click(screen.getByText("Citation and calculations"));
  expect(screen.getByText(/Provisional citation:/)).toBeVisible();
  expect(
    screen
      .getAllByText("Reporting guide (Sept 2020, draft)")
      .find((element) => element.closest("details")?.open),
  ).toBeVisible();
  expect(
    screen.getByText("Narrow liquid assets to volatile liabilities"),
  ).toBeVisible();
  expect(screen.getByText("In force", { selector: "summary" })).toBeVisible();
  expect(document.body).not.toHaveTextContent(
    /BK-SYNTH001|lrt_profile|narrow_to_volatile|synthetic_commencement|Primary source link not recorded/,
  );
});

describe("commencement notice", () => {
  function onDate() {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-25T12:00:00Z"));
  }

  it("counts final rules tomorrow through day 90 before applying table filters", async () => {
    onDate();
    fixture.bankId = "BK-SYNTH002";
    fixture.templates = [
      {
        ...rule("TOMORROW", "final_not_in_force"),
        effectiveFrom: new Date("2026-10-26T00:00:00Z"),
      },
      { ...rule("DAY90", "final_not_in_force"), effectiveFrom: "2027-01-23" },
      { ...rule("DRAFT", "exposure_draft"), effectiveFrom: "2026-11-01" },
    ];
    render(<InstrumentsRegister />);
    const notice = screen.getByRole("note");
    expect(notice).toHaveTextContent("2 rules commence in the next 90 days.");
    expect(
      within(notice).getByRole("link", { name: "Prepare" }),
    ).toHaveAttribute("href", "/submissions/upcoming?bankId=BK-SYNTH002");
    expect(
      screen.queryByRole("link", { name: "Preparing for upcoming rules" }),
    ).toBeNull();
    await userEvent.type(
      screen.getByRole("searchbox", { name: "Search instruments" }),
      "no matching rule",
    );
    expect(
      screen.getByText("No instruments match your search and status filter."),
    ).toBeVisible();
    expect(notice).toHaveTextContent("2 rules commence in the next 90 days.");
  });

  it.each([
    ["final_not_in_force", "2026-10-24"],
    ["final_not_in_force", "2026-10-25"],
    ["final_not_in_force", "2027-01-24"],
    ["final_not_in_force", null],
    ["exposure_draft", "2026-11-01"],
    ["in_force", "2026-11-01"],
    ["unpublished", "2026-11-01"],
  ])("hides the notice for %s with date %s", (status, effectiveFrom) => {
    onDate();
    fixture.templates = [{ ...rule("OTHER", status), effectiveFrom }];
    render(<InstrumentsRegister />);
    expect(screen.queryByRole("note")).toBeNull();
    expect(screen.queryByRole("link", { name: "Prepare" })).toBeNull();
    expect(
      screen.queryByRole("link", { name: "Preparing for upcoming rules" }),
    ).toBeNull();
  });

  it("uses singular copy for one final rule and omits the notice on Upcoming rules", () => {
    onDate();
    fixture.templates = [rule("FINAL", "final_not_in_force")];
    const { unmount } = render(<InstrumentsRegister />);
    expect(screen.getByRole("note")).toHaveTextContent(
      "1 rule commences in the next 90 days.",
    );
    expect(screen.getByRole("link", { name: "Prepare" })).toHaveAttribute(
      "href",
      "/submissions/upcoming",
    );
    unmount();
    render(<InstrumentsRegister upcoming />);
    expect(screen.queryByRole("note")).toBeNull();
    expect(
      screen.getByRole("link", { name: "All regulatory instruments" }),
    ).toHaveAttribute("href", "/submissions/instruments");
  });
});
