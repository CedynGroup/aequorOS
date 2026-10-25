// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { useSyncExternalStore, type ComponentProps } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import {
  IrrDashboardReadFromJSON,
  ReturnTemplateReadFromJSON,
} from "@aequoros/risk-service-api";
import Sidebar from "@/components/shell/Sidebar";
import IrrLayout from "@/app/(app)/irr/layout";
import IrrOverviewPage from "@/app/(app)/irr/page";
import InstrumentsRegister from "@/components/submissions/InstrumentsRegister";
import IrrbbSfCard from "@/components/icaap/p2/IrrbbSfCard";
import { normalizePillar2Register } from "@/lib/api/icaapRiskCapitalNormalize";
import type { ModuleScope } from "@/lib/modules";

const state = vi.hoisted(() => ({
  href: "/",
  sdi: false,
  bankStatus: "exposure_draft",
}));
vi.mock("next/navigation", () => ({
  usePathname: () => new URL(state.href, "https://example.test").pathname,
  useSearchParams: () =>
    new URL(state.href, "https://example.test").searchParams,
}));
vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...props
  }: ComponentProps<"a"> & { href: string }) => (
    <a
      {...props}
      href={href}
      onClick={(event) => {
        event.preventDefault();
        state.href = href;
        window.dispatchEvent(new Event("routechange"));
      }}
    >
      {children}
    </a>
  ),
}));
function scope(): ModuleScope {
  return {
    modules: new Set(["irrbb"]),
    organizationModules: new Set(),
    institutionClass: state.sdi ? "sdi" : "bank",
    hasInstitutionAuthority: true,
    isResolved: true,
    irrbbAggregatedView: true,
  };
}
vi.mock("@/components/shell/BankContext", () => ({
  useBankContext: () => ({
    bank: { id: "BK-SYNTH001" },
    period: { id: "period" },
    moduleScope: scope(),
  }),
  useModuleScope: () => scope(),
}));
vi.mock("@/lib/api/hooks", () => ({
  useReturnTemplates: () => ({
    data: {
      templates: ["IRRBB-PILOT", "SDI-IRRBB-QUARTERLY"].map((code) =>
        ReturnTemplateReadFromJSON({
          code,
          title: code,
          instrument_status:
            code === "IRRBB-PILOT" ? state.bankStatus : "exposure_draft",
          effective_from: "2027-01-01",
          directive_citation: `${code} source`,
        }),
      ),
    },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
  useIrrDashboard: () => ({
    data: IrrDashboardReadFromJSON({
      metrics: { eve_status: "green", worst_scenario_code: "parallel_up_200" },
      period: { id: "period" },
      stored: false,
      eve_scenarios: [],
      gap_table: [],
      trend: [],
      validations: [],
    }),
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
  useRegulatoryRun: () => ({ data: undefined }),
  useRunAllIrrScenarios: () => ({ isPending: false, mutateAsync: vi.fn() }),
}));
function subscribe(listener: () => void) {
  window.addEventListener("routechange", listener);
  return () => window.removeEventListener("routechange", listener);
}
function Journey() {
  const href = useSyncExternalStore(subscribe, () => state.href);
  const path = new URL(href, "https://example.test").pathname;
  return (
    <>
      <Sidebar />
      {path === "/submissions/upcoming" ? (
        <InstrumentsRegister upcoming />
      ) : path.startsWith("/irr") ? (
        <IrrLayout>
          {path === "/irr" ? <IrrOverviewPage /> : <p>Workspace {path}</p>}
        </IrrLayout>
      ) : path.startsWith("/icaap") ? (
        <IrrbbSfCard register={normalizePillar2Register({})} />
      ) : null}
    </>
  );
}
afterEach(() => {
  cleanup();
  state.href = "/";
  state.sdi = false;
  state.bankStatus = "exposure_draft";
});

it("takes an authorized SDI from the sidebar to its supported preparation workspace", () => {
  state.sdi = true;
  state.bankStatus = "in_force";
  render(<Journey />);
  fireEvent.click(screen.getByRole("link", { name: "IRRBB" }));
  expect(
    screen.queryByRole("heading", { name: "Interest Rate Risk" }),
  ).toBeNull();
  fireEvent.click(
    screen.getByRole("link", { name: "Preparing for upcoming rules" }),
  );
  for (const disclosure of screen.getAllByText("Citation and calculations"))
    fireEvent.click(disclosure);
  const entry = screen.getByRole("link", {
    name: "Open preparation workspace",
  });
  expect(entry).toHaveAttribute("href", "/irr?preparing=1");
  fireEvent.click(entry);
  expect(
    screen.getByRole("heading", { name: "Interest Rate Risk" }),
  ).toBeVisible();
  expect(screen.getByText("Draft")).toBeVisible();
  expect(screen.getByText("SDI-IRRBB-QUARTERLY source")).toBeInTheDocument();
  expect(
    screen.queryByRole("link", { name: "Standardised Framework" }),
  ).toBeNull();
  fireEvent.click(screen.getByRole("link", { name: "EVE & NII" }));
  expect(screen.getByText("Workspace /irr/sensitivity")).toBeVisible();
});

it.each([
  ["EVE & NII detail", "/irr/sensitivity"],
  ["Full gap analysis", "/irr/gaps"],
])("keeps preparation through the overview's %s link", (label, path) => {
  state.href = "/irr?preparing=1";
  render(<Journey />);
  const link = screen.getByRole("link", { name: label });
  expect(link).toHaveAttribute("href", `${path}?preparing=1`);
  fireEvent.click(link);
  expect(screen.getByText(`Workspace ${path}`)).toBeVisible();
  expect(screen.getByText("Draft")).toBeVisible();
});

it("keeps the ordinary in-force overview links ordinary", () => {
  state.href = "/irr";
  state.bankStatus = "in_force";
  render(<Journey />);
  expect(
    screen.getByRole("link", { name: "EVE & NII detail" }),
  ).toHaveAttribute("href", "/irr/sensitivity");
  expect(
    screen.getByRole("link", { name: "Full gap analysis" }),
  ).toHaveAttribute("href", "/irr/gaps");
  fireEvent.click(screen.getByRole("link", { name: "Full gap analysis" }));
  expect(screen.getByText("Workspace /irr/gaps")).toBeVisible();
});

it("opens the draft framework deliberately from the ICAAP method card", () => {
  state.href = "/icaap/cycle/pillar2";
  render(<Journey />);
  const link = screen.getByRole("link", {
    name: "Open the standardised framework",
  });
  expect(link).toHaveAttribute("href", "/irr/standardised?preparing=1");
  fireEvent.click(link);
  expect(screen.getByText("Workspace /irr/standardised")).toBeVisible();
  expect(screen.getByText("Draft")).toBeVisible();
});
