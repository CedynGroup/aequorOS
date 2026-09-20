// Set E2E_EVIDENCE_DIR to write reviewer-visible screenshots outside version control.
/**
 * Single sign-on through a real identity provider — the browser leg.
 *
 * The backend suite proves the id_token exchange (`tests/api/test_auth.py`)
 * and the egress guard around discovery (`tests/core/test_oidc_discovery_ssrf.py`).
 * What only a browser can prove is the round trip itself: the login page's
 * SSO button, the redirect to the institution's IdP, its credential form, the
 * NextAuth callback with its PKCE cookie, the `/auth/sso` exchange, and the
 * session that results — plus the same redirect used as attestation step-up,
 * where the signer must come back to the ceremony they left, with the
 * authorisation held server-side and the markers consumed.
 *
 * The IdP is the local issuer playwright.config.ts starts
 * (scripts/e2e_idp.py), registered as the tenant's SSO connection during
 * bootstrap. It keeps no session of its own, so every authorization request —
 * including the step-up's `prompt=login` — is a fresh authentication with a
 * fresh `auth_time`.
 */

import { expect, test, type Page } from "@playwright/test";
import path from "path";
import {
  E2E_API_ORIGIN,
  E2E_BASE_URL,
  E2E_IDP_ORIGIN,
} from "../playwright.config";
import {
  adoptTypedMark,
  placeBothSignatureFields,
  returnsUrl,
} from "./support/ceremony";
import { mintBackendToken } from "./support/mint";
import { requireObjectStorage } from "./support/object-storage";
import {
  IDP_ACCOUNTS,
  SESSION_COOKIE,
  authenticateAtIssuer,
  beginSsoSignIn,
  readSession,
  signInWithSso,
} from "./support/sso";

const API = `${E2E_API_ORIGIN}/api/v1`;
const SAMPLE_BANK_ID = "BK-SAMP0001";
const ORG_ID = "OR-DEM00001";
const evidenceDir = process.env.E2E_EVIDENCE_DIR;
/** Auth.js's one-shot round-trip cookies; none may outlive its callback. */
const ROUND_TRIP_COOKIE = /^authjs\.(state|pkce\.code_verifier|nonce)$/;

async function evidence(page: Page, name: string): Promise<void> {
  if (!evidenceDir) return;
  await page.screenshot({ path: path.join(evidenceDir, `${name}.png`) });
}

async function api(
  token: string,
  method: string,
  pathName: string,
  body?: unknown,
): Promise<any> {
  const response = await fetch(`${API}${pathName}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    throw new Error(
      `${method} ${pathName} -> ${response.status}: ${await response.text()}`,
    );
  }
  return response.json();
}

test.describe("single sign-on", () => {
  // Fresh browser context, no minted session: the whole point is signing in.
  test.use({ storageState: { cookies: [], origins: [] } });

  test("an invited officer signs in through the institution's identity provider", async ({
    page,
    context,
  }) => {
    // Where NextAuth sends the IdP, and where the IdP sends the browser back,
    // are both observed on the wire rather than inferred from the final page.
    const authorizeRequests: URL[] = [];
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.origin === E2E_IDP_ORIGIN && url.pathname === "/authorize") {
        authorizeRequests.push(url);
      }
    });
    const issuerCookies: string[] = [];
    page.on("response", async (response) => {
      if (new URL(response.url()).origin !== E2E_IDP_ORIGIN) return;
      const setCookie = (await response.allHeaders())["set-cookie"];
      if (setCookie) issuerCookies.push(setCookie);
    });

    await beginSsoSignIn(page);
    await evidence(page, "sso-issuer-login");

    // The relying party asked for a code, with PKCE, to be returned to the
    // dashboard's own callback — never anywhere else.
    const authorize = authorizeRequests[0];
    expect(authorize).toBeDefined();
    expect(authorize.searchParams.get("response_type")).toBe("code");
    expect(authorize.searchParams.get("code_challenge_method")).toBe("S256");
    expect(authorize.searchParams.get("code_challenge")).toBeTruthy();
    expect(authorize.searchParams.get("redirect_uri")).toBe(
      `${E2E_BASE_URL}/api/auth/callback/sso`,
    );

    await authenticateAtIssuer(page, IDP_ACCOUNTS.analyst);
    await expect(
      page.locator('header button[aria-haspopup="menu"]'),
    ).toBeVisible({ timeout: 30_000 });
    expect(new URL(page.url()).origin).toBe(E2E_BASE_URL);
    await evidence(page, "sso-signed-in-shell");

    // The session lives on the dashboard host, HttpOnly, and nowhere else: the
    // issuer set no cookie at all, and the one-shot PKCE cookie was consumed by
    // the callback rather than left behind to go stale.
    const cookies = await context.cookies();
    const session = cookies.filter((cookie) => cookie.name === SESSION_COOKIE);
    expect(session).toHaveLength(1);
    expect(session[0]).toMatchObject({
      domain: new URL(E2E_BASE_URL).hostname,
      path: "/",
      httpOnly: true,
    });
    expect(issuerCookies).toEqual([]);
    expect(
      cookies.filter((cookie) => ROUND_TRIP_COOKIE.test(cookie.name)),
    ).toEqual([]);

    // The identity the platform now holds is the SSO-linked one: the backend
    // resolved the IdP's subject to the pre-provisioned officer and recorded
    // the account as OIDC-authenticated.
    const dashboardSession = await readSession(page);
    expect(dashboardSession?.user?.email).toBe(IDP_ACCOUNTS.analyst);
    expect(dashboardSession?.accessToken).toBeTruthy();
    const me = await page.request.get(`${API}/auth/me`, {
      headers: { Authorization: `Bearer ${dashboardSession?.accessToken}` },
    });
    expect(me.ok()).toBeTruthy();
    expect(await me.json()).toMatchObject({
      email: IDP_ACCOUNTS.analyst,
      auth_provider: "oidc",
      organization_id: ORG_ID,
    });

    // And the shell shows who signed in.
    await page.locator('header button[aria-haspopup="menu"]').click();
    await expect(page.getByText(IDP_ACCOUNTS.analyst)).toBeVisible();
  });

  for (const [label, email, shot] of [
    [
      "an identity the institution never provisioned",
      IDP_ACCOUNTS.unprovisioned,
      "sso-unprovisioned-identity",
    ],
    [
      "an identity outside the allowed email domains",
      IDP_ACCOUNTS.outsider,
      "sso-outside-allowed-domains",
    ],
  ] as const) {
    test(`${label} is refused on the login page, with no session`, async ({
      page,
      context,
    }) => {
      await beginSsoSignIn(page);
      await authenticateAtIssuer(page, email);
      // The IdP vouched for the identity; the platform refused it. The refusal
      // is an honest message on the dashboard's own login page — not a
      // generic server-error page, and never a session.
      await expect(page).toHaveURL(/\/login\?/);
      await expect(
        page.getByText(/single sign-on could not complete/i),
      ).toBeVisible();
      await evidence(page, shot);
      expect(await readSession(page)).toBeNull();
      expect(
        (await context.cookies()).filter(
          (cookie) => cookie.name === SESSION_COOKIE,
        ),
      ).toEqual([]);
      await page.goto("/");
      await expect(page).toHaveURL(/\/login/);
    });
  }

  test("signing out and signing in again through SSO works from a clean slate", async ({
    page,
    context,
  }) => {
    await signInWithSso(page, IDP_ACCOUNTS.analyst);
    await page.locator('header button[aria-haspopup="menu"]').click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/login/);
    expect(await readSession(page)).toBeNull();
    expect(
      (await context.cookies()).filter(
        (cookie) => cookie.name === SESSION_COOKIE,
      ),
    ).toEqual([]);

    // A second sign-in mints a new PKCE verifier and consumes it again;
    // nothing from the first round trip may interfere.
    await signInWithSso(page, IDP_ACCOUNTS.analyst);
    expect((await readSession(page))?.user?.email).toBe(IDP_ACCOUNTS.analyst);
    expect(
      (await context.cookies()).filter((cookie) =>
        ROUND_TRIP_COOKIE.test(cookie.name),
      ),
    ).toEqual([]);
  });
});

/**
 * The same redirect as proof of presence for a signature.
 *
 * Step-up is a full navigation away from the ceremony and back, so the only
 * thing that can prove it is a browser: that the signer returns to the SAME
 * return with the workspace reopened, that the outcome marker is consumed
 * from the address bar, that the authorisation is held server-side and spent
 * by the certification, and — when the institution has no SSO — that the
 * signer is told so honestly and offered the password path instead.
 */
test.describe("attestation step-up through single sign-on", () => {
  test.use({ storageState: { cookies: [], origins: [] } });
  test.describe.configure({ timeout: 300_000 });

  const RETURN_CODE = "LCR-NSFR";
  /** The reporting period this journey owns (indices 0..4 belong to the lifecycle specs). */
  const OWN_PERIOD_INDEX = 5;
  let date = "";
  let adminToken = "";
  let accountAdminToken = "";
  /** Restored verbatim on teardown; the SSO-off leg disables it mid-journey. */
  let connection: Record<string, unknown> = {};

  test.beforeAll(async () => {
    requireObjectStorage();
    adminToken = await mintBackendToken("admin");
    accountAdminToken = await mintBackendToken("account_admin");
    const listing = await api(
      adminToken,
      "GET",
      `/banks/${SAMPLE_BANK_ID}/reporting-periods`,
    );
    const period = listing.periods[OWN_PERIOD_INDEX];
    date = String(period.period_end).slice(0, 10);
    await api(adminToken, "POST", `/banks/${SAMPLE_BANK_ID}/regulatory-runs`, {
      module: "liquidity",
      reporting_period_id: period.id,
      scenario_code: "baseline",
    });
    connection = await api(accountAdminToken, "GET", "/auth/sso/connection");
  });

  /** Toggle the tenant's SSO connection through the account-administration API. */
  async function setSsoEnabled(enabled: boolean): Promise<void> {
    await api(accountAdminToken, "PUT", "/auth/sso/connection", {
      issuer: connection.issuer,
      client_id: connection.client_id,
      allowed_email_domains: connection.allowed_email_domains,
      jit_enabled: connection.jit_enabled,
      enabled,
    });
  }

  test.afterAll(async () => {
    if (accountAdminToken) await setSsoEnabled(true);
  });

  test("the signer returns to the same ceremony, authorised, and certifies without a password", async ({
    page,
  }) => {
    await signInWithSso(page, IDP_ACCOUNTS.analyst);

    await page.goto(returnsUrl(RETURN_CODE, date));
    await page
      .getByRole("button", { name: /generate package|regenerate/i })
      .first()
      .click();
    const validate = page.getByRole("button", {
      name: "Validate",
      exact: true,
    });
    await expect(validate).toBeEnabled();
    await validate.click();
    await expect(page.getByText(/\bValidated\b/).first()).toBeVisible();

    const certify = page.getByRole("button", { name: "Certify and freeze" });
    await expect(certify).toBeEnabled({ timeout: 30_000 });
    await certify.click();
    const workspace = page.getByTestId("signing-workspace");
    await expect(workspace).toBeVisible({ timeout: 30_000 });
    await placeBothSignatureFields(page, workspace);
    await adoptTypedMark(workspace);
    await workspace
      .getByLabel("Approver recipient")
      .selectOption({ label: "E2E Approver (approver)" });
    const ssoStepUp = workspace.getByRole("button", {
      name: /re-authenticate with single sign-on/i,
    });

    // --- SSO off: the signer is told, on the same return, and nothing signed --
    //
    // The institution can switch its connection off at any time; a signer who
    // presses the SSO button afterwards has to land back here with a reason,
    // not on a JSON error page. This is the inverse of the round trip below,
    // proved against the same package so the two cannot drift apart.
    await setSsoEnabled(false);
    await Promise.all([
      page.waitForURL(/\/submissions\/returns/, { timeout: 30_000 }),
      ssoStepUp.click(),
    ]);
    await expect(
      page.getByText(/Re-authentication did not complete/i),
    ).toBeVisible({ timeout: 30_000 });
    await expect(
      page.getByText(/not configured for this institution/i),
    ).toBeVisible();
    expect(page.url()).not.toContain("stepUp=");
    await expect(page.getByText("Preparer certified")).toHaveCount(0);
    await setSsoEnabled(true);

    // --- SSO on: out to the IdP, fresh authentication, back with authority ---
    //
    // The workspace reopened where the signer left it: the boxes were saved
    // before the redirect, so nothing is re-placed. The nominee is parked only
    // for a successful round trip, so after a refusal the signer names them
    // again — the server re-validates every nominee regardless.
    await expect(workspace).toBeVisible({ timeout: 30_000 });
    await expect(
      workspace.getByText(/Place a signature field for/i),
    ).toHaveCount(0);
    await workspace
      .getByLabel("Approver recipient")
      .selectOption({ label: "E2E Approver (approver)" });
    const stepUpStarts: URL[] = [];
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.origin === E2E_IDP_ORIGIN && url.pathname === "/authorize") {
        stepUpStarts.push(url);
      }
    });
    await Promise.all([
      page.waitForURL((url) => url.origin === E2E_IDP_ORIGIN, {
        timeout: 30_000,
      }),
      ssoStepUp.click(),
    ]);
    // Step-up demands a FRESH authentication, and the callback belongs to the
    // dashboard — the ceremony can only ever resume on its own origin.
    const start = stepUpStarts[0];
    expect(start.searchParams.get("prompt")).toBe("login");
    expect(start.searchParams.get("max_age")).toBe("0");
    expect(start.searchParams.get("code_challenge_method")).toBe("S256");
    expect(start.searchParams.get("redirect_uri")).toBe(
      `${E2E_BASE_URL}/api/attestation/step-up/callback`,
    );
    await authenticateAtIssuer(page, IDP_ACCOUNTS.analyst);

    // Back on the same return, workspace open, identity confirmed — and the
    // markers consumed, so a reload cannot reopen a ceremony or replay an
    // outcome.
    await expect(page).toHaveURL(new RegExp(`code=${RETURN_CODE}`));
    await expect(page).toHaveURL(new RegExp(`date=${date}`));
    const confirmed = page.getByText(/Identity confirmed — not yet signed/i);
    await expect(confirmed).toBeVisible({ timeout: 30_000 });
    expect(page.url()).not.toContain("stepUp=");
    expect(page.url()).not.toContain("sign=");
    expect(page.url()).not.toContain("certify=");
    await confirmed.scrollIntoViewIfNeeded();
    await evidence(page, "sso-step-up-return");

    // The authorisation is held in an HttpOnly cookie and spent server-side:
    // no password is typed, and the certification lands.
    await expect(workspace.getByLabel("Your password")).toHaveCount(0);
    await workspace.getByRole("button", { name: "Certify and send" }).click();
    await expect(workspace).toBeHidden({ timeout: 120_000 });
    await expect(page.getByText("Preparer certified").first()).toBeVisible({
      timeout: 30_000,
    });
    await evidence(page, "sso-step-up-certified");
  });
});
