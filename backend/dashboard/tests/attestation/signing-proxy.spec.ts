import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const backendRequest = vi.hoisted(() => vi.fn());

vi.mock("@/auth", () => ({
  auth: vi.fn(async () => ({ accessToken: "synthetic-access-token" })),
}));
vi.mock("@/lib/backendRequest", () => ({ backendRequest }));
vi.mock("@/lib/attestation/stepUp", () => ({
  apiBase: () => "http://risk-service.test/api/v1",
  takeAuthorizationCookie: vi.fn(async () => "synthetic-step-up-token"),
}));

import { POST as certify } from "../../app/api/attestation/certify/route";
import { POST as certifyAndSend } from "../../app/api/attestation/certify-and-send/route";

const cases = [
  {
    name: "certify",
    post: certify,
    path: "/api/attestation/certify",
    body: {
      bankId: "BK-SAMPLE",
      packageId: "11111111-1111-4111-8111-111111111111",
      signingRole: "approver",
      expectedCertificationDigest: "digest-1",
    },
  },
  {
    name: "certify and send",
    post: certifyAndSend,
    path: "/api/attestation/certify-and-send",
    body: {
      bankId: "BK-SAMPLE",
      packageId: "11111111-1111-4111-8111-111111111111",
      signingRole: "preparer",
      expectedCertificationDigest: "digest-1",
      recipients: [],
      placements: [],
      reason: "Synthetic route test",
    },
  },
] as const;

describe("signing server routes", () => {
  beforeEach(() => backendRequest.mockReset());

  it.each(cases)("passes the backend status and body through for $name", async ({
    post,
    path,
    body,
  }) => {
    backendRequest.mockResolvedValue(
      new Response("named Signer binding required", {
        status: 403,
        headers: { "content-type": "text/plain" },
      }),
    );

    const response = await post(
      new NextRequest(`http://dashboard.test${path}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      }),
    );

    expect(response.status).toBe(403);
    expect(response.headers.get("content-type")).toBe("text/plain");
    await expect(response.text()).resolves.toBe("named Signer binding required");
  });
});
