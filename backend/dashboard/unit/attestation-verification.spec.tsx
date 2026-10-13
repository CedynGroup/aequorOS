// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SessionProvider } from "next-auth/react";
import { PathnameContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import AttestationPanel from "../components/attestation/AttestationPanel";
import ProfileProvider from "../components/profile/ProfileProvider";
import BankProvider from "../components/shell/BankContext";
import { apiBaseUrl } from "../lib/api/client";
import { setAccessToken } from "../lib/api/token";
import { QueryAuthorityScopeProvider } from "../lib/api/useQueryScope";
import { queryAuthorityScope } from "../lib/api/queryPolicy";
import { setActiveJurisdiction } from "../lib/format";

// Next compiles local fonts at build time; jsdom has no font compiler.
vi.mock("next/font/local", () => ({
  default: () => ({ className: "local-font", style: { fontFamily: "serif" } }),
}));

const bankId = "BK-SAMP0001";
const packageId = "package-1";
const endpoint = `${apiBaseUrl}/banks/${bankId}/regulatory-packages/${packageId}/attestation`;
const server = setupServer(
  http.get(`${apiBaseUrl}/banks`, () =>
    HttpResponse.json({
      banks: [
        {
          id: bankId,
          name: "Sample bank",
          currency: "EUR",
          jurisdiction_code: "EU",
          institution_type: "universal_bank",
          institution_type_detail: null,
          jurisdiction: null,
        },
      ],
    }),
  ),
  http.get(`${apiBaseUrl}/banks/${bankId}/reporting-periods`, () =>
    HttpResponse.json({ periods: [] }),
  ),
  http.get(`${apiBaseUrl}/feature-flags`, () =>
    HttpResponse.json({ bi_enabled: false, bi_nlq_enabled: false }),
  ),
  http.get(endpoint, () =>
    HttpResponse.json({
      package_id: packageId,
      attestation_state: "unsigned",
      attestation_cycle: 1,
      can_submit: true,
      signatures: [],
      recipients: [],
      outstanding: [],
      policy: {
        source: "esign_disabled",
        require_signature: false,
        require_signed_pdf: false,
        distinct_signers: true,
        required_signatures: [],
        required_attachments: [],
      },
    }),
  ),
);
let client: QueryClient;

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
afterEach(() => {
  cleanup();
  client.clear();
  server.resetHandlers();
  setAccessToken(null);
  setActiveJurisdiction({});
});

it("verifies on request, distinguishes skipped checks, and refreshes an integrity failure", async () => {
  const report = {
    package_id: packageId,
    return_code: "BSD3",
    reporting_date: "2026-03-31",
    verified_at: "2026-04-01T10:00:00Z",
    overall_passed: true,
    chain_ok: true,
    chain_broken_at: null as string | null,
    signatures: [],
    checks: [
      {
        check: "content_binding",
        passed: true,
        detail: "Frozen figures match the signed digest.",
        evidence: { status: "passed" },
      },
      {
        check: "pdf_signature",
        passed: false,
        detail: "No signed PDF is required by this deployment.",
        evidence: { status: "skipped" },
      },
    ],
  };
  server.use(http.get(`${endpoint}/verify`, () => HttpResponse.json(report)));
  client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  setAccessToken("fixture-access-token");
  const user = userEvent.setup();
  render(
    <SessionProvider session={null} refetchOnWindowFocus={false}>
      <QueryClientProvider client={client}>
        <QueryAuthorityScopeProvider
          scope={queryAuthorityScope("OR-DEM00001", "fixture@example.test", 1)}
        >
          <PathnameContext.Provider value="/submissions/returns">
            <ProfileProvider>
              <BankProvider>
                <AttestationPanel
                  bankId={bankId}
                  packageId={packageId}
                  returnLabel="BSD3 · 31 Mar 2026"
                  returnFamily="liquidity"
                  packageStatus="validated"
                  validationClean
                />
              </BankProvider>
            </ProfileProvider>
          </PathnameContext.Provider>
        </QueryAuthorityScopeProvider>
      </QueryClientProvider>
    </SessionProvider>,
  );
  expect(await screen.findByText(/Signing is switched off/)).toBeVisible();
  expect(
    screen.queryByText("Every applicable check passed."),
  ).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Verify" }));
  expect(
    await screen.findByText("Every applicable check passed."),
  ).toBeVisible();
  const checks = screen.getAllByRole("listitem");
  expect(
    within(checks[0]).getByText("PDF cryptographic validity"),
  ).toBeVisible();
  expect(within(checks[0]).getByText("Skipped")).toBeVisible();
  expect(within(checks[1]).getByText("Content binding")).toBeVisible();
  expect(within(checks[1]).getByText("Pass")).toBeVisible();
  expect(screen.getByText(/1 pass · 0 fail · 1 skipped/)).toBeVisible();
  expect(
    screen.getByText(/Skipped checks had nothing to validate/),
  ).toBeVisible();
  expect(screen.getByText("Signature hash chain intact")).toBeVisible();

  server.use(
    http.get(`${endpoint}/verify`, () =>
      HttpResponse.json({
        ...report,
        overall_passed: false,
        chain_ok: false,
        chain_broken_at: "signature-row-2",
        checks: [
          {
            check: "content_binding",
            passed: false,
            detail: "Frozen figures no longer match the signed digest.",
            evidence: { status: "failed" },
          },
        ],
      }),
    ),
  );
  await user.click(screen.getByRole("button", { name: "Verify" }));
  expect(await screen.findByText(/Verification did not pass/)).toBeVisible();
  expect(screen.getByText("Fail")).toBeVisible();
  expect(screen.getByText(/0 pass · 1 fail · 0 skipped/)).toBeVisible();
  expect(screen.getByText("Signature hash chain is broken")).toBeVisible();
  expect(screen.getByText("first broken row: signature-row-2")).toBeVisible();
  expect(
    screen.queryByText("Every applicable check passed."),
  ).not.toBeInTheDocument();
  expect(screen.queryByText("Skipped")).not.toBeInTheDocument();
});
