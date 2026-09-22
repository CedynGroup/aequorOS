/**
 * Full-lifecycle submission journeys — the complete regulator conversation
 * driven through the real hermetic stack (no mocking):
 *
 *  1. certify → approve and sign → submit (ORASS sandbox, ack) → poll →
 *     Acknowledged + Rev 1.0
 *  2. downtime 409 → guided email fallback → pending re-upload → re-upload
 *     via ORASS once restored → poll → Acknowledged (BG/FMD/2026/07 flow)
 *  3. sandbox reject → poll → Rejected + supervisor comments (SIM-LQ-104)
 *     with the package reopened for regeneration
 *  4. the reviewer's other exit — send back for corrections with a note, and the
 *     return is unfrozen, unsigned and unsubmittable again
 *  5. institution register (seeded ORASS code) → LRT corporate pack generates
 *
 * Every LCR-NSFR journey now passes through the SIGNING CEREMONY for real, because
 * signing is required for every return by default and an unsigned return must
 * never be submittable. Relaxing the policy would have turned these three
 * journeys green while deleting coverage of that gate, so instead the fixture
 * users carry password hashes (scripts/e2e_bootstrap.py) and the journeys sign:
 * the preparer places the fields, adopts a mark and certifies, and a separate
 * approver session approves-and-signs in one act from the checker queue. That
 * makes these the only tests in the suite covering the whole chain end to end.
 *
 * Note there is no "Request approval" step any more: for a return that requires
 * signatures the preparer's certification IS the request — it freezes the figures
 * and routes the return to the approver they name.
 *
 * And there is no "Validate" step any more either. Machine validation is not a
 * human act, so it stopped being a button: the checks run with generation and
 * the result is a panel the preparer CLEARS
 * (docs/filing_workflow_redesign.md §4b.2). These journeys assert that rule —
 * that the button is gone and the checks ran anyway — rather than being relaxed
 * around it. The three sessions are three ROLES: the preparer prepares, a
 * separate approver approves and signs, and a third officer holding the
 * transmission authority files. Nothing that reaches a regulator is offered to
 * the first two, and journey 0 asserts that on the preparer's own screen.
 *
 * Independence: each LCR-NSFR journey claims its OWN reporting period (indices
 * 1..4 — index 0 stays with the pre-existing submission-lifecycle spec) and
 * mints the liquidity baseline run LCR-NSFR draws on, so no journey shares a
 * package version chain with another. Sandbox behavior is configured through
 * the API with the minted admin token (PUT channel-configs/orass_sandbox).
 */

import { test, expect, type Browser, type Page } from "@playwright/test";
import path from "path";
import { E2E_API_ORIGIN, E2E_TMP } from "../playwright.config";
import { mintBackendToken } from "./support/mint";
import { requireObjectStorage } from "./support/object-storage";
import {
  approveAndSignAsChecker,
  certifyAsPreparer,
  fmtDateGB,
  returnsUrl,
  sendBackAsChecker,
} from "./support/ceremony";

const SAMPLE_BANK_ID = "BK-SAMP0001";
const adminState = path.join(E2E_TMP, "admin.json");
const approverState = path.join(E2E_TMP, "approver.json");
const validatorState = path.join(E2E_TMP, "validator.json");

// ---------------------------------------------------------------------------
// Backend API helpers (admin token minted the same way global-setup does).
// ---------------------------------------------------------------------------

async function api(
  token: string,
  method: string,
  pathName: string,
  body?: unknown,
): Promise<any> {
  const response = await fetch(`${E2E_API_ORIGIN}/api/v1${pathName}`, {
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

let adminToken: string;

/** One reporting date (ISO) per LCR-NSFR journey — never the latest period. */
const journeyDates = { ack: "", downtime: "", reject: "", sendBack: "" };

test.beforeAll(async () => {
  requireObjectStorage();
  adminToken = await mintBackendToken("admin");
  const listing = await api(
    adminToken,
    "GET",
    `/banks/${SAMPLE_BANK_ID}/reporting-periods`,
  );
  const periods: { id: string; period_end: string }[] = listing.periods;
  // Newest first; [0] belongs to the legacy submission-lifecycle journeys.
  const claims: (keyof typeof journeyDates)[] = [
    "ack",
    "downtime",
    "reject",
    "sendBack",
  ];
  for (const [index, key] of claims.entries()) {
    const period = periods[index + 1];
    journeyDates[key] = String(period.period_end).slice(0, 10);
    // LCR-NSFR generation pulls from the latest succeeded liquidity baseline run
    // of its reporting period — mint one for each claimed period.
    await api(adminToken, "POST", `/banks/${SAMPLE_BANK_ID}/regulatory-runs`, {
      module: "liquidity",
      reporting_period_id: period.id,
      scenario_code: "baseline",
    });
  }
});

/** Replace the ORASS sandbox channel config (behavior + downtime switch). */
function setSandboxConfig(config: Record<string, unknown>): Promise<unknown> {
  return api(
    adminToken,
    "PUT",
    `/banks/${SAMPLE_BANK_ID}/regulatory-reporting/channel-configs/orass_sandbox`,
    { config },
  );
}

// ---------------------------------------------------------------------------
// UI helpers
// ---------------------------------------------------------------------------

/**
 * Generate a fresh LCR-NSFR package for `date`; the checks run with it.
 *
 * The checks must pass — failing checks keep certification locked, and that is a
 * product bug this helper would surface rather than paper over. What it will not
 * do is press a "Validate" button, because there is no longer one to press: the
 * assertion below is that the button is ABSENT and the checks ran regardless.
 */
async function generateAndClearChecks(page: Page, date: string): Promise<void> {
  await page.goto(returnsUrl("LCR-NSFR", date));
  await expect(
    page.getByRole("heading", { name: /returns workspace/i }),
  ).toBeVisible();

  await expect(page.getByRole("button", { name: /^validate$/i })).toHaveCount(
    0,
  );

  await page
    .getByRole("button", { name: /generate the return|^regenerate$/i })
    .first()
    .click();

  // Nobody pressed anything: the checks are part of generating.
  await expect(page.getByText("Checks passed").first()).toBeVisible({
    timeout: 60_000,
  });
}

/**
 * Walk a fresh LCR-NSFR package all the way to `approved` — through the ceremony.
 *
 * Two officers, two sessions, as maker-checker requires: the admin session
 * prepares and certifies, a separate approver session approves and signs. The
 * approval decision is written by that signature, in the same transaction, so
 * there is no separate approve step to perform afterwards.
 */
async function certifyAndApprove(
  page: Page,
  browser: Browser,
  date: string,
): Promise<void> {
  await generateAndClearChecks(page, date);
  await certifyAsPreparer(page, "LCR-NSFR", date);
  await approveAndSignAsChecker(browser, approverState, date);
}

type FilingSession = { page: Page; close: () => Promise<void> };

/**
 * A THIRD session: the officer who transmits.
 *
 * Until 2026-09-20 these journeys filed from the preparer's own admin session,
 * because the submit route required the same permission as the approval
 * decision and on an ungated family a scalar role satisfied it. Filing now
 * requires an explicit Regulatory Reporting / restricted `submit` binding — the
 * Validator — which neither the preparer nor the approver holds. The journeys
 * are therefore three sessions, not two, and they assert the new rule rather
 * than being relaxed to pass (docs/filing_workflow_redesign.md §1 finding 3).
 */
async function openFilingSession(
  browser: Browser,
  date: string,
): Promise<FilingSession> {
  const context = await browser.newContext({ storageState: validatorState });
  const page = await context.newPage();
  await page.goto(returnsUrl("LCR-NSFR", date));
  return { page, close: () => context.close() };
}

/**
 * File the approved, fully certified return through the ORASS sandbox.
 *
 * The filing act is the ONE primary action the Validator's screen offers. It is
 * reached by its role, not by hunting for a Submit card among four others —
 * which is the point: no other session has this control at all.
 */
async function fileViaSandbox(page: Page): Promise<void> {
  // Cleared to submit is a claim the screen makes only when the service says
  // so — and it says so because both signatures are on record.
  await expect(page.getByTestId("attestation-clearance").first()).toHaveText(
    /^Cleared to submit$/i,
  );
  const file = page.getByTestId("primary-filing-action");
  await expect(file).toBeEnabled();
  // Stated in words before it is offered as a button.
  await expect(page.getByTestId("transmission-notice")).toContainText(
    /cannot be recalled/i,
  );
  await page.getByLabel("Channel").selectOption("orass_sandbox");
  await file.click();
}

// ---------------------------------------------------------------------------
// Journeys
// ---------------------------------------------------------------------------

test.describe("full lifecycle", () => {
  test.use({ storageState: adminState });
  // Two real certifications per journey: pyHanko signs the document and the
  // export engine re-renders it, and pdf.js renders it again in the browser.
  test.describe.configure({ timeout: 300_000 });

  test("journey 0: a preparer is never shown a way to reach the regulator", async ({
    page,
  }) => {
    // The founder's complaint, as an assertion. The old screen rendered
    // Generate, Validate, Approve and Submit-to-ORASS for everybody, each
    // greyed out with a blocked reason, and asked a preparer to work out from
    // four dead controls that none of it was theirs. Absent, not disabled
    // (docs/filing_workflow_redesign.md §4b.1, §4b.6).
    await generateAndClearChecks(page, journeyDates.ack);

    for (const forbidden of [/ORASS/i, /downtime/i, /re-upload/i]) {
      await expect(page.getByText(forbidden)).toHaveCount(0);
    }
    await expect(page.getByLabel("Channel")).toHaveCount(0);
    await expect(page.getByTestId("transmission-row")).toHaveCount(0);
    await expect(page.getByTestId("events-row")).toHaveCount(0);
    await expect(page.getByTestId("resubmission-row")).toHaveCount(0);

    // What they DO get: one act that is theirs, named, with what it does.
    const act = page.getByTestId("primary-filing-action");
    await expect(act).toHaveText(/certify and freeze/i);
    await expect(
      page.getByTestId("primary-filing-action-reason"),
    ).toContainText(/sends the return to the approver/i);
  });

  test("journey 1: certify → approve and sign → submit → poll acknowledges with Rev 1.0", async ({
    page,
    browser,
  }) => {
    const date = journeyDates.ack;
    await setSandboxConfig({ sandbox_behavior: "ack" });

    await certifyAndApprove(page, browser, date);
    const filing = await openFilingSession(browser, date);
    try {
      await fileViaSandbox(filing.page);

      // Submission stamps the ORASS revision — 1.0 for a first submission.
      await expect(filing.page.getByText("Rev 1.0")).toBeVisible();

      const decision = filing.page.getByTestId("primary-filing-action");
      await expect(decision).toHaveText(/check for .* decision/i);
      await expect(decision).toBeEnabled();
      await decision.click();

      await expect(filing.page.getByText("Acknowledged").first()).toBeVisible();
      await expect(filing.page.getByText("Rev 1.0")).toBeVisible();
    } finally {
      await filing.close();
    }
  });

  test("journey 2: downtime → email fallback → ORASS re-upload → acknowledged", async ({
    page,
    browser,
  }) => {
    const date = journeyDates.downtime;
    await setSandboxConfig({ sandbox_behavior: "ack", downtime: true });

    await certifyAndApprove(page, browser, date);
    const filing = await openFilingSession(browser, date);
    const filingPage = filing.page;
    try {
      await fileViaSandbox(filingPage);

      // The structured channel_downtime 409 surfaces as the guided fallback
      // panel, not a raw error.
      await expect(
        filingPage.getByText(/ORASS downtime — email fallback available/),
      ).toBeVisible();
      await filingPage
        .getByRole("button", { name: "Use email fallback" })
        .click();

      // Email submission is provisional (BG/FMD/2026/07): deemed complete only
      // after re-upload through ORASS once functionality is restored. Two
      // indicators: the actionable workspace panel (a <p> title) and the
      // events-feed chip on the email submission event (a <span>, which stays
      // on the historical event even after the re-upload completes).
      const reuploadPanel = filingPage.locator("p", {
        hasText: "Sent by the downtime bundle, and not yet complete",
      });
      await expect(reuploadPanel).toBeVisible();
      await expect(
        filingPage.locator("span", { hasText: "Pending ORASS re-upload" }),
      ).toBeVisible();
      // The re-upload is the Validator's one act while the filing is
      // incomplete — it is the primary action, not a button hidden in a card.
      const reupload = filingPage.getByTestId("primary-filing-action");
      await expect(reupload).toHaveText(/re-upload to the portal/i);
      await expect(reupload).toBeEnabled();

      await setSandboxConfig({ sandbox_behavior: "ack", downtime: false });
      await reupload.click();
      await expect(reuploadPanel).toBeHidden();

      const decision = filingPage.getByTestId("primary-filing-action");
      await expect(decision).toHaveText(/check for .* decision/i);
      await expect(decision).toBeEnabled();
      await decision.click();
      await expect(filingPage.getByText("Acknowledged").first()).toBeVisible();
    } finally {
      await filing.close();
    }
  });

  test("journey 3: rejection carries supervisor comments and reopens rework", async ({
    page,
    browser,
  }) => {
    const date = journeyDates.reject;
    await setSandboxConfig({ sandbox_behavior: "reject" });

    await certifyAndApprove(page, browser, date);
    const filing = await openFilingSession(browser, date);
    try {
      await fileViaSandbox(filing.page);

      const decision = filing.page.getByTestId("primary-filing-action");
      await expect(decision).toHaveText(/check for .* decision/i);
      await expect(decision).toBeEnabled();
      await decision.click();

      // Terminal Rejected state + ORASS "View Comments" parity panel carrying
      // the simulated server-side check.
      await expect(
        filing.page.getByText("Rejected", { exact: true }).first(),
      ).toBeVisible();
      await expect(
        filing.page.getByText("What the supervisor said about this return"),
      ).toBeVisible();
      // The simulated rule id also appears in the events feed / poll detail —
      // the panel copy is one of the matches.
      await expect(filing.page.getByText(/SIM-LQ-104/).first()).toBeVisible();
    } finally {
      await filing.close();
    }

    // Rejected = returned for correction, and the rework is the PREPARER's.
    // Their screen offers exactly one act, and it is the correction — not a
    // greyed-out row of everyone else's controls.
    await page.goto(returnsUrl("LCR-NSFR", date));
    const rework = page.getByTestId("primary-filing-action");
    await expect(rework).toHaveText(/generate a corrected version/i);
    await expect(rework).toBeEnabled();
    await expect(
      page.getByText("What the supervisor said about this return"),
    ).toBeVisible();
    // And nothing on the preparer's screen reaches the regulator.
    await expect(page.getByText(/ORASS/i)).toHaveCount(0);
    await expect(page.getByLabel("Channel")).toHaveCount(0);

    // Hygiene: leave the sandbox on its happy-path default for later specs.
    await setSandboxConfig({ sandbox_behavior: "ack" });
  });

  test("journey 4: the reviewer sends it back with a note, and it is unsubmittable again", async ({
    page,
    browser,
  }) => {
    const date = journeyDates.sendBack;
    const note = "Line 12 double-counts the placement maturing 2 April.";

    await generateAndClearChecks(page, date);
    await certifyAsPreparer(page, "LCR-NSFR", date);
    await sendBackAsChecker(browser, approverState, date, note);

    // Back with the preparer, and genuinely correctable: the certification that
    // froze the figures is withdrawn, the note is on the record, and nothing on
    // the screen claims the return may be filed.
    await page.goto(returnsUrl("LCR-NSFR", date));
    await expect(page.getByText("Unsigned").first()).toBeVisible();
    await expect(page.getByTestId("attestation-clearance").first()).toHaveText(
      /^Not cleared to submit/i,
    );
    // The filing control is ABSENT on the preparer's screen, not greyed out —
    // it was never theirs (docs/filing_workflow_redesign.md §4b.1). What they
    // get instead is the note that sent it back, stated before the figures.
    await expect(page.getByTestId("transmission-row")).toHaveCount(0);
    await expect(page.getByTestId("sent-back-notice")).toContainText(note);
    // The preparer's signature is history, not deleted — the withdrawn cycle is
    // named rather than silently dropped.
    await expect(
      page.getByText(/retained in the append-only trail/i),
    ).toBeVisible();
  });

  // Quarantined (e2e/support/quarantine.ts): LRT packs are event-driven, so
  // the registry yields no reporting anchors for them by design — and the
  // workspace then offers no reporting date at all (the select is disabled
  // and there is no Generate control). The register link reaches a dead end
  // until event-driven returns get a way to choose their as-of date; that is
  // a product gap, not fixture drift.
  test.fail(
    "journey 5: institution register drives the LRT corporate pack",
    async ({ page }) => {
      await page.goto("/institution");
      await expect(
        page.getByRole("heading", { name: "Institution Profile" }),
      ).toBeVisible();
      // Seeded corporate register (global-setup PUT institution-profile).
      await expect(page.getByText("GH-UB-9001")).toBeVisible();

      await page.getByRole("link", { name: /Generate LRT packs/ }).click();
      await expect(page).toHaveURL(/code=LRT-PROFILE/);
      // Scoped to the fidelity banner paragraph — the return <select> carries
      // the same text in its LRT-PROFILE option.
      await expect(
        page.locator("p", {
          hasText: "LRT-PROFILE — Corporate Profile Update pack",
        }),
      ).toBeVisible();

      const generate = page
        .getByRole("button", { name: /generate the return|^regenerate$/i })
        .first();
      await expect(generate).toBeVisible({ timeout: 5_000 });
      await generate.click();
      // The pack pre-fills from the register (no engine runs), and the checks
      // run with it — there is no separate act to offer.
      await expect(page.getByText("Checks passed").first()).toBeVisible();
      await expect(
        page.getByRole("button", { name: /^validate$/i }),
      ).toHaveCount(0);
      await expect(
        page.getByRole("button", { name: "Re-run checks" }),
      ).toBeEnabled();
    },
  );
});
