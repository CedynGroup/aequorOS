/**
 * POST /api/auth/break-glass — audited emergency staff sign-in.
 *
 * Relays the designated row's email + one-off emergency code server-side to
 * POST ${OPERATOR_API_URL}/operator/auth/break-glass and, on success,
 * establishes the same HttpOnly op_session cookie the other routes set — with
 * the short break-glass JWT in `token` and `mode: 'break_glass'`.
 *
 * The operator API owns every policy: disabled deployments answer 404, a
 * non-designated row answers the generic 401, repeated failures answer 429,
 * and each attempt is audited and alerted there. The cookie dies with the
 * token's 15-minute expiry; there is no refresh.
 */

import { NextResponse, type NextRequest } from "next/server";
import {
  SESSION_COOKIE,
  encodeCookie,
  operatorApiUrl,
} from "@/lib/server-auth";

export const dynamic = "force-dynamic";

interface LoginUpstream {
  access_token?: unknown;
  expires_at?: unknown;
  operator?: { email?: unknown; display_name?: unknown; role?: unknown };
}

export async function POST(req: NextRequest) {
  let body: { email?: unknown; code?: unknown };
  try {
    body = (await req.json()) as { email?: unknown; code?: unknown };
  } catch {
    return NextResponse.json(
      {
        error: { code: "invalid_request", message: "Malformed request body." },
      },
      { status: 400 },
    );
  }
  const email =
    typeof body.email === "string" ? body.email.trim().toLowerCase() : "";
  const code = typeof body.code === "string" ? body.code : "";
  if (!email || !code) {
    return NextResponse.json(
      {
        error: {
          code: "invalid_request",
          message: "Email and emergency access code are required.",
        },
      },
      { status: 400 },
    );
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${operatorApiUrl()}/operator/auth/break-glass`, {
      redirect: "error",
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({ email, code }),
      cache: "no-store",
    });
  } catch {
    return NextResponse.json(
      {
        error: {
          code: "operator_api_unreachable",
          message: `The console could not reach the operator API at ${operatorApiUrl()}.`,
        },
      },
      { status: 502 },
    );
  }

  if (!upstream.ok) {
    // Pass the backend envelope through untouched: 404 means the deployment
    // has not enabled emergency access, 401 stays generic, 429 is the lockout.
    const text = await upstream.text();
    return new NextResponse(text, {
      status: upstream.status,
      headers: {
        "Content-Type":
          upstream.headers.get("content-type") ?? "application/json",
      },
    });
  }

  let payload: LoginUpstream;
  try {
    payload = (await upstream.json()) as LoginUpstream;
  } catch {
    return NextResponse.json(
      {
        error: {
          code: "malformed_response",
          message: "The operator API returned an unreadable response.",
        },
      },
      { status: 502 },
    );
  }
  const token = payload.access_token;
  const expiresAt = payload.expires_at;
  const sessionEmail =
    typeof payload.operator?.email === "string"
      ? payload.operator.email
      : email;
  const exp =
    typeof expiresAt === "string"
      ? Math.floor(Date.parse(expiresAt) / 1000)
      : NaN;
  if (typeof token !== "string" || !token || !Number.isFinite(exp)) {
    return NextResponse.json(
      {
        error: {
          code: "malformed_response",
          message: "The operator API returned no usable session.",
        },
      },
      { status: 502 },
    );
  }

  const res = NextResponse.json({
    ok: true,
    email: sessionEmail,
    role:
      typeof payload.operator?.role === "string" ? payload.operator.role : null,
  });
  res.cookies.set(
    SESSION_COOKIE,
    encodeCookie({ token, email: sessionEmail, exp, mode: "break_glass" }),
    {
      httpOnly: true,
      sameSite: "lax",
      secure: req.nextUrl.protocol === "https:",
      path: "/",
      // The operator API rejects the token at its fixed expiry; the cookie
      // stops pretending slightly earlier and never outlives 15 minutes.
      maxAge: Math.max(
        1,
        Math.min(exp - Math.floor(Date.now() / 1000), 15 * 60),
      ),
    },
  );
  return res;
}
