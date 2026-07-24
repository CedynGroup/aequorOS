/**
 * Full-lifecycle submission journeys — the complete regulator conversation
 * driven through the real hermetic stack (no mocking):
 *
 *  1. approve → submit (ORASS sandbox, ack) → poll → Acknowledged + Rev 1.0
 *  2. downtime 409 → guided email fallback → pending re-upload → re-upload
 *     via ORASS once restored → poll → Acknowledged (BG/FMD/2026/07 flow)
 *  3. sandbox reject → poll → Rejected + supervisor comments (SIM-LQ-104)
 *     with the package reopened for regeneration
 *  4. institution register (seeded ORASS code) → LRT corporate pack generates
 *
 * Independence: each BSD3 journey claims its OWN reporting period (indices
 * 1..3 — index 0 stays with the pre-existing submission-lifecycle spec) and
 * mints the liquidity baseline run BSD3 draws on, so no journey shares a
 * package version chain with another. Sandbox behavior is configured through
 * the API with the minted admin token (PUT channel-configs/orass_sandbox).
 * Maker-checker is exercised for real: the admin session generates/validates/
 * requests approval, a separate approver session decides.
 */

import { test, expect, type Browser, type Page } from '@playwright/test';
import path from 'path';
import { E2E_API_ORIGIN, E2E_TMP } from '../playwright.config';
import { mintBackendToken } from './support/mint';

const SAMPLE_BANK_ID = '77000000-0000-4000-8000-000000000001';
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

/** One reporting date (ISO) per BSD3 journey — never the latest period. */
const journeyDates = { ack: '', downtime: '', reject: '' };

test.beforeAll(async () => {
  adminToken = await mintBackendToken('admin');
  const listing = await api(
    adminToken,
    'GET',
    `/banks/${SAMPLE_BANK_ID}/reporting-periods`
  );
  const periods: { id: string; period_end: string }[] = listing.periods;
  // Newest first; [0] belongs to the legacy submission-lifecycle journeys.
  const claims: (keyof typeof journeyDates)[] = ['ack', 'downtime', 'reject'];
  for (const [index, key] of claims.entries()) {
    const period = periods[index + 1];
    journeyDates[key] = String(period.period_end).slice(0, 10);
    // BSD3 generation pulls from the latest succeeded liquidity baseline run
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

const returnsUrl = (code: string, date?: string) =>
  `/submissions/returns?code=${code}${date ? `&date=${date}` : ''}`;

/** Mirror of the dashboard's fmtDateUTC ("2026-02-28" → "28 Feb 2026"). */
function fmtDateGB(iso: string): string {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  });
}

/**
 * Generate a fresh BSD3 package for `date` and walk it to pending_approval:
 * generate → validate (must pass — ERROR findings keep the approval request
 * locked, and that is a product bug this helper would surface) → request.
 */
async function makeReadyForApproval(page: Page, date: string): Promise<void> {
  await page.goto(returnsUrl('BSD3', date));
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

  const requestApproval = page.getByRole('button', { name: 'Request approval' });
  await expect(requestApproval).toBeEnabled();
  await requestApproval.click();
  await expect(page.getByText(/Awaiting a checker decision/)).toBeVisible();
}

/** Decide the pending package as a different officer (approver session). */
async function approveAsChecker(browser: Browser, date: string): Promise<void> {
  const context = await browser.newContext({ storageState: approverState });
  const page = await context.newPage();
  await page.goto('/submissions/approvals');
  const row = page.getByRole('row', { name: new RegExp(fmtDateGB(date)) });
  await expect(row.first()).toBeVisible();
  await row.first().click();
  await page.getByRole('button', { name: 'Approve', exact: true }).click();
  // On success the queue refetches empty and the decide panel unmounts with
  // it (its transient "Decision recorded" note included) — the deterministic
  // post-condition is the cleared checker queue.
  await expect(page.getByText('Queue is clear')).toBeVisible();
  await context.close();
}

/** Submit the approved package through the ORASS sandbox channel. */
async function submitViaSandbox(page: Page, date: string): Promise<void> {
  await page.goto(returnsUrl('BSD3', date));
  const submit = page.getByRole('button', { name: 'Submit', exact: true });
  await expect(submit).toBeEnabled();
  await page.getByLabel('Channel').selectOption('orass_sandbox');
  await submit.click();
}

// ---------------------------------------------------------------------------
// Journeys
// ---------------------------------------------------------------------------

test.describe('full lifecycle', () => {
  test.use({ storageState: adminState });

  test('journey 1: approve → submit → poll acknowledges with Rev 1.0', async ({
    page,
    browser,
  }) => {
    const date = journeyDates.ack;
    await setSandboxConfig({ sandbox_behavior: 'ack' });

    await makeReadyForApproval(page, date);
    await approveAsChecker(browser, date);
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

    await makeReadyForApproval(page, date);
    await approveAsChecker(browser, date);
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

    await makeReadyForApproval(page, date);
    await approveAsChecker(browser, date);
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

  test('journey 4: institution register drives the LRT corporate pack', async ({
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
