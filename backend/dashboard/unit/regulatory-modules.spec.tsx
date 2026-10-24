// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import RegulatoryModuleTabs from "@/components/shell/RegulatoryModuleTabs";

const state = vi.hoisted(() => ({
  preparing: false,
  status: "exposure_draft",
  code: "IRRBB-PILOT",
  sdi: false,
  pathname: "/irr/standardised",
}));
vi.mock("next/navigation", () => ({
  usePathname: () => state.pathname,
  useSearchParams: () =>
    new URLSearchParams(state.preparing ? "preparing=1" : ""),
}));
vi.mock("@/components/shell/BankContext", () => ({
  useBankContext: () => ({ bank: { id: "BK-SYNTH001" } }),
  useModuleScope: () => ({
    modules: new Set(["irrbb"]),
    organizationModules: new Set(),
    institutionClass: state.sdi ? "sdi" : "bank",
    hasInstitutionAuthority: true,
    irrbbAggregatedView: true,
    isResolved: true,
  }),
}));
vi.mock("@/lib/api/hooks", () => ({
  useReturnTemplates: () => ({
    data: {
      templates: [
        {
          code: state.code,
          instrumentStatus: state.status,
          effectiveFrom: "2027-01-01",
          directiveCitation: "Synthetic citation",
        },
      ],
    },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
}));
afterEach(() => {
  cleanup();
  state.preparing = false;
  state.status = "exposure_draft";
  state.sdi = false;
  state.code = "IRRBB-PILOT";
  state.pathname = "/irr/standardised";
});
const tabs = [
  { href: "/irr", label: "Overview" },
  {
    href: "/irr/standardised",
    label: "Standardised Framework",
    ruleCode: "IRRBB-PILOT",
  },
];

it("keeps draft capabilities out of the ordinary module surface", () => {
  render(
    <RegulatoryModuleTabs tabs={tabs}>
      <p>Calculation workspace</p>
    </RegulatoryModuleTabs>,
  );
  expect(screen.queryByText("Calculation workspace")).toBeNull();
  expect(
    screen.getByRole("link", { name: "Preparing for upcoming rules" }),
  ).toHaveAttribute("href", "/submissions/upcoming");
  expect(screen.getByText("Draft")).toBeVisible();
});

it("permits deliberate preparation without claiming the draft is in force", () => {
  state.preparing = true;
  render(
    <RegulatoryModuleTabs tabs={tabs}>
      <p>Calculation workspace</p>
    </RegulatoryModuleTabs>,
  );
  expect(screen.getByText("Calculation workspace")).toBeVisible();
  expect(screen.getByText("Draft")).toHaveAttribute(
    "title",
    expect.stringContaining("not law yet"),
  );
});

it("opens the ordinary workspace once the rule is in force", () => {
  state.status = "in_force";
  render(
    <RegulatoryModuleTabs tabs={tabs}>
      <p>Calculation workspace</p>
    </RegulatoryModuleTabs>,
  );
  expect(screen.getByText("Calculation workspace")).toBeVisible();
  expect(screen.getByText("In force")).toBeVisible();
});

it("does not open the bank-only framework for an SDI in preparation mode", () => {
  state.sdi = true;
  state.preparing = true;
  render(
    <RegulatoryModuleTabs tabs={tabs}>
      <p>SDI workspace</p>
    </RegulatoryModuleTabs>,
  );
  expect(screen.queryByText("SDI workspace")).toBeNull();
  expect(
    screen.queryByRole("link", { name: "Standardised Framework" }),
  ).toBeNull();
});

it("gates untagged draft tabs and preserves preparation when navigating", () => {
  state.pathname = "/irr";
  const { rerender } = render(
    <RegulatoryModuleTabs tabs={tabs} contextRule="IRRBB-PILOT">
      <p>Overview workspace</p>
    </RegulatoryModuleTabs>,
  );
  expect(screen.queryByText("Overview workspace")).toBeNull();
  expect(screen.queryByRole("link", { name: "Overview" })).toBeNull();
  state.preparing = true;
  rerender(
    <RegulatoryModuleTabs tabs={tabs} contextRule="IRRBB-PILOT">
      <p>Overview workspace</p>
    </RegulatoryModuleTabs>,
  );
  expect(screen.getByText("Overview workspace")).toBeVisible();
  expect(screen.getByRole("link", { name: "Overview" })).toHaveAttribute(
    "href",
    "/irr?preparing=1",
  );
});

it("keeps reference monitoring accessible without preparation mode", () => {
  state.status = "unpublished";
  render(
    <RegulatoryModuleTabs tabs={tabs}>
      <p>Monitoring workspace</p>
    </RegulatoryModuleTabs>,
  );
  expect(screen.getByText("Monitoring workspace")).toBeVisible();
  expect(screen.getByText("Reference")).toBeVisible();
});
