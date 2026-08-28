/** Temp: verify deferred ratio loading/geometry and capture home. HOME_CHECK=1. */
import { expect, test } from '@playwright/test';
import path from 'path';
import { E2E_TMP } from '../playwright.config';

test.describe('home check', () => {
  test.skip(!process.env.HOME_CHECK, 'set HOME_CHECK=1 to run');
  test.use({
    storageState: path.join(E2E_TMP, 'admin.json'),
    colorScheme: 'dark',
    viewport: { width: 1600, height: 600 },
  });
  test('captures the full command center', async ({ page }) => {
    await page.goto('/', { waitUntil: 'networkidle' });
    const panel = () => page.locator('section').filter({
      has: page.getByRole('heading', { name: 'Ratio trend' }),
    });
    const loadingPanel = panel();
    await expect(loadingPanel.getByLabel('Loading chart')).toBeVisible();
    const loadingBox = await loadingPanel.boundingBox();
    await loadingPanel.screenshot({
      path: path.join(__dirname, '.tmp', 'visual-tour', '_home-ratio-loading.png'),
    });

    await page.mouse.wheel(0, 1000);
    const ratioPanel = panel();
    await expect(ratioPanel.getByLabel('Loading chart')).toBeHidden();
    await expect(ratioPanel.locator('.recharts-wrapper')).toBeVisible();
    const loadedBox = await ratioPanel.boundingBox();
    expect(loadedBox?.height).toBe(loadingBox?.height);
    console.log(
      `Ratio panel geometry: ${loadingBox?.height}px loading -> ${loadedBox?.height}px loaded`,
    );
    await page.screenshot({
      path: path.join(__dirname, '.tmp', 'visual-tour', '_home-full.png'),
      fullPage: true,
    });
    await ratioPanel.screenshot({
      path: path.join(__dirname, '.tmp', 'visual-tour', '_home-ratio.png'),
    });
  });
});
