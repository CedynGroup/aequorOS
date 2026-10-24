// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import SdiLiquidityView from "@/components/liquidity/SdiLiquidityView";
import IcaapPage from "@/app/(app)/icaap/page";
import { notificationHref } from "@/components/shell/NotificationDrawer";
import {
  ReturnTemplateReadFromJSON,
  NotificationReadFromJSON,
} from "@aequoros/risk-service-api";
import { accessDeniedForPath, hrefAccess, isPathVisible } from "@/lib/modules";

const state = vi.hoisted(() => ({
  preparing: false,
  pathname: "/liquidity",
  status: "exposure_draft",
}));
vi.mock("next/navigation", () => ({
  useSearchParams: () =>
    new URLSearchParams(state.preparing ? "preparing=1" : ""),
  usePathname: () => state.pathname,
  useRouter: () => ({ push: vi.fn() }),
}));
vi.mock("@/components/shell/BankContext", () => ({
  useBankContext: () => ({ bank: { id: "BK-SYNTH001" } }),
  useModuleScope: () => ({
    modules: new Set(["liquidity", "capital"]),
    organizationModules: new Set(),
    institutionClass: "bank",
    hasInstitutionAuthority: true,
    isResolved: true,
    liquidityAggregatedView: true,
    capitalConfidentialView: true,
    institutionCapabilities: [
      {
        module: "cap",
        sensitivity: "confidential",
        permission: "view",
        requiresContextualAuthorization: false,
        dataScope: { kind: "all" },
      },
    ],
  }),
}));
vi.mock("@/components/icaap/CycleList", () => ({
  default: () => <p>Assessment cycles</p>,
}));
vi.mock("@/lib/api/icaap", () => ({
  useIcaapFrameworks: () => ({
    data: {
      frameworks: [
        {
          code: "synthetic",
          version: "1",
          status: "exposure_draft",
          effectiveFrom: new Date("2027-01-01"),
        },
      ],
    },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
  useIcaapFramework: () => ({
    data: {
      code: "synthetic",
      version: "1",
      status: "exposure_draft",
      effectiveFrom: new Date("2027-01-01"),
      title: "Synthetic framework",
      regulator: "Synthetic regulator",
      documents: [{ url: "https://example.test/icaap.pdf" }],
    },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
}));
vi.mock("@/lib/api/hooks", () => ({
  useReturnTemplates: () => ({
    data: {
      templates: [
        "SDI-LMT-MONTHLY",
        "ICAAP-REPORT",
        "LCR-NSFR",
        "IRRBB-PILOT",
      ].map((code) =>
        ReturnTemplateReadFromJSON({
          code,
          instrument_status: state.status,
          effective_from: "2027-01-01",
          directive_citation: "Synthetic citation",
        }),
      ),
    },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
}));
vi.mock("@/components/basel/sdiHooks", () => ({
  useSdiLiquidityPosition: () => ({
    data: {
      ratios: [
        {
          code: "draft",
          label: "Synthetic draft ratio",
          value_pct: "1",
          threshold_pct: "2",
          status: "below_minimum",
        },
      ],
      reserves: [
        {
          code: "reserve",
          label: "Synthetic reserve",
          value_pct: "3",
          threshold_pct: "2",
          status: "ok",
        },
      ],
      readiness: [
        { module: "liquidity_table1", status: "ready", reasons: [] },
        { module: "maturity_ladder", status: "ready", reasons: [] },
      ],
      maturity_ladder: [],
      as_of: "2026-10-23",
    },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
}));
afterEach(() => {
  cleanup();
  state.preparing = false;
  state.pathname = "/liquidity";
  state.status = "exposure_draft";
});

it("keeps draft ratios out of the ordinary SDI cockpit and its summary", () => {
  render(<SdiLiquidityView bankId="BK-SYNTH001" />);
  expect(screen.getByText("Synthetic reserve")).toBeVisible();
  expect(screen.queryByText("Synthetic draft ratio")).toBeNull();
  expect(screen.queryByText("Table 1 ratios")).toBeNull();
  expect(screen.queryByText("liquidity table1")).toBeNull();
  expect(screen.getByText("0")).toBeVisible();
});

it.each(["preparation", "in force"])(
  "shows SDI ratios for %s with the shared status",
  (mode) => {
    state.preparing = mode === "preparation";
    state.status = mode === "in force" ? "in_force" : "exposure_draft";
    render(<SdiLiquidityView bankId="BK-SYNTH001" />);
    expect(screen.getByText("Synthetic draft ratio")).toBeVisible();
    expect(
      screen.getByText(mode === "in force" ? "In force" : "Draft"),
    ).toBeVisible();
  },
);

it("requires deliberate preparation before mounting the draft ICAAP cycle list", () => {
  state.pathname = "/icaap";
  const { rerender } = render(<IcaapPage />);
  expect(screen.queryByText("Assessment cycles")).toBeNull();
  state.preparing = true;
  rerender(<IcaapPage />);
  expect(screen.getByText("Assessment cycles")).toBeVisible();
});

it("links both bank notices to their own register context", () => {
  for (const bankId of ["BK-SYNTH001", "BK-SYNTH002"]) {
    const notice = NotificationReadFromJSON({
      type: "reporting.instrument.changed:LMT:",
      entity_type: "bank",
      entity_id: bankId,
    });
    expect(notificationHref(notice)).toBe(
      `/submissions/instruments?bankId=${bankId}`,
    );
  }
});

it("admits public legal registers without a Regulatory Reporting module grant", () => {
  const scope = {
    modules: new Set<"liquidity">(["liquidity"]),
    organizationModules: new Set<"liquidity">(),
    institutionClass: "bank",
    hasInstitutionAuthority: true,
    isResolved: true,
    liquidityAggregatedView: true,
  };
  for (const path of ["/submissions/instruments", "/submissions/upcoming"]) {
    expect(hrefAccess(path, scope).state).toBe("enabled");
    expect(isPathVisible(path, scope)).toBe(true);
    expect(accessDeniedForPath(path, scope)).toBeNull();
  }
});
