/**
 * Spend an SSO-obtained stress sign-off authorization without exposing it to
 * the browser. The callback binds it to the signoff and digest; this proxy
 * consumes the HttpOnly cookie once and preserves the backend's verdict.
 */

import { NextResponse, type NextRequest } from "next/server";
import { auth } from "@/auth";
import { backendRequest } from "@/lib/backendRequest";
import { apiBase, takeAuthorizationCookie } from "@/lib/attestation/stepUp";

interface AttestBody {
  bankId?: string;
  signoffId?: string;
  expectedSigningDigest?: string;
  credibilityRationale?: string;
  boardChallenge?: string | null;
  reason?: string;
}

export async function POST(request: NextRequest): Promise<Response> {
  const session = await auth();
  if (!session?.accessToken) {
    return NextResponse.json({ error: "not_authenticated" }, { status: 401 });
  }

  let body: AttestBody;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }
  const {
    bankId,
    signoffId,
    expectedSigningDigest,
    credibilityRationale,
    boardChallenge,
    reason,
  } = body;
  if (
    !bankId ||
    !signoffId ||
    !expectedSigningDigest ||
    !credibilityRationale ||
    !reason
  ) {
    return NextResponse.json(
      {
        error:
          "bankId, signoffId, expectedSigningDigest, credibilityRationale and reason are required",
      },
      { status: 400 },
    );
  }

  const authorization = await takeAuthorizationCookie();
  if (!authorization) {
    return NextResponse.json(
      {
        error: "step_up_required",
        message:
          "No signing authorization is held. Re-authenticate with your institution to attest.",
      },
      { status: 401 },
    );
  }

  const response = await backendRequest(
    `${apiBase()}/banks/${encodeURIComponent(bankId)}/enterprise-stress/signoffs/${encodeURIComponent(signoffId)}/attest`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${session.accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        authorization_token: authorization,
        expected_signing_digest: expectedSigningDigest,
        credibility_rationale: credibilityRationale,
        board_challenge: boardChallenge ?? null,
        reason,
      }),
      cache: "no-store",
    },
  );
  return new Response(response.body, {
    status: response.status,
    headers: response.headers,
  });
}
