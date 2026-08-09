/** Temp verification: workbench split layout + transposed matrix. STRESS_CHECK=1. */
import { test, expect } from '@playwright/test';
import path from 'path';
import { E2E_TMP } from '../playwright.config';

const OUT = path.join(__dirname, '.tmp', 'visual-tour');

test.describe('workbench check', () => {
  test.skip(!process.env.STRESS_CHECK, 'set STRESS_CHECK=1 to run');
  test.use({
    storageState: path.join(E2E_TMP, 'admin.json'),
    colorScheme: 'dark',
    viewport: { width: 1600, height: 1000 },
  });

  test('runs an analysis and shows the side-by-side matrix', async ({ page }) => {
    await page.goto('/liquidity/stress', { waitUntil: 'networkidle' });
    await page.getByLabel('Select Combined stress').check();
    await page.getByRole('button', { name: 'Run analysis' }).click();
    await expect(page.getByText('results are not saved unless you save them')).toBeVisible({
      timeout: 30_000,
    });
    await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(OUT, '_workbench-split.png'), fullPage: true });
  });
});
