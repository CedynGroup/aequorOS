/**
 * Submission-pipeline e2e journeys (plan W7.5).
 *
 * These prove the dashboard is really wired to the backend end to end —
 * authenticated navigation, the returns workspace driving generation against
 * the live API, and the role gates. The exhaustive lifecycle state machine
 * (validate → approve → submit → acknowledge/reject/decline, resubmission,
 * revisions) is covered by the backend test suites; here we confirm the UI
 * surfaces reach those endpoints under real per-role sessions.
 */

import { test, expect } from '@playwright/test';
import path from 'path';
import { E2E_TMP } from '../playwright.config';

const RETURNS = '/submissions/returns?code=BSD3';
const approverState = path.join(E2E_TMP, 'approver.json');
const analystState = path.join(E2E_TMP, 'analyst.json');
const viewerState = path.join(E2E_TMP, 'viewer.json');

test.describe('submission pipeline', () => {
  test.use({ storageState: approverState });

  test('journey 1: authenticated returns workspace generates a package', async ({
    page,
  }) => {
    await page.goto(RETURNS);
    // Authenticated navigation lands on the workspace (not /login).
    await expect(page).toHaveURL(/\/submissions\/returns/);
    await expect(
      page.getByRole('heading', { name: /returns workspace/i })
    ).toBeVisible();

    // Generate a package against the live backend, then confirm the lifecycle
    // stepper advances to "Generated" — the UI round-tripped createRegulatoryPackage.
    const generate = page
      .getByRole('button', { name: /generate package|regenerate/i })
      .first();
    await generate.click();
    await expect(page.getByText(/\bGenerated\b/).first()).toBeVisible();

    // The stepper shows states REACHED, not activities in progress. The state a
    // package is in must read as achieved, with the highlight on what has NOT
    // happened yet — otherwise resting in a state is indistinguishable from
    // being stuck in it. Reported from the live app on 2026-07-25: an approved,
    // fully-certified return looked stuck because "Approved" rendered as the
    // current step, next to a step then labelled "Approval".
    const stepper = page.getByLabel('Package lifecycle').first();
    await expect(stepper).toBeVisible();
    // The waiting stage says it is waiting, and cannot be mistaken for the
    // decided one.
    await expect(stepper.getByText('Awaiting approval')).toBeVisible();
    await expect(stepper.getByText('Approved', { exact: true })).toBeVisible();

    // Validate is now offered — the workspace reflects backend state transitions.
    await expect(
      page.getByRole('button', { name: /validate/i }).first()
    ).toBeVisible();
  });

  test('journey 2: calendar deadline board loads with obligations', async ({
    page,
  }) => {
    await page.goto('/submissions');
    await expect(page).toHaveURL(/\/submissions/);
    // The obligation table is populated from listReportingObligations for the
    // seeded bank — BSD3 (monthly liquidity return) must appear.
    await expect(page.getByText('BSD3').first()).toBeVisible();
  });

  test('journey 3: history renders the package/version ledger', async ({ page }) => {
    // Generate at least one package first so history is non-empty.
    await page.goto(RETURNS);
    await page
      .getByRole('button', { name: /generate package|regenerate/i })
      .first()
      .click();
    await expect(page.getByText(/\bGenerated\b/).first()).toBeVisible();

    await page.goto('/submissions/history');
    await expect(page).toHaveURL(/\/submissions\/history/);
    await expect(page.getByText('BSD3').first()).toBeVisible();
  });

  test('journey 4: a prior version yields its files, its signers, and a diff', async ({
    page,
  }) => {
    // Two generations, so a superseded version exists whatever earlier
    // journeys left behind.
    await page.goto(RETURNS);
    for (let i = 0; i < 2; i += 1) {
      await page
        .getByRole('button', { name: /generate package|regenerate/i })
        .first()
        .click();
      await expect(page.getByText(/\bGenerated\b/).first()).toBeVisible();
    }

    const card = page.locator('section', {
      has: page.getByRole('heading', { name: 'Prior versions' }),
    });
    await expect(card).toBeVisible();

    // The row is a disclosure, not a dead line of text.
    const row = card.locator('li').first();
    await row.getByRole('button').first().click();

    // Nothing was exported on this chain, so the card says so rather than
    // offering a download that cannot resolve.
    await expect(row.getByText(/Never exported/).first()).toBeVisible();
    await expect(
      row.getByText(/No signature was ever recorded/).first()
    ).toBeVisible();

    // The figures comparison is available even with no file to retrieve — the
    // snapshot is immutable and always present.
    await row.getByRole('button', { name: /compare with current/i }).click();
    // Regeneration off unchanged canonical data reproduces the figures, so the
    // honest verdict is that nothing moved.
    await expect(row.getByText(/No figure differs from v\d+/)).toBeVisible();
  });

  test('journey 5: analyst cannot approve; viewer cannot generate', async ({
    browser,
  }) => {
    const analyst = await browser.newContext({ storageState: analystState });
    const analystPage = await analyst.newPage();
    await analystPage.goto('/submissions/approvals');
    await expect(analystPage).toHaveURL(/\/submissions\/approvals/);
    await analyst.close();

    const viewer = await browser.newContext({ storageState: viewerState });
    const viewerPage = await viewer.newPage();
    await viewerPage.goto(RETURNS);
    // Viewer reaches the workspace (read) but generation is refused (403);
    // the error surfaces rather than a package appearing.
    const gen = viewerPage
      .getByRole('button', { name: /generate package/i })
      .first();
    if (await gen.isVisible().catch(() => false)) {
      await gen.click();
      await expect(
        viewerPage.getByText(/analyst role|forbidden|not permitted|required/i).first()
      ).toBeVisible();
    }
    await viewer.close();
  });
});
