/** Temp verification: unified bell popover, both tabs. BELL_CHECK=1 to run. */
import { test, expect } from '@playwright/test';
import path from 'path';
import { E2E_TMP } from '../playwright.config';

const OUT = path.join(__dirname, '.tmp', 'visual-tour');

test.describe('unified bell', () => {
  test.skip(!process.env.BELL_CHECK, 'set BELL_CHECK=1 to run');
  test.use({ storageState: path.join(E2E_TMP, 'admin.json') });

  test('opens with Breaches and Inbox tabs', async ({ page }) => {
    await page.goto('/', { waitUntil: 'networkidle' });
    await page.getByRole('button', { name: /^Notifications/ }).click();
    await expect(page.getByRole('button', { name: 'Breaches', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Inbox', exact: true })).toBeVisible();
    await page.screenshot({ path: path.join(OUT, '_bell-breaches.png') });
    await page.getByRole('button', { name: 'Inbox', exact: true }).click();
    await page.waitForTimeout(300);
    await page.screenshot({ path: path.join(OUT, '_bell-inbox.png') });
    await expect(
      page.getByRole('button', { name: /Open full inbox/ })
    ).toBeVisible();
  });
});
