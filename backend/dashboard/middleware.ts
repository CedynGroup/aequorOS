import { NextResponse } from 'next/server';
import { auth } from '@/auth';
import { LOGIN_URL } from '@/lib/loginUrl';

// Gate every matched route behind a session. Unauthenticated visitors are sent
// to the sign-in page — in production that is the ROOT-level
// https://aequoros.com/login (see lib/loginUrl.ts), not the app-local
// /dashboard/login — carrying a callbackUrl back to the page they wanted.
export default auth((req) => {
  if (req.auth?.user) return NextResponse.next();
  const login = new URL(LOGIN_URL, req.nextUrl.origin);
  login.searchParams.set('callbackUrl', req.url);
  return NextResponse.redirect(login);
});

// Protect everything except the login page, the NextAuth routes, and static assets.
export const config = {
  matcher: ['/((?!login|api/auth|_next/static|_next/image|favicon.ico|icon.svg).*)'],
};
