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
import identities from "./identities.json";

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

export const E2E_USERS: Record<
  string,
  { id: string; roles: string[]; authv: number; organizationId?: string }
> = {
  // Bootstrap gives every human baseline membership. Admin then receives two
  // initial-ownership grants (owner and read access) and an organization-wide
  // Analyst grant, each advancing authv once.
  // The exact Liquidity-only fixture grant belongs to liquidity_viewer below.
  admin: {
    id: identities.bootstrap.admin,
    roles: ["admin"],
    authv: 5,
  },
  approver: {
    id: identities.bootstrap.approver,
    roles: ["approver"],
    authv: 3,
  },
  analyst: {
    id: identities.bootstrap.analyst,
    roles: ["analyst"],
    authv: 3,
  },
  fresh_forecast_analyst: {
    ...identities.journey.fresh_forecast_analyst,
    roles: ["analyst"],
    authv: 3,
  },
  viewer: {
    id: identities.bootstrap.viewer,
    roles: ["viewer"],
    authv: 2,
  },
  fx_member: {
    id: identities.bootstrap.fx_member,
    roles: ["viewer"],
    authv: 2,
  },
  access_request_member: {
    id: identities.bootstrap.access_request_member,
    roles: ["viewer"],
    authv: 2,
  },
  access_extra_member: {
    id: identities.bootstrap.access_extra_member,
    roles: ["viewer"],
    authv: 2,
  },
  grant_member: {
    id: identities.bootstrap.grant_member,
    roles: ["viewer"],
    authv: 2,
  },
  forecast_member: {
    id: identities.bootstrap.forecast_member,
    roles: ["viewer"],
    authv: 2,
  },
  forecast_summary_member: {
    id: identities.bootstrap.forecast_summary_member,
    roles: ["viewer"],
    authv: 2,
  },
  account_admin: {
    id: identities.bootstrap.account_admin,
    roles: ["account_admin"],
    authv: 3,
  },
  integration_admin: {
    id: identities.bootstrap.integration_admin,
    roles: ["account_admin"],
    authv: 4,
  },
  legacy_account_admin: {
    id: identities.bootstrap.legacy_account_admin,
    roles: ["account_admin"],
    authv: 2,
  },
  liquidity_aggregated_viewer: {
    id: identities.bootstrap.liquidity_aggregated_viewer,
    roles: ["viewer"],
    authv: 3,
  },
  liquidity_viewer: {
    id: identities.bootstrap.liquidity_viewer,
    roles: ["viewer"],
    authv: 3,
  },
  // Its own identity since 2026-09-22: this entry carried the `board` UUID
  // (from PR #204), so the bootstrap could not enrol two signing keys for one
  // user and aborted before seeding anything. The ids here and in
  // `scripts/e2e_bootstrap.py` must agree — a cookie minted for the wrong
  // subject authenticates as the other fixture's authority.
  macro_viewer: {
    id: identities.bootstrap.macro_viewer,
    roles: ["viewer"],
    authv: 2,
  },
  invite_fresh: {
    id: identities.bootstrap.invite_fresh,
    roles: ["viewer"],
    authv: 2,
  },
  // A board member. Scalar `viewer` and one exact Capital/confidential
  // APPROVER binding on the sample bank: baseline membership (1) plus that
  // grant (1) on top of the initial version. Holds NO Regulatory Reporting
  // authority, which is the point — the ICAAP filing surface has to give them
  // a signature they could not give through `/submissions`.
  board: {
    id: identities.bootstrap.board,
    roles: ["viewer"],
    authv: 3,
  },
  // The officer who transmits a return to the regulator. Scalar `viewer` on
  // purpose — filing authority is the binding and nothing else — plus two
  // grants on top of baseline membership: the organization-wide read sentence
  // and the Regulatory Reporting / restricted `submit` sentence.
  validator: {
    id: identities.bootstrap.validator,
    roles: ["viewer"],
    authv: 4,
  },
  // The officer the local issuer's linked account maps onto
  // (e2e/sso-sign-in.spec.ts). Same grants as `analyst`; the SSO journeys
  // sign in through the issuer rather than minting this token.
  sso_analyst: {
    id: identities.bootstrap.sso_analyst,
    roles: ["analyst"],
    authv: 3,
  },
};

export const E2E_STORAGE_ROLES = [
  "integration_admin",
  "admin",
  "approver",
  "analyst",
  "viewer",
  "account_admin",
  "legacy_account_admin",
  "liquidity_viewer",
  "liquidity_aggregated_viewer",
  "invite_fresh",
  "board",
  "validator",
] as const satisfies readonly (keyof typeof E2E_USERS)[];

export async function mintBackendToken(
  role: keyof typeof E2E_USERS,
  authorizationVersion?: number,
): Promise<string> {
  const user = E2E_USERS[role];
  const secret = new TextEncoder().encode(E2E_JWT_SECRET);
  return new SignJWT({
    org: user.organizationId ?? E2E_ORG_ID,
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
      organizationId: user.organizationId ?? E2E_ORG_ID,
      roles: user.roles,
      authorizationVersion: authorizationVersion ?? user.authv,
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
  authorizationVersion?: number,
): Promise<string> {
  const cookie = await mintSessionCookie(role, authorizationVersion);
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
