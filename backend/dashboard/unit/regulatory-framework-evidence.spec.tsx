// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import {
  IcaapFrameworkReadFromJSON,
  IcaapFrameworkSummaryReadFromJSON,
  IcaapCycleReadFromJSON,
  IcaapCycleSummaryReadFromJSON,
} from "@aequoros/risk-service-api";
import IcaapPage from "@/app/(app)/icaap/page";
import CreateCycleDialog from "@/components/icaap/CreateCycleDialog";
import OverviewReadiness from "@/components/icaap/OverviewReadiness";

type Jurisdiction = "KE" | "NG" | "GH";
const state = vi.hoisted(() => ({
  jurisdiction: "KE" as Jurisdiction,
  pinned: "KE" as Jurisdiction,
  preparing: false,
  canView: true,
  canCreate: true,
  loading: false,
  error: null as Error | null,
  noSource: false,
  lookup: vi.fn(),
}));
const definitions = {
  KE: {
    code: "cbk_icaap_gn",
    version: "2016.11",
    regulator: "CBK",
    status: "final",
    effective_from: "2016-11-01",
    filing_return_code: null,
  },
  NG: {
    code: "cbn_srp_icaap",
    version: "2021.09",
    regulator: "CBN",
    status: "final",
    effective_from: "2021-09-01",
    filing_return_code: null,
  },
  GH: {
    code: "bog_icaap",
    version: "2026.02-ed.1",
    regulator: "BoG",
    status: "exposure_draft",
    effective_from: "2027-01-01",
    filing_return_code: "ICAAP-REPORT",
  },
};
function summary(jurisdiction: Jurisdiction) {
  const definition = definitions[jurisdiction];
  return IcaapFrameworkSummaryReadFromJSON({
    ...definition,
    title: `${definition.regulator} own ICAAP framework`,
    short_title: `${definition.regulator} ICAAP`,
    first_as_of_date: "2026-12-31",
  });
}
function fullFramework(jurisdiction: Jurisdiction) {
  const definition = definitions[jurisdiction];
  return IcaapFrameworkReadFromJSON({
    ...definition,
    jurisdiction,
    title: `${definition.regulator} own ICAAP framework`,
    short_title: `${definition.regulator} ICAAP`,
    first_as_of_date: "2026-12-31",
    documents: [
      {
        id: "own-document",
        title: `${definition.regulator} own source`,
        url: state.noSource
          ? null
          : `https://example.test/${jurisdiction}/icaap.pdf`,
      },
    ],
    attachments: [],
    stages: [],
    risk_categories: [],
    sections: [],
    notes: [],
  });
}
function query<T>(data: T) {
  return { data, error: null, isLoading: false, refetch: vi.fn() };
}
vi.mock("next/navigation", () => ({
  useSearchParams: () =>
    new URLSearchParams(state.preparing ? "preparing=1" : ""),
  useRouter: () => ({ push: vi.fn() }),
}));
vi.mock("@/components/shell/BankContext", () => ({
  useBankContext: () => ({ bank: { id: "BK-SYNTH001" } }),
  useModuleScope: () => ({
    modules: new Set(["capital"]),
    organizationModules: new Set(),
    institutionClass: "bank",
    isResolved: true,
    hasInstitutionAuthority: true,
    capitalConfidentialView: state.canView,
    capitalCreate: state.canCreate,
    institutionCapabilities: state.canView
      ? [
          {
            module: "cap",
            sensitivity: "confidential",
            permission: "view",
            dataScope: { kind: "all" },
            requiresContextualAuthorization: false,
          },
        ]
      : [],
  }),
}));
vi.mock("@/lib/api/icaap", () => ({
  useIcaapFrameworks: () => ({
    ...query({ frameworks: [summary(state.jurisdiction)] }),
    isLoading: state.loading,
    error: state.error,
  }),
  useIcaapFramework: (bank: string, code: string, version: string) => {
    state.lookup(bank, code, version);
    const jurisdiction = (Object.keys(definitions) as Jurisdiction[]).find(
      (key) => definitions[key].code === code,
    )!;
    return query(fullFramework(jurisdiction));
  },
  useIcaapCycles: () =>
    query({
      cycles: [
        IcaapCycleSummaryReadFromJSON({
          id: "cycle",
          title: "Saved assessment",
          fiscal_year: 2026,
          cycle_kind: "annual",
          basis: "solo",
          status: "draft",
          as_of_date: "2026-12-31",
        }),
      ],
    }),
  useCreateIcaapCycle: () => ({
    isPending: false,
    error: null,
    mutate: vi.fn(),
  }),
  useIcaapCycle: () =>
    query(
      IcaapCycleReadFromJSON({
        id: "cycle",
        title: "Saved assessment",
        fiscal_year: 2026,
        cycle_kind: "annual",
        basis: "solo",
        status: "draft",
        as_of_date: "2026-12-31",
        framework_digest_matches: true,
        sections: [],
        framework: {
          ...definitions[state.pinned],
          short_title: "Pinned framework",
          first_as_of_date: "2026-12-31",
        },
      }),
    ),
  useIcaapReadiness: () =>
    query({
      readyForFreeze: false,
      deadline: { dueDate: null, rag: "none" },
      items: [
        {
          code: "missing-evidence",
          severity: "blocking",
          scope: "cycle",
          message: "Complete recorded evidence",
        },
      ],
      pendingPrimaryTextSections: [],
    }),
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  state.jurisdiction = "KE";
  state.pinned = "KE";
  state.preparing = false;
  state.canView = true;
  state.canCreate = true;
  state.loading = false;
  state.error = null;
  state.noSource = false;
});

it.each(["KE", "NG"] as const)(
  "admits an authorized final %s framework without preparation",
  (jurisdiction) => {
    state.jurisdiction = jurisdiction;
    render(<IcaapPage />);
    expect(
      screen.getByRole("link", { name: "Saved assessment" }),
    ).toHaveAttribute("href", "/icaap/cycle/overview");
    const badge = screen.getByText("In force");
    expect(badge).toHaveAttribute(
      "title",
      expect.stringContaining(definitions[jurisdiction].effective_from),
    );
    fireEvent.click(badge);
    expect(
      screen.getByRole("link", { name: "Read the source" }),
    ).toHaveAttribute("href", `https://example.test/${jurisdiction}/icaap.pdf`);
    expect(screen.queryByText("Draft", { selector: "summary" })).toBeNull();
  },
);

it("keeps the Ghana draft behind preparation without changing create authority", () => {
  state.jurisdiction = "GH";
  const view = render(<IcaapPage />);
  expect(screen.queryByRole("link", { name: "Saved assessment" })).toBeNull();
  state.preparing = true;
  state.canCreate = false;
  view.rerender(<IcaapPage />);
  expect(screen.getByRole("link", { name: "Saved assessment" })).toBeVisible();
  expect(screen.getByRole("button", { name: "New cycle" })).toBeDisabled();
  const badge = screen.getByText("Draft", { selector: "summary" });
  expect(badge).toHaveAttribute(
    "title",
    expect.stringContaining("Proposed by BoG"),
  );
  fireEvent.click(badge);
  expect(screen.getByRole("link", { name: "Read the source" })).toHaveAttribute(
    "href",
    "https://example.test/GH/icaap.pdf",
  );
});

it("retains module access, loading and failed framework reads at the hub", () => {
  state.canView = false;
  const view = render(<IcaapPage />);
  expect(screen.queryByRole("link", { name: "Saved assessment" })).toBeNull();
  expect(state.lookup).not.toHaveBeenCalled();
  state.canView = true;
  state.loading = true;
  view.rerender(<IcaapPage />);
  expect(screen.queryByRole("link", { name: "Saved assessment" })).toBeNull();
  state.loading = false;
  state.error = new Error("Framework metadata unavailable");
  view.rerender(<IcaapPage />);
  expect(screen.queryByRole("link", { name: "Saved assessment" })).toBeNull();
  expect(screen.getByText("Framework metadata unavailable")).toBeVisible();
});

it("changes creation badges and sources with the selected framework", () => {
  render(
    <CreateCycleDialog
      bankId="BK-SYNTH001"
      frameworks={(["KE", "NG", "GH"] as const).map(summary)}
      onClose={vi.fn()}
      onCreated={vi.fn()}
    />,
  );
  const dialog = screen.getByRole("dialog");
  for (const jurisdiction of ["KE", "NG", "GH"] as const) {
    const definition = definitions[jurisdiction];
    fireEvent.change(
      within(dialog).getByRole("combobox", { name: "Framework" }),
      { target: { value: `${definition.code}|${definition.version}` } },
    );
    const badge = within(dialog).getByText(
      jurisdiction === "GH" ? "Draft" : "In force",
    );
    if (!badge.closest("details")!.open) fireEvent.click(badge);
    expect(
      within(dialog).getByRole("link", { name: "Read the source" }),
    ).toHaveAttribute("href", `https://example.test/${jurisdiction}/icaap.pdf`);
    expect(state.lookup).toHaveBeenLastCalledWith(
      "BK-SYNTH001",
      definition.code,
      definition.version,
    );
  }
});

it("does not substitute a return register when the framework has no source link", () => {
  state.noSource = true;
  render(<OverviewReadiness bankId="BK-SYNTH001" cycleId="cycle" />);
  fireEvent.click(screen.getByText("In force"));
  expect(screen.getByText("CBK own ICAAP framework")).toBeVisible();
  expect(screen.getByText("Source link not recorded.")).toBeVisible();
  expect(
    screen.queryByRole("link", {
      name: /Source details in the instruments register/,
    }),
  ).toBeNull();
});

it.each(["KE", "NG", "GH"] as const)(
  "uses the pinned %s framework in overview and retains readiness refusal",
  (jurisdiction) => {
    state.jurisdiction = jurisdiction === "GH" ? "KE" : "GH";
    state.pinned = jurisdiction;
    render(<OverviewReadiness bankId="BK-SYNTH001" cycleId="cycle" />);
    const badge = screen.getByText(
      jurisdiction === "GH" ? "Draft" : "In force",
    );
    fireEvent.click(badge);
    expect(
      screen.getByRole("link", { name: "Read the source" }),
    ).toHaveAttribute("href", `https://example.test/${jurisdiction}/icaap.pdf`);
    expect(screen.getByText("Not ready to freeze")).toBeVisible();
    expect(screen.getByText("Complete recorded evidence")).toBeVisible();
  },
);
