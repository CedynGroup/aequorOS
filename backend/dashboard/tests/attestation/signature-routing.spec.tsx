// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, expect, it, vi } from "vitest";
import { AttestationStatusReadFromJSON } from "@aequoros/risk-service-api";
import SignatureRoutingForm from "../../components/attestation/SignatureRoutingForm";
import { apiBaseUrl } from "../../lib/api/client";
import { setAccessToken } from "../../lib/api/token";

vi.mock("@/components/profile/ProfileProvider", () => ({
  useUserProfile: () => ({
    effectiveAuthority: {
      organizationCapabilities: [],
      institutionCapabilities: [],
    },
  }),
}));

const bankId = "BK-SAMP0001";
const packageId = "package-routing";
const endpoint = `${apiBaseUrl}/banks/${bankId}/regulatory-packages/${packageId}/attestation`;
const rawStatus = {
  package_id: packageId,
  attestation_state: "unsigned",
  attestation_cycle: 1,
  can_submit: false,
  certification_digest: null,
  certified_at: null,
  fully_certified_at: null,
  void_reason: null,
  voided_at: null,
  signatures: [],
  recipients: [],
  outstanding: [
    { role: "preparer", count: 1 },
    { role: "approver", count: 1 },
  ],
  policy: {
    source: "configured",
    policy_id: "policy-1",
    require_signature: true,
    require_signed_pdf: false,
    distinct_signers: true,
    required_signatures: [],
    required_attachments: [],
  },
};
let directoryRequests = 0;
let submitted: unknown;
const server = setupServer(
  http.get(`${apiBaseUrl}/organization/users`, () => {
    directoryRequests += 1;
    return HttpResponse.json({ users: [] });
  }),
  http.put(`${endpoint}/recipients`, async ({ request }) => {
    submitted = await request.json();
    return HttpResponse.json(rawStatus);
  }),
);
const client = new QueryClient({
  defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
});

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
afterEach(() => {
  cleanup();
  client.clear();
  server.resetHandlers();
  setAccessToken(null);
  directoryRequests = 0;
  submitted = undefined;
});

it("routes every outstanding signer without organization-directory authority", async () => {
  setAccessToken("synthetic-access-token");
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={client}>
      <SignatureRoutingForm
        bankId={bankId}
        packageId={packageId}
        status={AttestationStatusReadFromJSON(rawStatus)}
      />
    </QueryClientProvider>,
  );

  await user.type(screen.getByLabelText("Preparer recipient"), "user-preparer");
  await user.type(screen.getByLabelText("Approver recipient"), "user-approver");
  await user.type(
    screen.getByLabelText("Routing reason"),
    "Route both signatures before preview",
  );
  await user.click(screen.getByRole("button", { name: "Save signature routing" }));

  await waitFor(() =>
    expect(submitted).toEqual({
      recipients: [
        { signing_role: "preparer", user_id: "user-preparer" },
        { signing_role: "approver", user_id: "user-approver" },
      ],
      reason: "Route both signatures before preview",
    }),
  );
  expect(directoryRequests).toBe(0);
  expect(screen.queryByRole("option")).not.toBeInTheDocument();
});
