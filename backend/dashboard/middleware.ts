import { NextRequest, NextResponse } from "next/server";
import type { NextFetchEvent } from "next/server";
import { auth } from "@/auth";
import { LOGIN_URL } from "@/lib/loginUrl";
import { IMPERSONATION_COOKIE } from "@/lib/impersonation-cookies";
import {
  authSessionCookieNamesToClear,
  expiredAuthSessionCookieHeaders,
  presentAuthSessionCookieNames,
} from "@/lib/authCookies";
import { requestOrigin } from "@/lib/requestOrigin";

function expireSessionCookies(response: Response, names: string[]): void {
  for (const cookie of expiredAuthSessionCookieHeaders(names)) {
    response.headers.append("set-cookie", cookie);
  }
}

function copySetCookies(target: Response, source: Response): void {
  for (const cookie of source.headers.getSetCookie()) {
    target.headers.append("set-cookie", cookie);
  }
}

// Gate every matched route behind a session. Unauthenticated visitors are sent
// to the sign-in page — in production that is the ROOT-level
// https://aequoros.com/login (see lib/loginUrl.ts), not the app-local
// /dashboard/login — carrying a callbackUrl back to the page they wanted.
//
// NextAuth is initialized LAZILY (auth.ts builds the SSO provider per request),
// which makes `auth` async: wrapping a middleware yields a PROMISE of the
// handler. Exporting that promise directly breaks Next ("must export a
// middleware or a default function"), so resolve it inside a real function.
type AuthenticatedRequest = NextRequest & {
  auth?: { user?: unknown } | null;
};
type AuthGateState = { request?: AuthenticatedRequest };

const sessionReader = (state: AuthGateState) =>
  auth((req) => {
    state.request = req;
    return NextResponse.next({ request: { headers: req.headers } });
  }) as unknown as Promise<
    (req: NextRequest, event: NextFetchEvent) => Promise<Response | undefined>
  >;

export default async function middleware(
  req: NextRequest,
  event: NextFetchEvent,
) {
  const origin = requestOrigin(req);
  const state: AuthGateState = {};
  const sessionResponse =
    (await (await sessionReader(state))(req, event)) ?? NextResponse.next();
  const authRequest = state.request;
  if (!authRequest) return sessionResponse;

  const cookieHeader = req.headers.get("cookie");
  const presentSessionCookies = presentAuthSessionCookieNames(cookieHeader);
  const sessionCookieNames =
    presentSessionCookies.length > 0
      ? authSessionCookieNamesToClear(cookieHeader)
      : [];
  if (req.nextUrl.pathname === "/login") {
    if (!authRequest.auth?.user) {
      expireSessionCookies(sessionResponse, sessionCookieNames);
    }
    return sessionResponse;
  }
  if (authRequest.auth?.user) {
    return sessionResponse;
  }
  // Act-as-examiner (additive): an operator inspecting a tenant has no NextAuth
  // session — only the HttpOnly hand-off cookie. Let them through on its
  // presence; the tenant API still serves read-only (examiner) and 403s every
  // mutation. Absent → identical to before: redirect to sign-in.
  if (req.cookies.get(IMPERSONATION_COOKIE)) {
    return sessionResponse;
  }
  const login = new URL(LOGIN_URL, origin);
  const callbackUrl = new URL(
    `${req.nextUrl.pathname}${req.nextUrl.search}`,
    origin,
  );
  login.searchParams.set("callbackUrl", callbackUrl.href);
  const response = new Response(null, {
    status: 307,
    headers: { location: login.href },
  });
  copySetCookies(response, sessionResponse);
  expireSessionCookies(response, sessionCookieNames);
  return response;
}

// Protect everything except the NextAuth routes, the operator inspection
// hand-off (its accept page + API run before any cookie/session exists), and
// static assets. /login stays matched so stale session variants are cleared
// while the public page renders normally.
export const config = {
  matcher: [
    "/((?!inspect|api/auth|api/impersonation|_next/static|_next/image|branding|favicon.ico|icon.svg).*)",
  ],
};
