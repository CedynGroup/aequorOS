/**
 * e2e credential minting (plan W7.5).
 *
 * Mints a backend access token (HS256, same claims create_token issues) and
 * wraps it in a NextAuth session cookie, exactly like the production login
 * flow would — no login forms, no real credentials anywhere in the repo.
 * Secrets here are e2e-only values injected into both dev servers by
 * playwright.config.ts.
 */

import { SignJWT } from 'jose';
import { encode } from '@auth/core/jwt';
import { mkdirSync, writeFileSync } from 'fs';
import path from 'path';

export const E2E_ORG_ID = 'OR-DEM00001';
export const E2E_JWT_SECRET = 'e2e-backend-jwt-secret-not-production-000';
export const E2E_AUTH_SECRET = 'e2e-nextauth-secret-not-production-000';

export const E2E_USERS: Record<string, { id: string; roles: string[] }> = {
  admin: { id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', roles: ['admin'] },
  approver: { id: 'eeeeeeee-2222-4eee-8eee-eeeeeeeeeee2', roles: ['approver'] },
  analyst: { id: 'eeeeeeee-3333-4eee-8eee-eeeeeeeeeee3', roles: ['analyst'] },
  viewer: { id: 'eeeeeeee-4444-4eee-8eee-eeeeeeeeeee4', roles: ['viewer'] },
};

export async function mintBackendToken(role: keyof typeof E2E_USERS): Promise<string> {
  const user = E2E_USERS[role];
  const secret = new TextEncoder().encode(E2E_JWT_SECRET);
  return new SignJWT({
    org: E2E_ORG_ID,
    roles: user.roles,
    type: 'access',
    email: `e2e.${String(role)}@aequoros.example`,
    name: `E2E ${String(role)}`,
  })
    .setProtectedHeader({ alg: 'HS256' })
    .setSubject(user.id)
    .setIssuer('aequoros')
    .setAudience('aequoros-api')
    .setIssuedAt()
    .setExpirationTime('2h')
    .sign(secret);
}

export async function mintSessionCookie(role: keyof typeof E2E_USERS): Promise<string> {
  const user = E2E_USERS[role];
  const accessToken = await mintBackendToken(role);
  return encode({
    token: {
      sub: user.id,
      name: `E2E ${String(role)}`,
      email: `e2e.${String(role)}@aequoros.example`,
      accessToken,
      refreshToken: accessToken,
      accessTokenExpires: Date.now() + 2 * 60 * 60 * 1000,
      organizationId: E2E_ORG_ID,
      roles: user.roles,
    },
    secret: E2E_AUTH_SECRET,
    salt: 'authjs.session-token',
    maxAge: 2 * 60 * 60,
  });
}

/** Playwright storageState with the session cookie + tour-done flag. */
export async function writeStorageState(
  role: keyof typeof E2E_USERS,
  baseURL: string,
  outDir: string
): Promise<string> {
  const cookie = await mintSessionCookie(role);
  const { hostname, origin } = new URL(baseURL);
  const state = {
    cookies: [
      {
        name: 'authjs.session-token',
        value: cookie,
        domain: hostname,
        path: '/',
        expires: Math.floor(Date.now() / 1000) + 2 * 60 * 60,
        httpOnly: true,
        secure: false,
        sameSite: 'Lax' as const,
      },
    ],
    origins: [
      {
        origin,
        localStorage: [{ name: 'aeq-tour-done', value: '1' }],
      },
    ],
  };
  mkdirSync(outDir, { recursive: true });
  const file = path.join(outDir, `${String(role)}.json`);
  writeFileSync(file, JSON.stringify(state));
  return file;
}
