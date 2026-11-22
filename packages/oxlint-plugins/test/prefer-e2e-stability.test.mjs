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

// -- Hardening findings (PR #899 review round) ------------------------------

// 4567941801: TypeScript wrappers around the string argument must be unwrapped
// before classification, in both the call argument and constant initializers.
test("reports waitForLoadState('networkidle' as const)", async () => {
  const result = await runOxlint(["e2eStability"], `
    test('journey', async ({ page }) => {
      await page.waitForLoadState('networkidle' as const);
    });
  `);
  assertFlagged(result, "e2eStability");
});

test("reports waitForLoadState with a TS-wrapped networkidle constant", async () => {
  const result = await runOxlint(["e2eStability"], `
    const READINESS = 'networkidle' as const;
    test('journey', async ({ page }) => {
      await page.waitForLoadState(READINESS);
    });
  `);
  assertFlagged(result, "e2eStability");
});

// 4567941811: computed member names are resolved to their static value before
// matching; an unresolved computed identifier is not treated as a method name.
test("reports page['waitForTimeout'](100)", async () => {
  const result = await runOxlint(["e2eStability"], `
    test('journey', async ({ page }) => {
      await page['waitForTimeout'](100);
    });
  `);
  assertFlagged(result, "e2eStability");
});

test("does not flag a computed member whose resolved name is not a wait method", async () => {
  const result = await runOxlint(["e2eStability"], `
    const waitForTimeout = 'waitForURL';
    test('journey', async ({ page }) => {
      await page[waitForTimeout]('**/banks');
    });
  `);
  assertClean(result, "e2eStability");
});

// 4567941816: a page-like member-expression initializer marks the alias
// page-like even when the alias name itself is not page-like.
test("reports waitForTimeout through a page-like member-expression alias", async () => {
  const result = await runOxlint(["e2eStability"], `
    test('journey', async ({ fixtures }) => {
      const tab = fixtures.page;
      await tab.waitForTimeout(100);
    });
  `);
  assertFlagged(result, "e2eStability");
});

// -- Review wave (2026-11-14/15, PR #922) ----------------------------------

// PRRT_kwDOSK8LJc63jg-U: a TS-wrapped computed identifier must be unwrapped
// before the constant lookup, so page[METHOD as typeof METHOD](100) resolves.
test("reports waitForTimeout through a TS-wrapped computed constant", async () => {
  const result = await runOxlint(["e2eStability"], `
    const METHOD = 'waitForTimeout';
    test('journey', async ({ page }) => {
      await page[METHOD as typeof METHOD](100);
    });
  `);
  assertFlagged(result, "e2eStability");
});

// PRRT_kwDOSK8LJc63jg-Y: a computed property's VALUE resolves before the
// page-name check, so `fixtures[page]` with a non-page string stays clean.
test("does not flag a computed alias whose property value is not page-like", async () => {
  const result = await runOxlint(["e2eStability"], `
    const page = 'scheduler';
    const fixtures = { scheduler: { waitForTimeout: (ms) => ms } };
    test('journey', async () => {
      const tab = fixtures[page];
      tab.waitForTimeout(5);
    });
  `);
  assertClean(result, "e2eStability");
});

test("still reports a computed alias whose property value names page", async () => {
  const result = await runOxlint(["e2eStability"], `
    const key = 'page';
    test('journey', async ({ fixtures }) => {
      const tab = fixtures[key];
      await tab.waitForTimeout(100);
    });
  `);
  assertFlagged(result, "e2eStability");
});

