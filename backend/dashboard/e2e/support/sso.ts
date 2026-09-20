/**
 * Driving single sign-on through the local issuer (scripts/e2e_idp.py).
 *
 * The issuer is a real OpenID Provider on loopback, registered as the e2e
 * tenant's SSO connection by scripts/e2e_bootstrap.py, so these helpers walk
 * exactly the path a bank officer does: the dashboard's SSO button, the IdP's
 * credential form, the NextAuth callback, the backend's id_token exchange.
 * Nothing is mocked and no session is minted — the accounts and the password
 * below are the issuer's static fixtures, literal in the repo because none of
 * them may ever be real.
 */

import { expect, type Page } from "@playwright/test";
import { E2E_BASE_URL, E2E_IDP_ORIGIN } from "../../playwright.config";

/** Mirrors `PASSWORD` in scripts/e2e_idp.py. */
export const IDP_PASSWORD = "e2e-idp-password-not-production-000";

/** The issuer's static accounts (`ACCOUNTS` in scripts/e2e_idp.py). */
export const IDP_ACCOUNTS = {
  /** Provisioned in the tenant (scripts/e2e_bootstrap.py `sso_analyst`). */
  analyst: "e2e.sso_analyst@aequoros.example",
  /** An allowed-domain identity the tenant never invited. */
  unprovisioned: "e2e.sso_unprovisioned@aequoros.example",
  /** Outside the connection's allowed email domains. */
  outsider: "e2e.sso_outsider@contractor.example",
} as const;

/** Auth.js's default session cookie name on a plain-http origin. */
export const SESSION_COOKIE = "authjs.session-token";

/**
 * Press the login page's SSO button and follow the redirect to the issuer.
 *
 * Resolves once the IdP's credential form is on screen, so a caller can take
 * evidence of the hand-off before authenticating.
 */
export async function beginSsoSignIn(page: Page): Promise<void> {
  await page.goto("/login");
  await page.getByRole("button", { name: /sign in with sso/i }).click();
  await page.waitForURL((url) => url.origin === E2E_IDP_ORIGIN);
  await expect(
    page.getByRole("heading", { name: "E2E Identity Provider" }),
  ).toBeVisible();
}

/** Authenticate at the issuer's form; the IdP then redirects back to the app. */
export async function authenticateAtIssuer(
  page: Page,
  email: string,
): Promise<void> {
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(IDP_PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL((url) => url.origin === E2E_BASE_URL);
}

/**
 * The whole round trip for a provisioned officer: ends signed in on the shell.
 */
export async function signInWithSso(page: Page, email: string): Promise<void> {
  await beginSsoSignIn(page);
  await authenticateAtIssuer(page, email);
  await expect(page.locator('header button[aria-haspopup="menu"]')).toBeVisible(
    { timeout: 30_000 },
  );
}

/** The dashboard session as `/api/auth/session` reports it (null when signed out). */
export async function readSession(
  page: Page,
): Promise<{ accessToken?: string; user?: { email?: string } } | null> {
  const response = await page.request.get("/api/auth/session");
  expect(response.ok()).toBeTruthy();
  return (await response.json()) as {
    accessToken?: string;
    user?: { email?: string };
  } | null;
}
