/**
 * Optimistic route protection: a request without a session cookie is sent to
 * sign-in before anything renders. The real check (decrypting the session and
 * the API verifying the token) happens in the data layer on every read.
 */

import { NextResponse, type NextRequest } from "next/server";

const PUBLIC = ["/sign-in", "/auth/"];

export function proxy(req: NextRequest) {
  const { pathname, search } = req.nextUrl;
  if (PUBLIC.some((p) => pathname === p || pathname.startsWith(p))) return NextResponse.next();
  if (req.cookies.has("srg_session")) return NextResponse.next();
  const url = new URL("/sign-in", req.url);
  if (pathname !== "/") url.searchParams.set("next", pathname + search);
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|icon.svg).*)"],
};
