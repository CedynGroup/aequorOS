// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import InstrumentsPage from "@/app/(app)/submissions/instruments/page";
import UpcomingRulesPage from "@/app/(app)/submissions/upcoming/page";
import FrameworkStatus from "@/components/icaap/FrameworkStatus";
import RegulatoryModuleTabs from "@/components/shell/RegulatoryModuleTabs";
import SdiLiquidityBoardDisclosure from "@/components/reports/SdiLiquidityBoardDisclosure";
import ThresholdRegisterCard from "@/components/institution/registers/ThresholdRegisterCard";
import { apiBaseUrl } from "@/lib/api/client";
import { setAccessToken } from "@/lib/api/token";
import { QueryAuthorityScopeProvider } from "@/lib/api/useQueryScope";
import { queryAuthorityScope } from "@/lib/api/queryPolicy";

vi.mock("next/navigation", () => ({
  usePathname: () => "/irr",
  useSearchParams: () => new URLSearchParams("preparing=1"),
}));
vi.mock("next-auth/react", () => ({
  useSession: () => ({ data: { user: { roles: [] } } }),
}));
vi.mock("@/components/shell/BankContext", () => {
  const scope = {
    modules: new Set(["capital", "irrbb", "liquidity"]),
    organizationModules: new Set(),
    institutionClass: "bank",
    hasInstitutionAuthority: true,
    isResolved: true,
    capitalConfidentialView: true,
    irrbbAggregatedView: true,
    liquidityAggregatedView: true,
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

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
let client: QueryClient;
function view(children: React.ReactNode) {
  return (
    <QueryClientProvider client={client}>
      <QueryAuthorityScopeProvider
        scope={queryAuthorityScope("OR-SYNTH001", "synthetic@example.test", 1)}
      >
        {children}
      </QueryAuthorityScopeProvider>
    </QueryClientProvider>
  );
}
afterEach(() => {
  cleanup();
  client.clear();
  server.resetHandlers();
  setAccessToken(null);
});

const surfaces = [
  ["instruments", () => <InstrumentsPage />, "Regulatory instruments"],
  ["upcoming", () => <UpcomingRulesPage />, "Preparing for upcoming rules"],
  [
    "framework",
    () => (
      <FrameworkStatus
        bankId="BK-SYNTH001"
        framework={{ code: "synthetic", version: "1" }}
      />
    ),
    "Draft",
  ],
  [
    "module",
    () => (
      <RegulatoryModuleTabs
        tabs={[{ href: "/irr", label: "Overview" }]}
        contextRule="IRRBB-PILOT"
      >
        <p>Preparation workspace</p>
      </RegulatoryModuleTabs>
    ),
    "Preparation workspace",
  ],
  [
    "board disclosure",
    () => (
      <SdiLiquidityBoardDisclosure
        asOf="2026-10-24"
        table1Breaches={1}
        reserveBreaches={0}
      />
    ),
    "Reserve evidence as of 2026-10-24",
  ],
] as const;

it.each(surfaces)(
  "recovers %s metadata through its visible Retry action",
  async (name, component, expected) => {
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    setAccessToken("synthetic-token");
    let calls = 0;
    server.use(
      http.get(`${apiBaseUrl}/*`, ({ request }) => {
        const url = new URL(request.url);
        expect(
          name === "framework" ? url.pathname : url.searchParams.get("bank_id"),
        ).toBe(
          name === "framework"
            ? "/api/v1/banks/BK-SYNTH001/icaap/frameworks/synthetic/versions/1"
            : "BK-SYNTH001",
        );
        calls++;
        if (calls === 1)
          return HttpResponse.json(
            { message: "Synthetic metadata outage" },
            { status: 503 },
          );
        if (name === "framework")
          return HttpResponse.json({
            code: "synthetic",
            version: "1",
            status: "exposure_draft",
            effective_from: "2027-01-01",
            title: "Synthetic framework",
            regulator: "Recorded issuer",
            documents: [{ url: "https://example.test/framework" }],
            attachments: [],
            risk_categories: [],
            sections: [],
            stages: [],
          });
        return HttpResponse.json({
          templates: [
            {
              code: "IRRBB-PILOT",
              title: "Synthetic upcoming rule",
              instrument_status: "exposure_draft",
              regulator: "Recorded issuer",
              family: "irr",
              frequency: "quarterly",
              calculations: [],
            },
            {
              code: "LCR-NSFR",
              title: "Synthetic reference rule",
              instrument_status: "unpublished",
              regulator: "Recorded issuer",
              calculations: [],
            },
          ],
        });
      }),
    );
    render(view(component()));
    expect(await screen.findByText("Synthetic metadata outage")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    if (name === "instruments" || name === "upcoming")
      expect(
        await screen.findByRole("heading", { name: "Synthetic upcoming rule" }),
      ).toBeVisible();
    else
      expect(await screen.findByText(expected, { exact: false })).toBeVisible();
    expect(screen.queryByText("Synthetic metadata outage")).toBeNull();
    expect(calls).toBe(2);
  },
);

it.each([
  ["exposure_draft", "Draft"],
  ["final_not_in_force", "Not yet in force"],
  ["in_force", "In force"],
  ["unpublished", "Reference"],
  [undefined, "Status unavailable"],
] as const)(
  "keeps the threshold register heading stable with %s status",
  async (status, label) => {
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    setAccessToken("synthetic-token");
    server.use(
      http.get(`${apiBaseUrl}/*`, ({ request }) =>
        new URL(request.url).pathname.endsWith("/templates")
          ? HttpResponse.json({
              templates: [
                {
                  code: "LMT",
                  instrument_status: status,
                  regulator: "Recorded issuer",
                },
              ],
            })
          : HttpResponse.json({
              bank_id: "BK-SYNTH001",
              thresholds: [],
              history: [],
            }),
      ),
    );
    render(view(<ThresholdRegisterCard bankId="BK-SYNTH001" />));
    const title = screen.getByRole("heading", {
      name: "Board threshold register",
    });
    const badge = await screen.findByText(label);
    expect(badge).toBeVisible();
    expect(title).toHaveAccessibleName("Board threshold register");
    fireEvent.click(badge);
    expect(screen.getByRole("tooltip")).toBeVisible();
    expect(
      screen.getByRole("link", {
        name: "Source details in the instruments register",
      }),
    ).toHaveAttribute("href", "/submissions/instruments#LMT");
    expect(title).toHaveAccessibleName("Board threshold register");
    fireEvent.click(badge);
    expect(title).toHaveAccessibleName("Board threshold register");
  },
);
