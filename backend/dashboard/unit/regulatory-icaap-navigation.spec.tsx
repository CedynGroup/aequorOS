// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import {
  IcaapCycleReadFromJSON,
  IcaapCycleSummaryReadFromJSON,
  IcaapFrameworkSummaryReadFromJSON,
  ReturnTemplateReadFromJSON,
  ReturnAnchorReadFromJSON,
} from "@aequoros/risk-service-api";
import ReturnsPage from "@/app/(app)/submissions/returns/page";
import IcaapPage from "@/app/(app)/icaap/page";
import IcaapCycleLayout from "@/app/(app)/icaap/[cycleId]/layout";
import IcaapCycleIndex from "@/app/(app)/icaap/[cycleId]/page";
import SectionList from "@/components/icaap/SectionList";
import OverviewReadiness from "@/components/icaap/OverviewReadiness";

const state = vi.hoisted(() => ({
  preparing: true,
  returns: false,
  push: vi.fn(),
  redirect: vi.fn(),
}));
vi.mock("next/font/local", () => ({
  default: () => ({
    className: "test-font",
    style: { fontFamily: "test-font" },
  }),
}));
vi.mock("next/navigation", () => ({
  useSearchParams: () =>
    new URLSearchParams(
      state.returns
        ? "code=ICAAP-REPORT&date=2026-12-31"
        : state.preparing
          ? "preparing=1"
          : "",
    ),
  usePathname: () => "/icaap/cycle/overview",
  useRouter: () => ({ push: state.push }),
  redirect: (href: string) => state.redirect(href),
}));
vi.mock("@/components/shell/BankContext", () => {
  const scope = {
    modules: new Set(["capital"]),
    organizationModules: new Set(),
    institutionClass: "bank",
    isResolved: true,
    hasInstitutionAuthority: true,
    capitalCreate: true,
    capitalConfidentialView: true,
    institutionCapabilities: [
      {
        module: "cap",
        sensitivity: "confidential",
        permission: "view",
        dataScope: { kind: "all" },
        requiresContextualAuthorization: false,
      },
    ],
  };
  return {
    useBankContext: () => ({ bank: { id: "BK-SYNTH001" }, moduleScope: scope }),
    useModuleScope: () => scope,
  };
});
function query<T>(data: T) {
  return { data, isLoading: false, error: null, refetch: vi.fn() };
}
function framework() {
  return IcaapFrameworkSummaryReadFromJSON({
    code: "synthetic",
    version: "1",
    regulator: "Synthetic regulator",
    short_title: "ICAAP framework",
    filing_return_code: "ICAAP-REPORT",
  });
}
function cycle() {
  return IcaapCycleReadFromJSON({
    id: "cycle",
    title: "Synthetic cycle",
    framework: {
      code: "synthetic",
      version: "1",
      short_title: "ICAAP framework",
    },
    fiscal_year: 2026,
    cycle_kind: "rehearsal",
    basis: "solo",
    status: "draft",
    editable: true,
    framework_digest_matches: true,
    sections: [
      {
        key: "governance",
        title: "Governance",
        letter: "a",
        order: 1,
        requirement_counts: {},
        source_status: "confirmed",
      },
    ],
  });
}
vi.mock("@/lib/api/icaap", () => ({
  useIcaapCycles: () =>
    query({
      cycles: [
        IcaapCycleSummaryReadFromJSON({
          id: "cycle",
          title: "Synthetic cycle",
          fiscal_year: 2026,
          cycle_kind: "rehearsal",
          basis: "solo",
          status: "draft",
        }),
      ],
    }),
  useIcaapFrameworks: () => query({ frameworks: [framework()] }),
  useIcaapCycle: () => query(cycle()),
  useIcaapReadiness: () =>
    query({
      readyForFreeze: false,
      pendingPrimaryTextSections: [],
      deadline: { dueDate: null, rag: "none" },
      items: [
        {
          code: "section",
          severity: "blocking",
          scope: "section",
          ref: "governance",
          message: "Complete governance",
        },
      ],
    }),
  useCreateIcaapCycle: () => ({
    isPending: false,
    error: null,
    mutate: (
      _: unknown,
      callbacks: { onSuccess: (cycle: { id: string }) => void },
    ) => callbacks.onSuccess({ id: "new-cycle" }),
  }),
}));
vi.mock("@/components/profile/ProfileProvider", () => ({
  useUserProfile: () => ({ effectiveAuthority: undefined, isLoading: false }),
}));
vi.mock("@/components/impersonation/useImpersonation", () => ({
  useImpersonation: () => ({ impersonating: false }),
}));
vi.mock("@/lib/api/hooks", () => ({
  useReturnAnchors: () =>
    query({
      anchors: [
        ReturnAnchorReadFromJSON({
          reporting_date: "2026-12-31",
          data_status: "computed",
        }),
      ],
    }),
  useRegulatoryPackages: () => query({ packages: [] }),
  useRegulatoryPackage: () => query(undefined),
  useGenerateRegulatoryPackage: () => ({ isPending: false, error: null }),
  useValidateRegulatoryPackage: () => ({ isPending: false, error: null }),
  useReturnTemplates: () =>
    query({
      templates: ["ICAAP-REPORT", "LCR-NSFR", "IRRBB-PILOT"].map((code) =>
        ReturnTemplateReadFromJSON({
          code,
          instrument_status: "exposure_draft",
          family: "icaap",
          fidelity: "PARTIAL",
          title: "ICAAP report",
        }),
      ),
    }),
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  state.preparing = true;
  state.returns = false;
});

it.each([true, false])(
  "preserves preparing=%s when opening and creating cycles",
  (preparing) => {
    state.preparing = preparing;
    render(<IcaapPage />);
    if (!preparing) {
      expect(
        screen.queryByRole("link", { name: "Synthetic cycle" }),
      ).toBeNull();
      return;
    }
    expect(
      screen.getByRole("link", { name: "Synthetic cycle" }),
    ).toHaveAttribute("href", "/icaap/cycle/overview?preparing=1");
    fireEvent.click(screen.getByRole("button", { name: "New cycle" }));
    fireEvent.change(screen.getByRole("textbox", { name: /Reason/ }), {
      target: { value: "Prepare upcoming rules" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create cycle" }));
    expect(state.push).toHaveBeenCalledWith(
      "/icaap/new-cycle/overview?preparing=1",
    );
  },
);

it.each([true, false])(
  "keeps preparation across all cycle tabs, breadcrumbs and sections: %s",
  async (preparing) => {
    state.preparing = preparing;
    await act(async () => {
      render(
        <IcaapCycleLayout params={Promise.resolve({ cycleId: "cycle" })}>
          <SectionList bankId="BK-SYNTH001" cycleId="cycle" />
          <OverviewReadiness bankId="BK-SYNTH001" cycleId="cycle" />
        </IcaapCycleLayout>,
      );
    });
    const suffix = preparing ? "?preparing=1" : "";
    expect(screen.getByRole("link", { name: "ICAAP" })).toHaveAttribute(
      "href",
      `/icaap${suffix}`,
    );
    for (const link of screen.getAllByRole("link")) {
      const href = link.getAttribute("href")!;
      if (href.startsWith("/icaap/cycle/"))
        expect(
          new URL(href, "https://example.test").searchParams.get("preparing"),
        ).toBe(preparing ? "1" : null);
    }
    expect(screen.getByRole("link", { name: /Governance/ })).toHaveAttribute(
      "href",
      `/icaap/cycle/sections/governance${suffix}`,
    );
    expect(screen.getByRole("link", { name: "Open" })).toHaveAttribute(
      "href",
      `/icaap/cycle/sections/governance${suffix}`,
    );
  },
);

it.each(["1", undefined])(
  "preserves preparation through the cycle index redirect: %s",
  async (preparing) => {
    await IcaapCycleIndex({
      params: Promise.resolve({ cycleId: "cycle" }),
      searchParams: Promise.resolve({ preparing }),
    });
    expect(state.redirect).toHaveBeenCalledWith(
      `/icaap/cycle/overview${preparing ? "?preparing=1" : ""}`,
    );
  },
);

it("carries the draft return entry into the ICAAP preparation workspace", () => {
  state.returns = true;
  state.preparing = false;
  render(<ReturnsPage />);
  expect(screen.getByRole("link", { name: "Open ICAAP" })).toHaveAttribute(
    "href",
    "/icaap?preparing=1",
  );
});
