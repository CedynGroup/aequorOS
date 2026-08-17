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
 * Independence: each LCR-NSFR journey claims its OWN reporting period (indices
 * 1..4 — index 0 stays with the pre-existing submission-lifecycle spec) and
 * mints the liquidity baseline run LCR-NSFR draws on, so no journey shares a
 * package version chain with another. Sandbox behavior is configured through
 * the API with the minted admin token (PUT channel-configs/orass_sandbox).
 */

import { test, expect, type Browser, type Page } from '@playwright/test';
import path from 'path';
import { E2E_API_ORIGIN, E2E_TMP } from '../playwright.config';
import { mintBackendToken } from './support/mint';
import {
  approveAndSignAsChecker,
  certifyAsPreparer,
  fmtDateGB,
  returnsUrl,
  sendBackAsChecker,
} from './support/ceremony';

const SAMPLE_BANK_ID = 'BK-SAMP0001';
const adminState = path.join(E2E_TMP, 'admin.json');
const approverState = path.join(E2E_TMP, 'approver.json');

// ---------------------------------------------------------------------------
// Backend API helpers (admin token minted the same way global-setup does).
// ---------------------------------------------------------------------------

async function api(
  token: string,
  method: string,
  pathName: string,
  body?: unknown
): Promise<any> {
  const response = await fetch(`${E2E_API_ORIGIN}/api/v1${pathName}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    throw new Error(
      `${method} ${pathName} -> ${response.status}: ${await response.text()}`
    );
  }
  return response.json();
}

let adminToken: string;

/** One reporting date (ISO) per LCR-NSFR journey — never the latest period. */
const journeyDates = { ack: '', downtime: '', reject: '', sendBack: '' };

test.beforeAll(async () => {
  adminToken = await mintBackendToken('admin');
  const listing = await api(
    adminToken,
    'GET',
    `/banks/${SAMPLE_BANK_ID}/reporting-periods`
  );
  const periods: { id: string; period_end: string }[] = listing.periods;
  // Newest first; [0] belongs to the legacy submission-lifecycle journeys.
  const claims: (keyof typeof journeyDates)[] = [
    'ack',
    'downtime',
    'reject',
    'sendBack',
  ];
  for (const [index, key] of claims.entries()) {
    const period = periods[index + 1];
    journeyDates[key] = String(period.period_end).slice(0, 10);
    // LCR-NSFR generation pulls from the latest succeeded liquidity baseline run
    // of its reporting period — mint one for each claimed period.
    await api(adminToken, 'POST', `/banks/${SAMPLE_BANK_ID}/regulatory-runs`, {
      module: 'liquidity',
      reporting_period_id: period.id,
      scenario_code: 'baseline',
    });
  }
});

/** Replace the ORASS sandbox channel config (behavior + downtime switch). */
function setSandboxConfig(config: Record<string, unknown>): Promise<unknown> {
  return api(
    adminToken,
    'PUT',
    `/banks/${SAMPLE_BANK_ID}/regulatory-reporting/channel-configs/orass_sandbox`,
    { config }
  );
}

// ---------------------------------------------------------------------------
// UI helpers
// ---------------------------------------------------------------------------

/**
 * Generate a fresh LCR-NSFR package for `date` and validate it.
 *
 * Validation must pass — ERROR findings keep certification locked, and that is a
 * product bug this helper would surface rather than paper over.
 */
async function generateAndValidate(page: Page, date: string): Promise<void> {
  await page.goto(returnsUrl('LCR-NSFR', date));
  await expect(
    page.getByRole('heading', { name: /returns workspace/i })
  ).toBeVisible();

  await page
    .getByRole('button', { name: /generate package|regenerate/i })
    .first()
    .click();
  const validate = page.getByRole('button', { name: 'Validate', exact: true });
  await expect(validate).toBeEnabled();
  await validate.click();
  await expect(page.getByText(/\bValidated\b/).first()).toBeVisible();
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
  date: string
): Promise<void> {
  await generateAndValidate(page, date);
  await certifyAsPreparer(page, 'LCR-NSFR', date);
  await approveAndSignAsChecker(browser, approverState, date);
}

/** Submit the approved, fully certified package through the ORASS sandbox. */
async function submitViaSandbox(page: Page, date: string): Promise<void> {
  await page.goto(returnsUrl('LCR-NSFR', date));
  // Cleared to submit is now a claim the screen makes only when the service
  // says so — and it says so because both signatures are on record.
  await expect(page.getByTestId('attestation-clearance').first()).toHaveText(
    /^Cleared to submit$/i
  );
  const submit = page.getByTestId('submit-package');
  await expect(submit).toBeEnabled();
  await page.getByLabel('Channel').selectOption('orass_sandbox');
  await submit.click();
}

// ---------------------------------------------------------------------------
// Journeys
// ---------------------------------------------------------------------------

test.describe('full lifecycle', () => {
  test.use({ storageState: adminState });
  // Two real certifications per journey: pyHanko signs the document and the
  // export engine re-renders it, and pdf.js renders it again in the browser.
  test.describe.configure({ timeout: 300_000 });

  test('journey 1: certify → approve and sign → submit → poll acknowledges with Rev 1.0', async ({
    page,
    browser,
  }) => {
    const date = journeyDates.ack;
    await setSandboxConfig({ sandbox_behavior: 'ack' });

    await certifyAndApprove(page, browser, date);
    await submitViaSandbox(page, date);

    // Submission stamps the ORASS revision — 1.0 for a first submission.
    await expect(page.getByText('Rev 1.0')).toBeVisible();

    const poll = page.getByRole('button', { name: 'Poll status' });
    await expect(poll).toBeEnabled();
    await poll.click();

    await expect(page.getByText(/Acknowledged by the regulator/)).toBeVisible();
    await expect(page.getByText('Rev 1.0')).toBeVisible();
  });

  test('journey 2: downtime → email fallback → ORASS re-upload → acknowledged', async ({
    page,
    browser,
  }) => {
    const date = journeyDates.downtime;
    await setSandboxConfig({ sandbox_behavior: 'ack', downtime: true });

    await certifyAndApprove(page, browser, date);
    await submitViaSandbox(page, date);

    // The structured channel_downtime 409 surfaces as the guided fallback
    // panel, not a raw error.
    await expect(
      page.getByText('ORASS downtime — email fallback available')
    ).toBeVisible();
    await page.getByRole('button', { name: 'Use email fallback' }).click();

    // Email submission is provisional (BG/FMD/2026/07): deemed complete only
    // after re-upload through ORASS once functionality is restored. Two
    // indicators: the actionable workspace panel (a <p> title) and the
    // events-feed chip on the email submission event (a <span>, which stays
    // on the historical event even after the re-upload completes).
    const reuploadPanel = page.locator('p', {
      hasText: 'Pending ORASS re-upload',
    });
    await expect(reuploadPanel).toBeVisible();
    await expect(
      page.locator('span', { hasText: 'Pending ORASS re-upload' })
    ).toBeVisible();
    const reupload = page.getByRole('button', { name: 'Re-upload via ORASS' });
    await expect(reupload).toBeEnabled();

    await setSandboxConfig({ sandbox_behavior: 'ack', downtime: false });
    await reupload.click();
    await expect(reuploadPanel).toBeHidden();

    const poll = page.getByRole('button', { name: 'Poll status' });
    await expect(poll).toBeEnabled();
    await poll.click();
    await expect(page.getByText(/Acknowledged by the regulator/)).toBeVisible();
  });

  test('journey 3: rejection carries supervisor comments and reopens rework', async ({
    page,
    browser,
  }) => {
    const date = journeyDates.reject;
    await setSandboxConfig({ sandbox_behavior: 'reject' });

    await certifyAndApprove(page, browser, date);
    await submitViaSandbox(page, date);

    const poll = page.getByRole('button', { name: 'Poll status' });
    await expect(poll).toBeEnabled();
    await poll.click();

    // Terminal Rejected state + ORASS "View Comments" parity panel carrying
    // the simulated server-side validation rule.
    await expect(page.getByText('Rejected', { exact: true }).first()).toBeVisible();
    await expect(page.getByText('Supervisor comments')).toBeVisible();
    // The simulated rule id also appears in the events feed / poll detail —
    // the panel copy is one of the matches.
    await expect(page.getByText(/SIM-LQ-104/).first()).toBeVisible();

    // Rejected = returned for correction: regeneration stays available to
    // mint the superseding rework version.
    await expect(
      page.getByText(/regenerate to mint a superseding version/)
    ).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Regenerate (new version)' })
    ).toBeEnabled();

    // Hygiene: leave the sandbox on its happy-path default for later specs.
    await setSandboxConfig({ sandbox_behavior: 'ack' });
  });

  test('journey 4: the reviewer sends it back with a note, and it is unsubmittable again', async ({
    page,
    browser,
  }) => {
    const date = journeyDates.sendBack;
    const note = 'Line 12 double-counts the placement maturing 2 April.';

    await generateAndValidate(page, date);
    await certifyAsPreparer(page, 'LCR-NSFR', date);
    await sendBackAsChecker(browser, approverState, date, note);

    // Back with the preparer, and genuinely correctable: the certification that
    // froze the figures is withdrawn, the note is on the record, and nothing on
    // the screen claims the return may be filed.
    await page.goto(returnsUrl('LCR-NSFR', date));
    await expect(page.getByText('Unsigned').first()).toBeVisible();
    await expect(page.getByTestId('attestation-clearance').first()).toHaveText(
      /^Not cleared to submit/i
    );
    await expect(page.getByTestId('submit-package')).toBeDisabled();
    await expect(page.getByText(note).first()).toBeVisible();
    // The preparer's signature is history, not deleted — the withdrawn cycle is
    // named rather than silently dropped.
    await expect(page.getByText(/retained in the append-only trail/i)).toBeVisible();
  });

  test('journey 5: institution register drives the LRT corporate pack', async ({
    page,
  }) => {
    await page.goto('/institution');
    await expect(
      page.getByRole('heading', { name: 'Institution Profile' })
    ).toBeVisible();
    // Seeded corporate register (global-setup PUT institution-profile).
    await expect(page.getByText('GH-UB-9001')).toBeVisible();

    await page.getByRole('link', { name: /Generate LRT packs/ }).click();
    await expect(page).toHaveURL(/code=LRT-PROFILE/);
    // Scoped to the fidelity banner paragraph — the return <select> carries
    // the same text in its LRT-PROFILE option.
    await expect(
      page.locator('p', { hasText: 'LRT-PROFILE — Corporate Profile Update pack' })
    ).toBeVisible();

    await page
      .getByRole('button', { name: /generate package|regenerate/i })
      .first()
      .click();
    // The pack pre-fills from the register (no engine runs) and lands in
    // 'generated' — the stepper appears and validation is offered.
    await expect(page.getByText(/\bGenerated\b/).first()).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Validate', exact: true })
    ).toBeEnabled();
  });
});
