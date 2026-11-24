/** GET /auth/expired — the API no longer accepts the session: clear it and sign in again. */

import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE } from "@/lib/server/config";

export const dynamic = "force-dynamic";

export function GET(req: NextRequest) {
  const res = NextResponse.redirect(new URL("/sign-in?error=session_expired", req.url), 303);
  res.cookies.set(SESSION_COOKIE, "", { path: "/", maxAge: 0 });
  return res;
}
