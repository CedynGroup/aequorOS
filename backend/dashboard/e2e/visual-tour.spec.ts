/**
 * Visual tour — full-page screenshots of every route against the hermetic
 * stack, for design-audit before/after evidence. Not part of the regular
 * gate: runs only when VISUAL_TOUR=1 is set.
 *
 *   VISUAL_TOUR=1 pnpm exec playwright test visual-tour
 *
 * Output: e2e/.tmp/visual-tour/<route>.png
 */
import { test } from '@playwright/test';
import { mkdirSync } from 'fs';
import path from 'path';
import { E2E_TMP } from '../playwright.config';

const OUT = path.join(__dirname, '.tmp', 'visual-tour');

const ROUTES = [
  '/',
  '/alerts',
  '/basel',
  '/basel/planning',
  '/basel/rwa',
  '/basel/stress',
  '/basel/structure',
  '/behavioral',
  '/behavioral/deposit-stability',
  '/behavioral/nmd-duration',
  '/behavioral/prepayment',
  '/data-engine',
  '/data-engine/adapters',
  '/data-engine/api',
  '/data-engine/database',
  '/data-engine/excel-csv',
  '/data-engine/market-data',
  '/data-engine/positions',
  '/data-engine/t24',
  '/forecasting',
  '/forecasting/assumptions',
  '/forecasting/nii',
  '/forecasting/optimizer',
  '/forecasting/reverse-stress',
  '/forecasting/scenario',
  '/forecasting/whatif',
  '/ftp',
  '/ftp/expost',
  '/ftp/lines',
  '/ftp/products',
  '/ftp/rules',
  '/ftp/scenarios',
  '/fx',
  '/fx/forwards',
  '/fx/hedges',
  '/fx/limits',
  '/fx/scenarios',
  '/fx/var',
  '/institution',
  '/institution/history',
  '/institution/outlets',
  '/institution/parties',
  '/institution/products',
  '/irr',
  '/irr/gaps',
  '/irr/limits',
  '/irr/scenarios',
  '/irr/sensitivity',
  '/liquidity',
  '/liquidity/buffer',
  '/liquidity/cfp',
  '/liquidity/forecast',
  '/liquidity/monitoring',
  '/liquidity/nsfr',
  '/liquidity/stress',
  '/markets',
  '/positions',
  '/reports',
  '/reports/board-pack',
  '/risk',
  '/settings',
  '/settings/profile',
  '/submissions',
  '/submissions/approvals',
  '/submissions/history',
  '/submissions/returns',
  '/submissions/settings',
  '/submissions/signatures',
  '/submissions/templates',
];

test.describe('visual tour', () => {
  test.skip(!process.env.VISUAL_TOUR, 'set VISUAL_TOUR=1 to capture');
  test.use({ storageState: path.join(E2E_TMP, 'admin.json') });

  test('captures every route', async ({ page }) => {
    test.setTimeout(ROUTES.length * 20_000);
    mkdirSync(OUT, { recursive: true });
    for (const route of ROUTES) {
      await page.goto(route, { waitUntil: 'networkidle' }).catch(() => {});
      // settle skeletons / charts
      await page.waitForTimeout(600);
      const name = route === '/' ? 'home' : route.slice(1).replaceAll('/', '__');
      await page.screenshot({
        path: path.join(OUT, `${name}.png`),
        fullPage: true,
      });
    }
  });
});
