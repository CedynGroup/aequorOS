/** ACTS003 acts/prefer-e2e-stability — runs the real pinned oxlint binary. */
import test from "node:test";
import { assertClean, assertFlagged, runOxlint } from "./helpers.mjs";

test("plugin exposes the ACTS003 rule id", async () => {
  const result = await runOxlint(
    ["e2eStability"],
    "test('t', async ({ page }) => { await page.waitForTimeout(100); });\n"
  );
  assertFlagged(result, "e2eStability");
});

test("reports page.waitForTimeout", async () => {
  const result = await runOxlint(["e2eStability"], `
    test('journey', async ({ page }) => {
      await page.goto('/banks');
      await page.waitForTimeout(1000);
      await expect(page.getByText('Ready')).toBeVisible();
    });
  `);
  assertFlagged(result, "e2eStability");
});

test("reports waitForTimeout through a page alias", async () => {
  const result = await runOxlint(["e2eStability"], `
    test('journey', async ({ page }) => {
      const adminPage = page;
      adminPage.waitForTimeout(5);
    });
  `);
  assertFlagged(result, "e2eStability");
});

test("reports waitForTimeout on a page-like destructured parameter", async () => {
  const result = await runOxlint(["e2eStability"], `
    async function openJournal({ page }) {
      await page.waitForTimeout(250);
    }
  `);
  assertFlagged(result, "e2eStability");
});

test("reports waitForLoadState('networkidle')", async () => {
  const result = await runOxlint(["e2eStability"], `
    test('journey', async ({ page }) => {
      await page.goto('/banks');
      await page.waitForLoadState('networkidle');
    });
  `);
  assertFlagged(result, "e2eStability");
});

test("reports waitForLoadState with a networkidle constant", async () => {
  const result = await runOxlint(["e2eStability"], `
    const READINESS = 'networkidle';
    test('journey', async ({ page }) => {
      await page.waitForLoadState(READINESS);
    });
  `);
  assertFlagged(result, "e2eStability");
});

test("allows waitForLoadState with a concrete load state", async () => {
  const result = await runOxlint(["e2eStability"], `
    test('journey', async ({ page }) => {
      await page.waitForLoadState('domcontentloaded');
    });
  `);
  assertClean(result, "e2eStability");
});

test("allows condition-based waits", async () => {
  const result = await runOxlint(["e2eStability"], `
    test('journey', async ({ page }) => {
      await page.waitForSelector('[data-testid="register"]');
      await page.waitForURL('**/banks');
      await expect(page.getByRole('row')).toHaveCount(5);
      await page.waitForLoadState('load');
    });
  `);
  assertClean(result, "e2eStability");
});

test("allows waitForTimeout on an object that is not page-like", async () => {
  const result = await runOxlint(["e2eStability"], `
    test('unit-ish', async () => {
      const scheduler = { waitForTimeout: () => {} };
      scheduler.waitForTimeout(5);
    });
  `);
  assertClean(result, "e2eStability");
});

test("treats a page-named local binding as a page", async () => {
  const result = await runOxlint(["e2eStability"], `
    test('t', async () => {
      const page = { waitForTimeout: (ms) => ms };
      page.waitForTimeout(5);
    });
  `);
  assertFlagged(result, "e2eStability");
});

test("still reports waitForTimeout with object-rest parameters present", async () => {
  const result = await runOxlint(["e2eStability"], `
    async function openJournal({ page, ...fixtures }) {
      await page.waitForTimeout(250);
    }
  `);
  assertFlagged(result, "e2eStability");
});

test("a disable comment with a reason suppresses the finding", async () => {
  const result = await runOxlint(["e2eStability"], `
    test('journey', async ({ page }) => {
      // oxlint-disable-next-line acts/prefer-e2e-stability -- waits past the 30s signing deadline (#847)
      await page.waitForTimeout(31_000);
      await expect(page.getByText('Signed')).toBeVisible();
    });
  `);
  assertClean(result, "e2eStability");
});
