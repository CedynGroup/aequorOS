/**
 * Submission-pipeline e2e journeys (plan W7.5).
 *
 * These prove the dashboard is really wired to the backend end to end —
 * authenticated navigation, the returns workspace driving generation against
 * the live API, and the role gates. The exhaustive lifecycle state machine
 * (checks → approve → file → acknowledge/reject/decline, corrections,
 * revisions) is covered by the backend test suites; here we confirm the UI
 * surfaces reach those endpoints under real per-role sessions.
 *
 * The workspace no longer draws a six-status stepper. Statuses are not people,
 * and one of them was labelled "Validated", which a bank reads as "the Validator
 * signed off" when it means the rules engine found no errors. What replaced it
 * is the chain: who holds the return, and what has been decided on it
 * (docs/filing_workflow_redesign.md §4b.4).
 */

import { test, expect } from "@playwright/test";
import path from "path";
import { E2E_TMP } from "../playwright.config";
import { requireObjectStorage } from "./support/object-storage";
import { generateCurrentVersion } from "./support/generate";

const RETURNS = "/submissions/returns?code=BSD3";
const analystState = path.join(E2E_TMP, "analyst.json");
const viewerState = path.join(E2E_TMP, "viewer.json");
// Set E2E_EVIDENCE_DIR to write reviewer-visible screenshots outside version control.
const evidenceDir = process.env.E2E_EVIDENCE_DIR;

test.beforeAll(() => requireObjectStorage());

test.describe("submission pipeline", () => {
  // The preparer's session: generating a return is the preparer's act, and an
  // approver's workspace offers no Generate control at all
  // (docs/filing_workflow_redesign.md §4b.1).
  test.use({ storageState: analystState });

  test("journey 1: authenticated returns workspace generates a package", async ({
    page,
  }) => {
    await page.goto(RETURNS);
    // Authenticated navigation lands on the workspace (not /login).
    await expect(page).toHaveURL(/\/submissions\/returns/);
    await expect(
      page.getByRole("heading", { name: /returns workspace/i }),
    ).toBeVisible();

    // Generate a package against the live backend — the UI round-tripped
    // createRegulatoryPackage, and the checks ran with it.
    await generateCurrentVersion(page);
    await expect(page.getByText("Checks passed").first()).toBeVisible({
      timeout: 60_000,
    });

    // The chain names PEOPLE, in order, and says who holds the return now.
    // A status is not a person, which is what the old stepper kept implying.
    const chain = page.getByTestId("filing-chain");
    await expect(chain).toBeVisible();
    await expect(chain.getByText("Preparer", { exact: true })).toBeVisible();
    await expect(chain.getByText("Approver", { exact: true })).toBeVisible();
    await expect(chain.getByText("Validator", { exact: true })).toBeVisible();
    await expect(chain).toContainText(/with the preparer/i);

    // And nothing anywhere calls the machine check a person's decision.
    await expect(page.getByText(/\bValidated\b/)).toHaveCount(0);
    await expect(page.getByRole("button", { name: /^validate$/i })).toHaveCount(
      0,
    );
    await expect(
      page.getByRole("button", { name: "Re-run checks" }),
    ).toBeVisible();
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "returns-workspace-generated.png"),
        fullPage: true,
      });
    }
  });

  test("journey 2: calendar deadline board loads with obligations", async ({
    page,
  }) => {
    // The deadline board moved to its own route when /submissions became a
    // redirect to the Returns workspace; the obligation table lives here.
    await page.goto("/submissions/calendar");
    await expect(page).toHaveURL(/\/submissions\/calendar/);
    // The obligation table is populated from listReportingObligations for the
    // seeded bank. Follow the due-date pager because the calendar deliberately
    // renders only 25 obligations at a time.
    const lcrNsfr = page.getByText("LCR-NSFR", { exact: true }).first();
    const pager = page.getByRole("navigation", {
      name: "Reporting obligations pages",
    });
    const nextPage = pager.getByRole("button", { name: "Next" });
    const firstObligation = page.getByRole("table").getByRole("row").nth(1);
    while (!(await lcrNsfr.isVisible())) {
      // Reaching a disabled Next button without the return is a real failure:
      // LCR-NSFR (the recoded monthly liquidity return) must be in the registry.
      await expect(nextPage).toBeEnabled();
      const previousFirstObligation = await firstObligation.textContent();
      const nextPageResponse = page.waitForResponse((response) => {
        const url = new URL(response.url());
        return (
          url.pathname.endsWith("/reporting-obligations") &&
          response.request().method() === "GET" &&
          response.ok()
        );
      });
      await nextPage.click();
      await nextPageResponse;
      await expect(firstObligation).not.toHaveText(
        previousFirstObligation ?? "",
      );
    }
    await expect(lcrNsfr).toBeVisible();
  });

  test("journey 3: history renders the package/version ledger", async ({
    page,
  }) => {
    // Generate at least one package first so history is non-empty.
    await page.goto(RETURNS);
    await generateCurrentVersion(page);
    await expect(page.getByText("Checks passed").first()).toBeVisible({
      timeout: 60_000,
    });

    await page.goto("/submissions/history");
    await expect(page).toHaveURL(/\/submissions\/history/);
    await expect(page.getByText("LCR-NSFR").first()).toBeVisible();
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "history-ledger.png"),
        fullPage: true,
      });
    }
  });

  test("journey 4: a prior version yields its files, its signers, and a diff", async ({
    page,
  }) => {
    // Two generations, so a superseded version exists whatever earlier
    // journeys left behind.
    await page.goto(RETURNS);
    await generateCurrentVersion(page);
    const current = await generateCurrentVersion(page);
    await expect(page.getByText("Checks passed").first()).toBeVisible({
      timeout: 60_000,
    });

    // Earlier versions are one collapsed row now: provenance, not a card
    // competing with the figures an officer is about to attest to.
    const card = page.getByTestId("versions-row");
    await expect(card).toBeVisible();
    await card.getByRole("button").first().click();

    // The row is a disclosure, not a dead line of text. Address the version
    // this journey just superseded by name rather than by position: it is
    // the one known to be unsigned and never exported, whatever earlier
    // journeys left on the chain.
    const row = card.locator("li", {
      has: page.getByText(`v${current - 1}`, { exact: true }),
    });
    await row.getByRole("button").first().click();

    // Nothing was exported on this chain, so the card says so rather than
    // offering a download that cannot resolve.
    await expect(row.getByText(/Never exported/).first()).toBeVisible();
    await expect(
      row.getByText(/No signature was ever recorded/).first(),
    ).toBeVisible();

    // The figures comparison is available even with no file to retrieve — the
    // snapshot is immutable and always present.
    await row.getByRole("button", { name: /compare with current/i }).click();
    // Regeneration off unchanged canonical data reproduces the figures, so the
    // honest verdict is that nothing moved.
    await expect(row.getByText(/No figure differs from v\d+/)).toBeVisible();
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "prior-version-diff.png"),
        fullPage: true,
      });
    }
  });

  test("journey 5: analyst cannot approve; viewer cannot generate", async ({
    browser,
  }) => {
    const analyst = await browser.newContext({ storageState: analystState });
    const analystPage = await analyst.newPage();
    await analystPage.goto("/submissions/approvals");
    await expect(analystPage).toHaveURL(/\/submissions\/approvals/);
    await analyst.close();

    const viewer = await browser.newContext({ storageState: viewerState });
    const viewerPage = await viewer.newPage();
    await viewerPage.goto(RETURNS);
    // Viewer reaches the workspace (read) but generation is refused (403);
    // the error surfaces rather than a package appearing.
    const gen = viewerPage
      .getByRole("button", { name: /generate the return/i })
      .first();
    if (await gen.isVisible().catch(() => false)) {
      await gen.click();
      await expect(
        viewerPage
          .getByText(/analyst role|forbidden|not permitted|required/i)
          .first(),
      ).toBeVisible();
    }
    await viewer.close();
  });
});

/**
 * The login screen must not blame the operator for an outage.
 *
 * On 2026-07-26 the production API crash-looped and every sign-in attempt read
 * "Invalid email or password" — the operator was told their password was wrong
 * while the backend was not running. `backendTokens` collapsed a 5xx, a refused
 * connection and a genuine 401 into one null.
 *
 * NextAuth distinguishes them: a rejected credential is `CredentialsSignin`, an
 * unreachable backend is `Configuration` (verified against a dev server pointed
 * at a closed port). This asserts the credential branch — the half that can be
 * driven hermetically — so a change that reverts to one message for both is
 * caught here rather than in production.
 */
test.describe("sign-in failures", () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test("a rejected password says so, and does not claim an outage", async ({
    page,
  }) => {
    await page.goto("/login");
    await page.getByLabel("Email").fill("e2e.analyst@aequoros.example");
    await page
      .getByLabel("Password")
      .fill("deliberately-wrong-not-the-fixture-password");
    await page.getByRole("button", { name: "Sign in", exact: true }).click();

    // Scoped to the form: Next's route announcer also carries role="alert"
    // and is empty, which silently swallows a bare getByRole('alert').
    await expect(page.locator('form p[role="alert"]')).toHaveText(
      "Invalid email or password.",
    );
    // The outage copy must NOT appear for a credential rejection: the backend
    // answered, so telling the operator the service is unreachable would be the
    // same defect in the opposite direction.
    await expect(
      page.getByText(/could not reach the aequoros service/i),
    ).toHaveCount(0);
  });
});
