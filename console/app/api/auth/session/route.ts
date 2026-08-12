/**
 * GET /api/auth/session — who is signed in via the op_session cookie, if
 * anyone, and through which mode ('password' | 'oidc'). Returns derived
 * facts only; the credential (operator JWT or id_token) never leaves the
 * HttpOnly cookie.
 */

import { NextResponse, type NextRequest } from 'next/server';
import { readSession, sessionMode } from '@/lib/server-auth';

export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const session = readSession(req);
  if (!session) {
    return NextResponse.json({
      authenticated: false,
      email: null,
      expires_at: null,
      mode: null,
    });
  }
  return NextResponse.json({
    authenticated: true,
    email: session.email,
    expires_at: new Date(session.exp * 1000).toISOString(),
    mode: sessionMode(session),
  });
}
