/**
 * /api/op/* — same-origin proxy to the operator API.
 *
 * Every console API call goes through here, in both auth modes:
 * - workforce OIDC: the id_token from the HttpOnly session cookie becomes the
 *   bearer — browser JavaScript never holds the credential;
 * - dev token: the browser's own Authorization header is forwarded verbatim.
 *
 * The proxy adds no authorization of its own — the operator API verifies the
 * bearer (dev token or id_token signature/issuer/audience/domain) on every
 * request. Only /operator/* paths are forwardable, so the console cannot be
 * used as an open relay to arbitrary backend routes.
 */

import { NextResponse, type NextRequest } from 'next/server';
import { operatorApiUrl, readSession } from '@/lib/server-auth';

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
  if (incomingAuth) {
    headers.Authorization = incomingAuth; // dev-token mode
  } else if (session) {
    headers.Authorization = `Bearer ${session.id_token}`; // workforce mode
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

  const body = await upstream.text();
  return new NextResponse(body, {
    status: upstream.status,
    headers: {
      'Content-Type': upstream.headers.get('content-type') ?? 'application/json',
    },
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
