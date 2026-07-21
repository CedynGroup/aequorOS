import { NextResponse } from 'next/server';
import type { NextFetchEvent, NextRequest } from 'next/server';
import { auth } from '@/auth';
import { LOGIN_URL } from '@/lib/loginUrl';

// Gate every matched route behind a session. Unauthenticated visitors are sent
// to the sign-in page — in production that is the ROOT-level
// https://aequoros.com/login (see lib/loginUrl.ts), not the app-local
// /dashboard/login — carrying a callbackUrl back to the page they wanted.
//
// NextAuth is initialized LAZILY (auth.ts builds the SSO provider per request),
// which makes `auth` async: wrapping a middleware yields a PROMISE of the
// handler. Exporting that promise directly breaks Next ("must export a
// middleware or a default function"), so resolve it inside a real function.
const gate = auth((req) => {
  if (req.auth?.user) return NextResponse.next();
  const login = new URL(LOGIN_URL, req.nextUrl.origin);
  login.searchParams.set('callbackUrl', req.url);
  return NextResponse.redirect(login);
}) as unknown as Promise<
  (req: NextRequest, event: NextFetchEvent) => Promise<Response | undefined>
>;

export default async function middleware(req: NextRequest, event: NextFetchEvent) {
  return (await gate)(req, event);
}

// Protect everything except the login page, the NextAuth routes, and static assets.
export const config = {
  matcher: ['/((?!login|api/auth|_next/static|_next/image|favicon.ico|icon.svg).*)'],
};
