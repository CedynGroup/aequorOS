/**
 * End an operator impersonation hand-off: clear both cookies. Idempotent, so a
 * double click or a leave with no active hand-off is a harmless 200.
 */

import { NextResponse } from 'next/server';
import {
  IMPERSONATION_COOKIE,
  IMPERSONATION_MARKER_COOKIE,
} from '@/lib/impersonation-cookies';

export async function POST(): Promise<NextResponse> {
  const response = NextResponse.json({ ok: true });
  response.cookies.delete(IMPERSONATION_COOKIE);
  response.cookies.delete(IMPERSONATION_MARKER_COOKIE);
  return response;
}
