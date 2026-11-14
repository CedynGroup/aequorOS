/** ACTS001 acts/require-frozen-time — runs the real pinned oxlint binary. */
import test from "node:test";
import { assertClean, assertFlagged, runOxlint } from "./helpers.mjs";

test("plugin exposes the ACTS001 rule id", async () => {
  const result = await runOxlint(["frozenTime"], "it('t', () => { new Date(); });\n");
  assertFlagged(result, "frozenTime");
});

test("reports new Date() in a test with no freeze", async () => {
  const result = await runOxlint(["frozenTime"], `
    it('t', () => {
      const stamp = new Date();
      expect(stamp).toBeTruthy();
    });
  `);
  assertFlagged(result, "frozenTime");
});

test("reports Date.now() in a test with no freeze", async () => {
  const result = await runOxlint(["frozenTime"], `
    it('t', () => {
      const now = Date.now();
      expect(now).toBeGreaterThan(0);
    });
  `);
  assertFlagged(result, "frozenTime");
});

test("reports new Date() when only a sibling test freezes time", async () => {
  const result = await runOxlint(["frozenTime"], `
    describe('suite', () => {
      it('frozen', () => {
        vi.setSystemTime(new Date('2026-01-01'));
        new Date();
      });
      it('unfrozen', () => {
        new Date();
      });
    });
  `);
  assertFlagged(result, "frozenTime");
});

test("reports new Date() used before vi.setSystemTime in the same test", async () => {
  const result = await runOxlint(["frozenTime"], `
    it('t', () => {
      new Date();
      vi.setSystemTime(new Date('2026-01-01'));
    });
  `);
  assertFlagged(result, "frozenTime");
});

test("allows new Date with an explicit argument", async () => {
  const result = await runOxlint(["frozenTime"], `
    it('t', () => {
      expect(new Date('2026-01-01')).toBeTruthy();
    });
  `);
  assertClean(result, "frozenTime");
});

test("allows Date.now() when vi.setSystemTime is called before it in the test", async () => {
  const result = await runOxlint(["frozenTime"], `
    it('t', () => {
      vi.setSystemTime('2026-01-01');
      expect(Date.now()).toBeGreaterThan(0);
    });
  `);
  assertClean(result, "frozenTime");
});

test("allows new Date() when vi.useFakeTimers runs in the test", async () => {
  const result = await runOxlint(["frozenTime"], `
    it('t', () => {
      vi.useFakeTimers({ toFake: ['Date'] });
      expect(new Date()).toBeTruthy();
    });
  `);
  assertClean(result, "frozenTime");
});

test("allows new Date() when a beforeEach in the same describe freezes time", async () => {
  const result = await runOxlint(["frozenTime"], `
    describe('suite', () => {
      beforeEach(() => {
        vi.useFakeTimers();
        vi.setSystemTime(new Date('2026-01-01'));
      });
      it('t', () => {
        expect(new Date()).toBeTruthy();
      });
    });
  `);
  assertClean(result, "frozenTime");
});

test("allows new Date() when a beforeEach in an outer describe freezes time", async () => {
  const result = await runOxlint(["frozenTime"], `
    describe('outer', () => {
      beforeEach(() => {
        vi.useFakeTimers();
      });
      describe('inner', () => {
        it('t', () => {
          expect(new Date()).toBeTruthy();
        });
      });
    });
  `);
  assertClean(result, "frozenTime");
});

test("allows new Date() when a file-level beforeEach freezes time", async () => {
  const result = await runOxlint(["frozenTime"], `
    beforeEach(() => {
      vi.useFakeTimers();
      vi.setSystemTime(new Date('2026-01-01'));
    });
    it('t', () => {
      expect(new Date()).toBeTruthy();
    });
  `);
  assertClean(result, "frozenTime");
});

test("allows Date.now() when a beforeAll freezes time in the same describe", async () => {
  const result = await runOxlint(["frozenTime"], `
    describe('suite', () => {
      beforeAll(() => {
        vi.useFakeTimers();
      });
      it('t', () => {
        expect(Date.now()).toBeGreaterThan(0);
      });
    });
  `);
  assertClean(result, "frozenTime");
});

test("allows jest.setSystemTime as well as vi.setSystemTime", async () => {
  const result = await runOxlint(["frozenTime"], `
    it('t', () => {
      jest.useFakeTimers();
      jest.setSystemTime('2026-01-01');
      expect(Date.now()).toBeGreaterThan(0);
    });
  `);
  assertClean(result, "frozenTime");
});

test("allows new Date() when page.clock.install runs in a Playwright beforeEach", async () => {
  const result = await runOxlint(["frozenTime"], `
    test.describe('journeys', () => {
      test.beforeEach(async ({ page }) => {
        await page.clock.install({ time: new Date('2026-01-01') });
      });
      test('journey', async ({ page }) => {
        expect(new Date()).toBeTruthy();
      });
    });
  `);
  assertClean(result, "frozenTime");
});

test("allows Date.now() when page.clock.setFixedTime runs in the Playwright test", async () => {
  const result = await runOxlint(["frozenTime"], `
    test('journey', async ({ page }) => {
      await page.clock.setFixedTime(new Date('2026-01-01'));
      expect(Date.now()).toBeGreaterThan(0);
    });
  `);
  assertClean(result, "frozenTime");
});

test("allows Date.now() when a file-level test.beforeEach installs the clock", async () => {
  const result = await runOxlint(["frozenTime"], `
    test.beforeEach(async ({ page }) => {
      await page.clock.install({ time: new Date('2026-01-01') });
    });
    test('journey', async ({ page }) => {
      expect(Date.now()).toBeGreaterThan(0);
    });
  `);
  assertClean(result, "frozenTime");
});

test("reports new Date() in a Playwright test without a frozen clock", async () => {
  const result = await runOxlint(["frozenTime"], `
    test('journey', async ({ page }) => {
      await page.goto('/banks');
      const startedAt = Date.now();
      expect(startedAt).toBeGreaterThan(0);
    });
  `);
  assertFlagged(result, "frozenTime");
});

test("reports Date.now() when a sibling Playwright test installs the clock", async () => {
  const result = await runOxlint(["frozenTime"], `
    test.describe('journeys', () => {
      test('frozen', async ({ page }) => {
        await page.clock.install({ time: new Date('2026-01-01') });
      });
      test('unfrozen', async ({ page }) => {
        expect(Date.now()).toBeGreaterThan(0);
      });
    });
  `);
  assertFlagged(result, "frozenTime");
});

test("a disable comment with a reason suppresses the finding", async () => {
  const result = await runOxlint(["frozenTime"], `
    it('t', () => {
      // oxlint-disable-next-line acts/require-frozen-time -- asserts the DB server_default against the wall clock (#847)
      expect(Date.now()).toBeGreaterThan(0);
    });
  `);
  assertClean(result, "frozenTime");
});

test("reports a module-level read even when a file-level beforeEach freezes time", async () => {
  const result = await runOxlint(["frozenTime"], `
    beforeEach(() => {
      vi.useFakeTimers();
      vi.setSystemTime(new Date('2026-01-01'));
    });
    const startedAt = Date.now();
    it('t', () => {
      expect(startedAt).toBeGreaterThan(0);
    });
  `);
  assertFlagged(result, "frozenTime");
});

test("does not let an afterAll freeze cover tests", async () => {
  const result = await runOxlint(["frozenTime"], `
    describe('suite', () => {
      afterAll(() => {
        vi.useFakeTimers();
      });
      it('t', () => {
        expect(new Date()).toBeTruthy();
      });
    });
  `);
  assertFlagged(result, "frozenTime");
});

test("does not let an afterEach freeze cover tests", async () => {
  const result = await runOxlint(["frozenTime"], `
    afterEach(() => {
      vi.useFakeTimers();
    });
    it('t', () => {
      expect(new Date()).toBeTruthy();
    });
  `);
  assertFlagged(result, "frozenTime");
});

test("reports time reads outside any test callback in a test file", async () => {
  const result = await runOxlint(["frozenTime"], `
    const startedAt = Date.now();
    it('t', () => {
      expect(startedAt).toBeGreaterThan(0);
    });
  `);
  assertFlagged(result, "frozenTime");
});
