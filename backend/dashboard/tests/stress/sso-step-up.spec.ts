import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const calls = vi.hoisted(() => ({
  backendRequest: vi.fn(),
  readStateCookie: vi.fn(),
  writeAuthorizationCookie: vi.fn(),
  writeStateCookie: vi.fn(),
  clearStateCookie: vi.fn(async () => undefined),
}));

vi.mock("@/auth", () => ({
  auth: vi.fn(async () => ({ accessToken: "synthetic-access-token" })),
}));
vi.mock("@/lib/backendRequest", () => ({
  backendRequest: calls.backendRequest,
}));
vi.mock("@/lib/requestOrigin", () => ({
  requestOrigin: () => "http://dashboard.test",
}));
vi.mock("@/lib/attestation/stepUp", () => ({
  apiBase: () => "http://risk-service.test/api/v1",
  backToCeremony: (_origin: string, returnTo: string, outcome: string) =>
    new Response(null, {
      status: 302,
      headers: { location: `${returnTo}?stepUp=${outcome}` },
    }),
  clearStateCookie: calls.clearStateCookie,
  discover: vi.fn(async () => ({
    authorization_endpoint: "https://idp.example.test/authorize",
    token_endpoint: "https://idp.example.test/token",
  })),
  exchangeCode: vi.fn(async () => ({ id_token: "synthetic-id-token" })),
  fetchClientConfig: vi.fn(async () => ({
    enabled: true,
    issuer: "https://idp.example.test",
    client_id: "synthetic-client",
    client_secret: "synthetic-client-secret",
  })),
  newOpaque: vi.fn(() => "synthetic-state"),
  newPkcePair: vi.fn(() => ({
    verifier: "synthetic-verifier",
    challenge: "synthetic-challenge",
  })),
  readStateCookie: calls.readStateCookie,
  stepUpRedirectUri: () =>
    "http://dashboard.test/api/attestation/step-up/callback",
  unverifiedNonce: () => "synthetic-state",
  writeAuthorizationCookie: calls.writeAuthorizationCookie,
  writeStateCookie: calls.writeStateCookie,
}));

import { GET as startStepUp } from "../../app/api/attestation/step-up/start/route";
import { GET as finishStepUp } from "../../app/api/attestation/step-up/callback/route";

const signoffId = "22222222-2222-4222-8222-222222222222";
const packageId = "11111111-1111-4111-8111-111111111111";

beforeEach(() => {
  calls.backendRequest.mockReset();
  calls.readStateCookie.mockReset();
  calls.writeAuthorizationCookie.mockReset();
  calls.writeStateCookie.mockReset();
});

describe("shared package and stress SSO step-up", () => {
  it("stores only a stress signoff target when starting stress SSO", async () => {
    const response = await startStepUp(
      new NextRequest(
        `http://dashboard.test/api/attestation/step-up/start?bankId=BK-SAMPLE&signoffId=${signoffId}&returnTo=/stress%3Frun=run-1`,
      ),
    );

    expect(response.status).toBe(307);
    expect(calls.writeStateCookie).toHaveBeenCalledWith(
      expect.objectContaining({
        bankId: "BK-SAMPLE",
        signoffId,
        returnTo: "/stress?run=run-1",
      }),
    );
    expect(calls.writeStateCookie.mock.calls[0]?.[0]).not.toHaveProperty(
      "packageId",
      packageId,
    );
  });

  it.each([
    {
      name: "stress sign-off",
      context: { bankId: "BK-SAMPLE", signoffId },
      path: `/banks/BK-SAMPLE/enterprise-stress/signoffs/${signoffId}/step-up`,
      expectedBody: { id_token: "synthetic-id-token" },
    },
    {
      name: "package certification",
      context: { bankId: "BK-SAMPLE", packageId, signingRole: "approver" },
      path: `/banks/BK-SAMPLE/regulatory-packages/${packageId}/attestation/step-up`,
      expectedBody: {
        signing_role: "approver",
        id_token: "synthetic-id-token",
      },
    },
  ])("keeps the fresh id_token server-side for $name", async ({
    context,
    path,
    expectedBody,
  }) => {
    calls.readStateCookie.mockResolvedValue({
      ...context,
      state: "synthetic-state",
      nonce: "synthetic-state",
      codeVerifier: "synthetic-verifier",
      returnTo: "/stress?run=run-1",
    });
    calls.backendRequest.mockResolvedValue(
      Response.json({
        authorization_token: "synthetic-authorization",
        expires_at: "2099-01-01T00:00:00Z",
      }),
    );

    const response = await finishStepUp(
      new NextRequest(
        "http://dashboard.test/api/attestation/step-up/callback?code=synthetic-code&state=synthetic-state",
      ),
    );

    expect(response.status).toBe(302);
    expect(calls.backendRequest).toHaveBeenCalledWith(
      `http://risk-service.test/api/v1${path}`,
      expect.objectContaining({ body: JSON.stringify(expectedBody) }),
    );
    expect(calls.writeAuthorizationCookie).toHaveBeenCalledWith(
      "synthetic-authorization",
      expect.any(Number),
    );
  });
});
