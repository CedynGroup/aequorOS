/**
 * /api/op/* — same-origin proxy to the operator API.
 *
 * Every console API call goes through here, in all auth modes:
 * - password (primary): the operator session JWT from the HttpOnly session
 *   cookie becomes the bearer — browser JavaScript never holds the credential;
 * - workforce OIDC (secondary): same cookie mechanics with the id_token; the
 *   backend requires a matching active operator_users row and uses its role;
 * - dev token (local only): the browser's own Authorization header is
 *   forwarded verbatim.
 *
 * The proxy adds no authorization of its own — the operator API verifies the
 * bearer (dev token, operator JWT, or id_token signature/issuer/audience/domain
 * plus active operator row) on every request. Only /operator/* paths are
 * forwardable, so the console cannot be used as an open relay to arbitrary
 * backend routes.
 */

import { NextResponse, type NextRequest } from 'next/server';
import { operatorApiUrl, readSession, sessionBearer } from '@/lib/server-auth';

export const dynamic = 'force-dynamic';

async function forward(req: NextRequest, path: string[]) {
  const joined = path.join('/');
  if (!joined.startsWith('operator/')) {
    return NextResponse.json(
      { error: { code: 'not_proxied', message: 'Only /operator paths are proxied.' } },
      { status: 404 },
    );
  }

  const target = `${operatorApiUrl()}/${joined}${req.nextUrl.search}`;
  const headers: Record<string, string> = { Accept: 'application/json' };

  const incomingAuth = req.headers.get('authorization');
  const session = readSession(req);
  const bearer = session ? sessionBearer(session) : null;
  if (incomingAuth) {
    headers.Authorization = incomingAuth; // dev-token mode
  } else if (bearer) {
    // Cookie session: operator JWT (password mode) or OIDC id_token (SSO),
    // forwarded identically — the operator API verifies either.
    headers.Authorization = `Bearer ${bearer}`;
  }

  const contentType = req.headers.get('content-type');
  if (contentType) headers['Content-Type'] = contentType;

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: req.method,
      headers,
      body: req.method === 'GET' || req.method === 'HEAD' ? undefined : await req.text(),
      cache: 'no-store',
    });
  } catch {
    return NextResponse.json(
      {
        error: {
          code: 'operator_api_unreachable',
          message: `The console could not reach the operator API at ${operatorApiUrl()}.`,
        },
      },
      { status: 502 },
    );
  }

  const body = await upstream.arrayBuffer();
  const responseHeaders: Record<string, string> = {
    'Content-Type': upstream.headers.get('content-type') ?? 'application/json',
  };
  const contentDisposition = upstream.headers.get('content-disposition');
  if (contentDisposition) responseHeaders['Content-Disposition'] = contentDisposition;
  const cacheControl = upstream.headers.get('cache-control');
  if (cacheControl) responseHeaders['Cache-Control'] = cacheControl;
  return new NextResponse(body, {
    status: upstream.status,
    headers: responseHeaders,
  });
}

export async function GET(req: NextRequest, ctx: { params: { path: string[] } }) {
  return forward(req, ctx.params.path);
}

export async function POST(req: NextRequest, ctx: { params: { path: string[] } }) {
  return forward(req, ctx.params.path);
}

export async function PUT(req: NextRequest, ctx: { params: { path: string[] } }) {
  return forward(req, ctx.params.path);
}

export async function PATCH(req: NextRequest, ctx: { params: { path: string[] } }) {
  return forward(req, ctx.params.path);
}

export async function DELETE(req: NextRequest, ctx: { params: { path: string[] } }) {
  return forward(req, ctx.params.path);
}
