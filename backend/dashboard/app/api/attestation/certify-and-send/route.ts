/**
 * Spend an SSO-obtained signing authorisation on "certify and send", server-side.
 *
 * The sibling `../certify` route exists for the roles that only certify; this
 * one exists because the workspace's act is indivisible — the signature, the
 * field placement it lands in, and the nomination of who signs next commit in
 * one backend transaction, so a nominee the policy refuses takes the
 * certification down with it rather than leaving a certified return in nobody's
 * queue. Splitting that across two calls from the browser would reintroduce
 * exactly the half-finished state the single endpoint removes.
 *
 * The authorisation never reaches the browser: it is written to an HttpOnly
 * cookie by the step-up callback and read here. A script running in the page
 * cannot read it, replay it, or spend it against a different package — the
 * binding to (user, package, digest, role) is enforced by the risk service, and
 * the cookie is consumed on first use.
 */

import { NextResponse, type NextRequest } from 'next/server';
import { auth } from '@/auth';
import { apiBase, takeAuthorizationCookie } from '@/lib/attestation/stepUp';

interface Recipient {
  signingRole?: string;
  userId?: string;
}

interface Placement {
  signingRole?: string;
  pageIndex?: number;
  x1?: number;
  y1?: number;
  x2?: number;
  y2?: number;
}

interface CertifyAndSendBody {
  bankId?: string;
  packageId?: string;
  signingRole?: string;
  expectedCertificationDigest?: string;
  recipients?: Recipient[];
  placements?: Placement[];
  reason?: string;
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  const session = await auth();
  if (!session?.accessToken) {
    return NextResponse.json({ error: 'not_authenticated' }, { status: 401 });
  }

  let body: CertifyAndSendBody;
  try {
    body = (await request.json()) as CertifyAndSendBody;
  } catch {
    return NextResponse.json({ error: 'invalid_json' }, { status: 400 });
  }
  const { bankId, packageId, signingRole, expectedCertificationDigest, reason } = body;
  if (!bankId || !packageId || !signingRole || !expectedCertificationDigest || !reason) {
    return NextResponse.json(
      {
        error:
          'bankId, packageId, signingRole, expectedCertificationDigest and reason are required',
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
    `${apiBase()}/banks/${bankId}/regulatory-packages/${packageId}/attestation/certify-and-send`,
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
        reason,
        recipients: (body.recipients ?? []).map((recipient) => ({
          signing_role: recipient.signingRole,
          user_id: recipient.userId,
        })),
        placements: (body.placements ?? []).map((placement) => ({
          signing_role: placement.signingRole,
          page_index: placement.pageIndex,
          x1: placement.x1,
          y1: placement.y1,
          x2: placement.x2,
          y2: placement.y2,
        })),
      }),
      cache: 'no-store',
    },
  );

  // Pass the risk service's own verdict through unchanged — the workspace
  // branches on its error codes (maker_checker, recipient_role_insufficient,
  // placement_too_small, figures_changed_since_certification, …).
  const payload: unknown = await response.json().catch(() => null);
  return NextResponse.json(payload ?? { error: 'certify_failed' }, {
    status: response.status,
  });
}
