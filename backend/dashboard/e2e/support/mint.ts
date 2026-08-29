/**
 * e2e credential minting (plan W7.5).
 *
 * Mints a backend access token (HS256, same claims create_token issues) and
 * wraps it in a NextAuth session cookie, exactly like the production login
 * flow would — no login forms, no real credentials anywhere in the repo.
 * Secrets here are e2e-only values injected into both dev servers by
 * playwright.config.ts.
 */

import { SignJWT } from "jose";
import { encode } from "@auth/core/jwt";
import { mkdirSync, writeFileSync } from "fs";
import path from "path";

export const E2E_ORG_ID = "OR-DEM00001";
export const E2E_JWT_SECRET = "e2e-backend-jwt-secret-not-production-000";
export const E2E_AUTH_SECRET = "e2e-nextauth-secret-not-production-000";

/**
 * The step-up password every e2e signer re-authenticates with.
 *
 * Signing requires proof of presence NOW, and a minted session token carries no
 * password behind it — so `scripts/e2e_bootstrap.py` gives each fixture user this
 * hash and the ceremony runs for real instead of being skipped or unlocked by
 * relaxing the signing policy. The two values must agree; the constant lives in
 * both files rather than in shared config because the bootstrap runs in Python
 * before this process reads anything.
 */
export const E2E_PASSWORD = "e2e-step-up-password-not-production-000";

export const E2E_USERS: Record<string, { id: string; roles: string[]; authv: number }> = {
  // Initial-owner assignment is the sole bootstrap authorization mutation, so
  // only admin starts at version 2. Grant journeys pass later versions explicitly.
  admin: { id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", roles: ["admin"], authv: 2 },
  approver: { id: "eeeeeeee-2222-4eee-8eee-eeeeeeeeeee2", roles: ["approver"], authv: 1 },
  analyst: { id: "eeeeeeee-3333-4eee-8eee-eeeeeeeeeee3", roles: ["analyst"], authv: 1 },
  viewer: { id: "eeeeeeee-4444-4eee-8eee-eeeeeeeeeee4", roles: ["viewer"], authv: 1 },
  grant_member: { id: "eeeeeeee-5555-4eee-8eee-eeeeeeeeeee5", roles: ["viewer"], authv: 1 },
};

export async function mintBackendToken(
  role: keyof typeof E2E_USERS,
  authorizationVersion?: number,
): Promise<string> {
  const user = E2E_USERS[role];
  const secret = new TextEncoder().encode(E2E_JWT_SECRET);
  return new SignJWT({
    org: E2E_ORG_ID,
    roles: user.roles,
    type: "access",
    authv: authorizationVersion ?? user.authv,
    email: `e2e.${String(role)}@aequoros.example`,
    name: `E2E ${String(role)}`,
  })
    .setProtectedHeader({ alg: "HS256" })
    .setSubject(user.id)
    .setIssuer("aequoros")
    .setAudience("aequoros-api")
    .setIssuedAt()
    .setExpirationTime("2h")
    .sign(secret);
}

export async function mintSessionCookie(
  role: keyof typeof E2E_USERS,
  authorizationVersion?: number,
): Promise<string> {
  const user = E2E_USERS[role];
  const accessToken = await mintBackendToken(role, authorizationVersion);
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
    salt: "authjs.session-token",
    maxAge: 2 * 60 * 60,
  });
}

/** Playwright storageState with the session cookie + tour-done flag. */
export async function writeStorageState(
  role: keyof typeof E2E_USERS,
  baseURL: string,
  outDir: string,
): Promise<string> {
  const cookie = await mintSessionCookie(role);
  const { hostname, origin } = new URL(baseURL);
  const state = {
    cookies: [
      {
        name: "authjs.session-token",
        value: cookie,
        domain: hostname,
        path: "/",
        expires: Math.floor(Date.now() / 1000) + 2 * 60 * 60,
        httpOnly: true,
        secure: false,
        sameSite: "Lax" as const,
      },
    ],
    origins: [
      {
        origin,
        localStorage: [{ name: "aeq-tour-done", value: "1" }],
      },
    ],
  };
  mkdirSync(outDir, { recursive: true });
  const file = path.join(outDir, `${String(role)}.json`);
  writeFileSync(file, JSON.stringify(state));
  return file;
}
