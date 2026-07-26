/**
 * Spend an SSO-obtained signing authorisation, server-side.
 *
 * This proxy exists so the authorisation never reaches the browser: it is
 * written to an HttpOnly cookie by the step-up callback and read here. A script
 * running in the page therefore cannot read it, replay it, or spend it against a
 * different package — the binding to (user, package, digest, role) is enforced
 * by the risk service, and the cookie is consumed on first use.
 *
 * The password step-up path does not need this proxy: there the client already
 * holds the authorisation for the length of one call. This route is specifically
 * the SSO completion.
 */

import { NextResponse, type NextRequest } from 'next/server';
import { auth } from '@/auth';
import { apiBase, takeAuthorizationCookie } from '@/lib/attestation/stepUp';

interface CertifyBody {
  bankId?: string;
  packageId?: string;
  signingRole?: string;
  expectedCertificationDigest?: string;
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  const session = await auth();
  if (!session?.accessToken) {
    return NextResponse.json({ error: 'not_authenticated' }, { status: 401 });
  }

  let body: CertifyBody;
  try {
    body = (await request.json()) as CertifyBody;
  } catch {
    return NextResponse.json({ error: 'invalid_json' }, { status: 400 });
  }
  const { bankId, packageId, signingRole, expectedCertificationDigest } = body;
  if (!bankId || !packageId || !signingRole || !expectedCertificationDigest) {
    return NextResponse.json(
      {
        error:
          'bankId, packageId, signingRole and expectedCertificationDigest are required',
      },
      { status: 400 },
    );
  }

  // Single use: taking it also clears it, so a failed certify cannot be retried
  // with the same authorisation. Re-authentication is the correct recovery.
  const authorization = await takeAuthorizationCookie();
  if (!authorization) {
    return NextResponse.json(
      {
        error: 'step_up_required',
        message:
          'No signing authorisation is held. Re-authenticate with your institution to sign.',
      },
      { status: 401 },
    );
  }

  const response = await fetch(
    `${apiBase()}/banks/${bankId}/regulatory-packages/${packageId}/attestation/certify`,
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${session.accessToken}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        signing_role: signingRole,
        authorization_token: authorization,
        expected_certification_digest: expectedCertificationDigest,
      }),
      cache: 'no-store',
    },
  );

  // Pass the risk service's own verdict through unchanged — the dialog branches
  // on its error codes (maker_checker, figures_changed_since_certification, …).
  const payload: unknown = await response.json().catch(() => null);
  return NextResponse.json(payload ?? { error: 'certify_failed' }, {
    status: response.status,
  });
}
