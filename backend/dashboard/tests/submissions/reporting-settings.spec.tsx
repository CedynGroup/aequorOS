// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import React from "react";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SessionProvider } from "next-auth/react";
import {
  PathnameContext,
  SearchParamsContext,
} from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import GatewayPage from "../../app/(app)/submissions/settings/reporting-gateway/page";
import FilingProfilePage from "../../app/(app)/submissions/settings/filing-profile/page";
import ProfileProvider from "../../components/profile/ProfileProvider";
import BankProvider from "../../components/shell/BankContext";
import { apiBaseUrl } from "../../lib/api/client";
import { setAccessToken } from "../../lib/api/token";
import { QueryAuthorityScopeProvider } from "../../lib/api/useQueryScope";
import { queryAuthorityScope } from "../../lib/api/queryPolicy";

/**
 * Reporting settings: the gateway connection a bank only reads (AequorOS
 * enrols it), its connection test, and the filing profile's default channel,
 * which offers the gateway only once the bank is enrolled. Every read and
 * write is answered at the MSW network boundary through the real providers.
 */

const BANK = "BK-SAMP0001";

const state: {
  enrolled: boolean;
  configure: boolean;
  testOk: boolean;
  writes: { path: string; body: unknown }[];
  statusReads: number;
  /** The staff plane's scheduled renewal check is running for this bank. */
  automaticRenewal: boolean;
} = {
  enrolled: false,
  configure: true,
  testOk: true,
  writes: [],
  statusReads: 0,
  automaticRenewal: false,
};

function capability(permission: "view" | "configure") {
  return {
    module: "reg",
    permission,
    sensitivity: "restricted",
    requires_contextual_authorization: false,
    data_scope: { kind: "all", branches: [], regions: [] },
  };
}

function gatewayStatus() {
  if (!state.enrolled) {
    return {
      enrolled: false,
      institution_code: null,
      enrolled_at: null,
      connection: null,
      signing_key: null,
      last_connection_test_at: null,
      last_connection_test_ok: null,
      steps: [
        { key: "enrolled", done: false },
        { key: "signing_key_created", done: false },
        { key: "connection_tested", done: false },
        { key: "first_filing", done: false },
      ],
      transmissions: [],
    };
  }
  return {
    enrolled: true,
    institution_code: "0142",
    enrolled_at: "2026-02-03T09:00:00Z",
    connection: {
      client_id: "cl-0142-01",
      environment: "production",
      renews_by: "2027-06-14T00:00:00Z",
      fingerprint_sha256: "ab".repeat(32),
      renewed_at: state.automaticRenewal ? "2026-06-14T00:00:00Z" : null,
      automatic_renewal_from: state.automaticRenewal
        ? "2027-05-15T00:00:00Z"
        : null,
    },
    signing_key: {
      kid: "0142-sign-20260203",
      algorithm: "ES256",
      held_by: "aequoros_vault",
      created_at: "2026-02-03T09:00:00Z",
      last_used_at: "2026-11-21T16:38:00Z",
    },
    last_connection_test_at: "2026-11-21T16:30:00Z",
    last_connection_test_ok: true,
    steps: [
      { key: "enrolled", done: true },
      { key: "signing_key_created", done: true },
      { key: "connection_tested", done: true },
      { key: "first_filing", done: true },
    ],
    transmissions: [
      {
        package_id: "11111111-1111-4111-8111-111111111111",
        return_code: "BSD2",
        reporting_date: "2026-10-31",
        approved_by_role: "Chief Finance Officer",
        sent_at: "2026-11-21T16:38:00Z",
        receipt_id: "SRG-26-0114822",
        gateway_status: "in_review",
      },
    ],
  };
}

const server = setupServer(
  http.get(`${apiBaseUrl}/auth/me`, () =>
    HttpResponse.json({
      user_id: "actor",
      effective_authority: {
        authv: 1,
        organization_capabilities: [],
        institution_capabilities: [
          {
            institution_id: BANK,
            capabilities: [
              capability("view"),
              ...(state.configure ? [capability("configure")] : []),
            ],
          },
        ],
      },
    }),
  ),
  http.get(`${apiBaseUrl}/banks`, () =>
    HttpResponse.json({
      banks: [
        {
          id: BANK,
          name: "Fixture Bank",
          currency: "GHS",
          jurisdiction_code: "GH",
          license_type: "universal",
          institution_type: "universal_bank",
        },
      ],
    }),
  ),
  http.get(`${apiBaseUrl}/feature-flags`, () =>
    HttpResponse.json({ bi_enabled: false, bi_nlq_enabled: false }),
  ),
  http.get(`${apiBaseUrl}/banks/${BANK}/reporting-periods`, () =>
    HttpResponse.json({ periods: [] }),
  ),
  http.get(
    `${apiBaseUrl}/banks/${BANK}/regulatory-reporting/reporting-gateway`,
    () => {
      state.statusReads += 1;
      return HttpResponse.json(gatewayStatus());
    },
  ),
  http.post(
    `${apiBaseUrl}/banks/${BANK}/regulatory-reporting/reporting-gateway/connection-test`,
    () => {
      state.writes.push({ path: "connection-test", body: null });
      return HttpResponse.json({
        ok: state.testOk,
        gateway_keys: state.testOk ? 1 : 0,
        tested_at: "2026-11-21T17:00:00Z",
        message: state.testOk
          ? "Connected: the gateway issued a token and published its keys."
          : "The AequorOS Reporting Gateway could not be reached.",
      });
    },
  ),
  http.get(
    `${apiBaseUrl}/banks/${BANK}/regulatory-reporting/filing-profile`,
    () =>
      HttpResponse.json({
        bank_id: BANK,
        institution_code: "0142",
        reporting_basis: "solo",
        contact_name: "Ama Mensah",
        contact_email: null,
        default_channel: null,
        updated_at: null,
        orass_ready: false,
      }),
  ),
  http.put(
    `${apiBaseUrl}/banks/${BANK}/regulatory-reporting/filing-profile`,
    async ({ request }) => {
      const body: unknown = await request.json();
      state.writes.push({ path: "filing-profile", body });
      return HttpResponse.json({
        bank_id: BANK,
        created: true,
        ...(body as object),
      });
    },
  ),
  http.get(
    `${apiBaseUrl}/banks/${BANK}/regulatory-reporting/channel-configs/orass_api`,
    () =>
      HttpResponse.json(
        { detail: "No configuration stored." },
        { status: 404 },
      ),
  ),
  http.get(`${apiBaseUrl}/regulatory-reporting/templates`, () =>
    HttpResponse.json({ templates: [] }),
  ),
);

let client: QueryClient;
beforeEach(() => {
  state.enrolled = false;
  state.configure = true;
  state.testOk = true;
  state.writes = [];
  state.statusReads = 0;
  state.automaticRenewal = false;
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  setAccessToken("synthetic-session");
  server.listen({ onUnhandledRequest: "warn" });
});
afterEach(() => {
  vi.useRealTimers();
  server.resetHandlers();
  server.close();
  cleanup();
  client.clear();
  setAccessToken(null);
});

function Providers({ children }: React.PropsWithChildren) {
  return (
    <SessionProvider
      session={{
        expires: "2099-01-01",
        accessToken: "synthetic-session",
        user: {
          email: "actor@example.test",
          organizationId: "OR-DEM00001",
          authorizationVersion: 1,
        },
      }}
      refetchOnWindowFocus={false}
    >
      <QueryClientProvider client={client}>
        <QueryAuthorityScopeProvider
          scope={queryAuthorityScope("OR-DEM00001", "actor", 1)}
        >
          <PathnameContext.Provider value="/submissions/settings/reporting-gateway">
            <SearchParamsContext.Provider value={new URLSearchParams()}>
              <ProfileProvider>
                <BankProvider>{children}</BankProvider>
              </ProfileProvider>
            </SearchParamsContext.Provider>
          </PathnameContext.Provider>
        </QueryAuthorityScopeProvider>
      </QueryClientProvider>
    </SessionProvider>
  );
}

it("tells an unenrolled bank that AequorOS enrols it, with nothing to set up", async () => {
  render(<GatewayPage />, { wrapper: Providers });

  expect(
    await screen.findByText("Your AequorOS team enrols your bank"),
  ).toBeVisible();
  expect(screen.getByText("Not yet enrolled")).toBeVisible();
  expect(screen.getByText("0142")).toBeVisible();
  // No certificate, key or credential controls exist for a bank.
  expect(
    screen.queryByRole("button", { name: /certificate|key|credential/i }),
  ).toBeNull();
  expect(screen.queryByRole("textbox")).toBeNull();
});

it("shows an enrolled bank's connection, signature and receipts", async () => {
  state.enrolled = true;
  render(<GatewayPage />, { wrapper: Providers });

  expect(await screen.findByText(/Connected · Production/)).toBeVisible();
  expect(screen.getByText("Enrolled by AequorOS")).toBeVisible();
  expect(screen.getAllByText("0142-sign-20260203").length).toBeGreaterThan(0);
  expect(
    screen.getByText("AequorOS, encrypted for your bank only"),
  ).toBeVisible();
  const table = screen.getByRole("table");
  expect(within(table).getByText("SRG-26-0114822")).toBeVisible();
  expect(within(table).getByText("In review")).toBeVisible();
  expect(within(table).getByText("Chief Finance Officer")).toBeVisible();
  expect(
    screen.queryByText(/sandbox: filings sent here are tests/i),
  ).toBeNull();
});

it("states when the connection certificate expires, without promising a renewal", async () => {
  // The fixture certificate is valid until 14 Jun 2027; read the page on a fixed day.
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-22T12:00:00Z"));
  state.enrolled = true;
  render(<GatewayPage />, { wrapper: Providers });

  expect(await screen.findByText("Valid until 14 Jun 2027")).toBeVisible();
  expect(screen.getByText("Valid")).toBeVisible();
  expect(
    screen.getByText(/ask your AequorOS team to renew it before it expires/),
  ).toBeVisible();
  expect(
    screen.queryByText(/renews automatically|renewed before it expires/),
  ).toBeNull();
});

it("says AequorOS renews the certificate only while its scheduled check runs", async () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-22T12:00:00Z"));
  state.enrolled = true;
  state.automaticRenewal = true;
  render(<GatewayPage />, { wrapper: Providers });

  expect(
    await screen.findByText(
      /AequorOS renews its certificate automatically from 15 May 2027/,
    ),
  ).toBeVisible();
  expect(
    screen.getByText("Automatic from 15 May 2027 · last renewed 14 Jun 2026"),
  ).toBeVisible();
  expect(screen.queryByText(/ask your AequorOS team to renew/)).toBeNull();
});

it("leaves renewal to the AequorOS team when no scheduled check is evidenced", async () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-22T12:00:00Z"));
  state.enrolled = true;
  render(<GatewayPage />, { wrapper: Providers });

  expect(await screen.findByText("By your AequorOS team")).toBeVisible();
  expect(screen.queryByText(/renews its certificate automatically/)).toBeNull();
});

it("warns in the last thirty days of the connection certificate", async () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2027-06-01T12:00:00Z"));
  state.enrolled = true;
  render(<GatewayPage />, { wrapper: Providers });

  expect(
    await screen.findByText("Valid until 14 Jun 2027 · expires within 30 days"),
  ).toBeVisible();
  expect(screen.getByText("Expires soon")).toBeVisible();
});

it("tests the connection and refreshes the status", async () => {
  state.enrolled = true;
  render(<GatewayPage />, { wrapper: Providers });
  const button = await screen.findByRole("button", { name: "Test connection" });
  await waitFor(() => expect(button).toBeEnabled());

  await userEvent.setup().click(button);

  expect(
    await screen.findByText(
      "Connected: the gateway issued a token and published its keys.",
    ),
  ).toBeVisible();
  expect(state.writes.map((w) => w.path)).toEqual(["connection-test"]);
  await waitFor(() => expect(state.statusReads).toBeGreaterThanOrEqual(2));
});

it("explains why a viewer cannot test the connection", async () => {
  state.enrolled = true;
  state.configure = false;
  render(<GatewayPage />, { wrapper: Providers });
  const button = await screen.findByRole("button", { name: "Test connection" });

  await waitFor(() => {
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription(
      /Requires Regulatory Reporting configure access/i,
    );
  });
  await userEvent.setup().click(button);
  expect(state.writes).toEqual([]);
});

it("offers the gateway as the default channel only once the bank is enrolled", async () => {
  render(<FilingProfilePage />, { wrapper: Providers });
  const gateway = await screen.findByRole("radio", {
    name: /reporting gateway/i,
  });
  expect(gateway).toBeDisabled();
  expect(
    screen.getByText(/Available once AequorOS has enrolled your bank/),
  ).toBeVisible();

  cleanup();
  client.clear();
  state.enrolled = true;
  render(<FilingProfilePage />, { wrapper: Providers });
  const enrolled = await screen.findByRole("radio", {
    name: /reporting gateway/i,
  });
  await waitFor(() => expect(enrolled).toBeEnabled());
  const user = userEvent.setup();
  await user.click(enrolled);
  await user.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() =>
    expect(state.writes).toEqual([
      {
        path: "filing-profile",
        body: {
          reporting_basis: "solo",
          contact_name: "Ama Mensah",
          contact_email: null,
          default_channel: "reporting_gateway",
        },
      },
    ]),
  );
  expect(await screen.findByText("Filing profile saved.")).toBeVisible();
});
