/**
 * GET /api/auth/session — who is signed in via workforce OIDC, if anyone.
 * Returns claims-derived facts only; the id_token itself never leaves the
 * HttpOnly cookie.
 */

import { NextResponse, type NextRequest } from 'next/server';
import { readSession } from '@/lib/server-auth';

export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const session = readSession(req);
  if (!session) {
    return NextResponse.json({ authenticated: false, email: null, expires_at: null });
  }
  return NextResponse.json({
    authenticated: true,
    email: session.email,
    expires_at: new Date(session.exp * 1000).toISOString(),
  });
}
