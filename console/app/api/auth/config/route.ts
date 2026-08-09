/**
 * GET /api/auth/config — what sign-in methods this deployment offers, plus
 * the operator API host for the header badge. No secrets: issuer host and
 * API host are deployment facts, not credentials.
 */

import { NextResponse } from 'next/server';
import { oidcEnv, operatorApiUrl } from '@/lib/server-auth';

export const dynamic = 'force-dynamic';

export async function GET() {
  const env = oidcEnv();
  let issuerHost: string | null = null;
  if (env) {
    try {
      issuerHost = new URL(env.issuer).host;
    } catch {
      issuerHost = env.issuer;
    }
  }
  let apiHost: string;
  try {
    apiHost = new URL(operatorApiUrl()).host;
  } catch {
    apiHost = operatorApiUrl();
  }
  return NextResponse.json({
    oidc_configured: env !== null,
    issuer_host: issuerHost,
    api_host: apiHost,
  });
}
