/** Temp: full-page home capture (RangeTabs + Window analysis). HOME_CHECK=1. */
import { test } from '@playwright/test';
import path from 'path';
import { E2E_TMP } from '../playwright.config';

test.describe('home check', () => {
  test.skip(!process.env.HOME_CHECK, 'set HOME_CHECK=1 to run');
  test.use({
    storageState: path.join(E2E_TMP, 'admin.json'),
    colorScheme: 'dark',
    viewport: { width: 1600, height: 1000 },
  });
  test('captures the full command center', async ({ page }) => {
    await page.goto('/', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1800);
    await page.screenshot({
      path: path.join(__dirname, '.tmp', 'visual-tour', '_home-full.png'),
      fullPage: true,
    });
  });
});
