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

test("allows Date reads frozen by node:test's mock.timers with Date mocked", async () => {
  const result = await runOxlint(["frozenTime"], `
    test('t', async (t) => {
      t.mock.timers.enable({ apis: ['Date'], now: 1_700_000_000_000 });
      expect(Date.now()).toBe(1_700_000_000_000);
    });
  `);
  assertClean(result, "frozenTime");
});

test("allows Date reads frozen by node:test's mock.timers with default apis", async () => {
  const result = await runOxlint(["frozenTime"], `
    test('t', async (t) => {
      t.mock.timers.enable();
      expect(new Date()).toBeTruthy();
    });
  `);
  assertClean(result, "frozenTime");
});

test("still flags Date reads when node:test's mock.timers excludes Date", async () => {
  const result = await runOxlint(["frozenTime"], `
    test('t', async (t) => {
      t.mock.timers.enable({ apis: ['setTimeout'] });
      expect(Date.now()).toBeGreaterThan(0);
    });
  `);
  assertFlagged(result, "frozenTime");
});

test("still flags Date reads when the apis key is quoted and excludes Date", async () => {
  const result = await runOxlint(["frozenTime"], `
    test('t', async (t) => {
      t.mock.timers.enable({ 'apis': ['setTimeout'] });
      expect(Date.now()).toBeGreaterThan(0);
    });
  `);
  assertFlagged(result, "frozenTime");
});

test("still flags Date reads when apis is an identifier whose value excludes Date", async () => {
  const result = await runOxlint(["frozenTime"], `
    const timerApis = ['setTimeout'];
    test('t', async (t) => {
      t.mock.timers.enable({ apis: timerApis });
      expect(Date.now()).toBeGreaterThan(0);
    });
  `);
  assertFlagged(result, "frozenTime");
});

test("still flags Date reads when apis is an identifier, even if its value names Date", async () => {
  const result = await runOxlint(["frozenTime"], `
    const clockApis = ['setTimeout', 'Date'];
    test('t', async (t) => {
      t.mock.timers.enable({ apis: clockApis });
      expect(Date.now()).toBeGreaterThan(0);
    });
  `);
  assertFlagged(result, "frozenTime");
});

test("still flags Date reads when apis is a shadowed identifier", async () => {
  const result = await runOxlint(["frozenTime"], `
    const clockApis = ['setTimeout', 'Date'];
    test('t', async (t) => {
      const clockApis = ['setTimeout'];
      t.mock.timers.enable({ apis: clockApis });
      expect(Date.now()).toBeGreaterThan(0);
    });
  `);
  assertFlagged(result, "frozenTime");
});

test("still flags Date reads when a later spread overrides the apis list", async () => {
  const result = await runOxlint(["frozenTime"], `
    const override = { apis: ['setTimeout'] };
    test('t', async (t) => {
      t.mock.timers.enable({ apis: ['Date'], ...override });
      expect(Date.now()).toBeGreaterThan(0);
    });
  `);
  assertFlagged(result, "frozenTime");
});

test("allows Date reads when the apis list comes after the spread", async () => {
  const result = await runOxlint(["frozenTime"], `
    const defaults = { interval: 1_000 };
    test('t', async (t) => {
      t.mock.timers.enable({ ...defaults, apis: ['Date'] });
      expect(Date.now()).toBeGreaterThan(0);
    });
  `);
  assertClean(result, "frozenTime");
});

test("node:test mock.timers freeze covers only its own test", async () => {
  const result = await runOxlint(["frozenTime"], `
    test('frozen', async (t) => {
      t.mock.timers.enable({ apis: ['Date'] });
      expect(Date.now()).toBeGreaterThan(0);
    });
    test('unfrozen', () => {
      expect(Date.now()).toBeGreaterThan(0);
    });
  `);
  assertFlagged(result, "frozenTime");
});

// -- Hardening findings (PR #899 review round) ------------------------------

// 4567941824: `test.describe.serial(...)` chains must resolve to the describe
// scope, so its freezing beforeEach covers only that suite — never the sibling
// suites via a bogus file-level freeze.
test("resolves chained describe modifiers so a serial suite's freeze stays scoped", async () => {
  const result = await runOxlint(["frozenTime"], `
    test.describe.serial('serial suite', () => {
      test.beforeEach(() => {
        vi.useFakeTimers();
      });
      test('frozen', () => {
        expect(Date.now()).toBeGreaterThan(0);
      });
    });
    test.describe('sibling suite', () => {
      test('unfrozen', () => {
        expect(Date.now()).toBeGreaterThan(0);
      });
    });
  `);
  assertFlagged(result, "frozenTime");
});

// 4567895297 + 4567941842: describe bodies execute during collection, before
// any hook, so an ancestor's freezing beforeEach must not exempt them.
test("reports a collection-time read in a nested describe body", async () => {
  const result = await runOxlint(["frozenTime"], `
    describe('outer', () => {
      beforeEach(() => {
        vi.useFakeTimers();
      });
      describe('inner', () => {
        const stamp = Date.now();
        it('t', () => {
          expect(stamp).toBeGreaterThan(0);
        });
      });
    });
  `);
  assertFlagged(result, "frozenTime");
});

// 4567941842: a beforeEach freeze must not cover a beforeAll read — beforeAll
// runs before beforeEach.
test("does not let a beforeEach freeze cover a beforeAll read", async () => {
  const result = await runOxlint(["frozenTime"], `
    describe('suite', () => {
      beforeAll(() => {
        expect(Date.now()).toBeGreaterThan(0);
      });
      beforeEach(() => {
        vi.useFakeTimers();
      });
      it('t', () => {
        expect(new Date()).toBeTruthy();
      });
    });
  `);
  assertFlagged(result, "frozenTime");
});

// 4567941829: a file-level beforeAll freeze runs before a later file-level
// beforeEach, so the beforeEach read is covered — no false positive.
test("lets a file-level beforeAll freeze cover a later beforeEach read", async () => {
  const result = await runOxlint(["frozenTime"], `
    beforeAll(() => {
      vi.setSystemTime(new Date('2026-01-01'));
    });
    beforeEach(() => {
      expect(Date.now()).toBeGreaterThan(0);
    });
    it('t', () => {
      expect(new Date()).toBeTruthy();
    });
  `);
  assertClean(result, "frozenTime");
});

// 4567941832: restoring real timers invalidates an earlier freeze in the same
// callback; the read after useRealTimers() sees the wall clock again.
test("reports a read after useRealTimers restored the wall clock", async () => {
  const result = await runOxlint(["frozenTime"], `
    it('t', () => {
      vi.useFakeTimers();
      vi.useRealTimers();
      expect(Date.now()).toBeGreaterThan(0);
    });
  `);
  assertFlagged(result, "frozenTime");
});

test("still allows a read after a freeze with no restoration in between", async () => {
  const result = await runOxlint(["frozenTime"], `
    it('t', () => {
      vi.useFakeTimers();
      vi.useRealTimers();
      vi.useFakeTimers();
      expect(Date.now()).toBeGreaterThan(0);
    });
  `);
  assertClean(result, "frozenTime");
});

// CodeRabbit hardening round: an explicit module-level freeze that already
// executed covers the collection-time reads that follow it, until restoration.
test("lets an explicit module-level freeze cover collection-time reads", async () => {
  const result = await runOxlint(["frozenTime"], `
    vi.useFakeTimers();
    describe('suite', () => {
      const stamp = Date.now();
      it('t', () => {
        expect(stamp).toBeGreaterThan(0);
      });
    });
  `);
  assertClean(result, "frozenTime");
});

test("reports a collection-time read made before the module-level freeze", async () => {
  const result = await runOxlint(["frozenTime"], `
    describe('suite', () => {
      const stamp = Date.now();
      it('t', () => {
        expect(stamp).toBeGreaterThan(0);
      });
    });
    vi.useFakeTimers();
  `);
  assertFlagged(result, "frozenTime");
});

test("reports a collection-time read after a module-level restoration", async () => {
  const result = await runOxlint(["frozenTime"], `
    vi.useFakeTimers();
    vi.useRealTimers();
    describe('suite', () => {
      const stamp = Date.now();
      it('t', () => {
        expect(stamp).toBeGreaterThan(0);
      });
    });
  `);
  assertFlagged(result, "frozenTime");
});

// CodeRabbit hardening round: a restoring beforeEach undoes an earlier
// freezing beforeEach, so the test it precedes reads the real clock.
test("does not let a restoring beforeEach keep an earlier freeze", async () => {
  const result = await runOxlint(["frozenTime"], `
    describe('suite', () => {
      beforeEach(() => {
        vi.useFakeTimers();
      });
      beforeEach(() => {
        vi.useRealTimers();
      });
      it('t', () => {
        expect(Date.now()).toBeGreaterThan(0);
      });
    });
  `);
  assertFlagged(result, "frozenTime");
});

// -- Review wave (2026-11-14/15, PR #922) ----------------------------------

// PRRT_kwDOSK8LJc63jg-g: a module-level freeze executes before every
// registered callback, so test-body reads are covered regardless of where the
// freeze sits in the source.
test("lets a module-level freeze cover a test-body read", async () => {
  const result = await runOxlint(["frozenTime"], `
    vi.useFakeTimers();
    it('t', () => {
      expect(Date.now()).toBeGreaterThan(0);
    });
  `);
  assertClean(result, "frozenTime");
});

test("lets a module-level freeze declared after the test cover its read", async () => {
  const result = await runOxlint(["frozenTime"], `
    it('t', () => {
      expect(Date.now()).toBeGreaterThan(0);
    });
    vi.useFakeTimers();
  `);
  assertClean(result, "frozenTime");
});

test("reports a test-body read when module-level timers were restored", async () => {
  const result = await runOxlint(["frozenTime"], `
    vi.useFakeTimers();
    vi.useRealTimers();
    it('t', () => {
      expect(Date.now()).toBeGreaterThan(0);
    });
  `);
  assertFlagged(result, "frozenTime");
});

// PRRT_kwDOSK8LJc63jpat: a restoration captured in a nested closure the hook
// never calls executes only if invoked, so the hook's freeze still covers.
test("does not let an uncalled nested restoration undo a hook freeze", async () => {
  const result = await runOxlint(["frozenTime"], `
    beforeEach(() => {
      vi.useFakeTimers();
      const restore = () => vi.useRealTimers();
    });
    it('t', () => {
      expect(Date.now()).toBeGreaterThan(0);
    });
  `);
  assertClean(result, "frozenTime");
});

test("still lets a hook's own restoration undo its freeze", async () => {
  const result = await runOxlint(["frozenTime"], `
    beforeEach(() => {
      vi.useFakeTimers();
      vi.useRealTimers();
    });
    it('t', () => {
      expect(Date.now()).toBeGreaterThan(0);
    });
  `);
  assertFlagged(result, "frozenTime");
});

// PRRT_kwDOSK8LJc63jzd6: an afterEach restoration executes before afterAll,
// so a later afterAll read sees the real wall clock again.
test("replays an afterEach restoration before an afterAll read", async () => {
  const result = await runOxlint(["frozenTime"], `
    beforeEach(() => {
      vi.useFakeTimers();
    });
    afterEach(() => {
      vi.useRealTimers();
    });
    afterAll(() => {
      expect(Date.now()).toBeGreaterThan(0);
    });
  `);
  assertFlagged(result, "frozenTime");
});

test("still lets a beforeEach freeze cover an afterAll read with no teardown", async () => {
  const result = await runOxlint(["frozenTime"], `
    beforeEach(() => {
      vi.useFakeTimers();
    });
    afterAll(() => {
      expect(Date.now()).toBeGreaterThan(0);
    });
  `);
  assertClean(result, "frozenTime");
});

test("reports a freeze inside an uncalled module-level helper", async () => {
  const result = await runOxlint(["frozenTime"], `
    function installClock() {
      vi.useFakeTimers();
    }
    it('t', () => {
      expect(Date.now()).toBeGreaterThan(0);
    });
  `);
  assertFlagged(result, "frozenTime");
});

test("counts an immediately invoked restoration inside a hook", async () => {
  const result = await runOxlint(["frozenTime"], `
    beforeEach(() => {
      vi.useFakeTimers();
      (() => vi.useRealTimers())();
    });
    it('t', () => {
      expect(Date.now()).toBeGreaterThan(0);
    });
  `);
  assertFlagged(result, "frozenTime");
});

test("lets a module-level immediately invoked freeze cover a read", async () => {
  const result = await runOxlint(["frozenTime"], `
    (() => {
      vi.useFakeTimers();
    })();
    it('t', () => {
      expect(Date.now()).toBeGreaterThan(0);
    });
  `);
  assertClean(result, "frozenTime");
});

// -- Hardening review round (nested teardown order, describe-body restores,
// IIFE argument evaluation) -------------------------------------------------

test("reports a nested afterAll read when the outer afterEach restores last", async () => {
  const result = await runOxlint(["frozenTime"], `
    beforeEach(() => {
      vi.useFakeTimers();
    });
    afterEach(() => {
      vi.useRealTimers();
    });
    describe('suite', () => {
      afterEach(() => {
        vi.useFakeTimers();
      });
      afterAll(() => {
        new Date();
      });
      it('t', () => {
        new Date();
      });
    });
  `);
  assertFlagged(result, "frozenTime");
});

test("reports a test read when a describe body restores real timers", async () => {
  const result = await runOxlint(["frozenTime"], `
    vi.useFakeTimers();
    describe('suite', () => {
      vi.useRealTimers();
      it('t', () => {
        new Date();
      });
    });
  `);
  assertFlagged(result, "frozenTime");
});

test("reports a read in an IIFE argument that evaluates before the freeze", async () => {
  const result = await runOxlint(["frozenTime"], `
    (() => vi.useFakeTimers())(Date.now());
  `);
  assertFlagged(result, "frozenTime");
});

test("reports a test read when a module-level restore follows the describe freeze", async () => {
  const result = await runOxlint(["frozenTime"], `
    describe('s', () => {
      vi.useFakeTimers();
      it('t', () => Date.now());
    });
    vi.useRealTimers();
  `);
  assertFlagged(result, "frozenTime");
});

test("reports a read in a block-bodied IIFE argument evaluated before the freeze", async () => {
  const result = await runOxlint(["frozenTime"], `
    (() => {
      vi.useFakeTimers();
    })(Date.now());
  `);
  assertFlagged(result, "frozenTime");
});

test("allows a read inside an IIFE body that follows the freeze", async () => {
  const result = await runOxlint(["frozenTime"], `
    it('t', () => {
      (() => {
        vi.useFakeTimers();
        Date.now();
      })();
    });
  `);
  assertClean(result, "frozenTime");
});

test("reports a read in an afterAll that follows a restoring afterAll", async () => {
  const result = await runOxlint(["frozenTime"], `
    beforeAll(() => {
      vi.useFakeTimers();
    });
    afterAll(() => {
      vi.useRealTimers();
    });
    afterAll(() => {
      Date.now();
    });
  `);
  assertFlagged(result, "frozenTime");
});

test("reports a file-level read when a sibling describe restores real timers", async () => {
  const result = await runOxlint(["frozenTime"], `
    vi.useFakeTimers();
    describe('setup', () => {
      vi.useRealTimers();
    });
    it('t', () => Date.now());
  `);
  assertFlagged(result, "frozenTime");
});

test("allows a read covered by a freeze inside the same helper", async () => {
  const result = await runOxlint(["frozenTime"], `
    it('t', () => {
      function helper() {
        vi.useFakeTimers();
        Date.now();
      }
      helper();
    });
  `);
  assertClean(result, "frozenTime");
});

test("allows a read in an inner afterAll before the outer restoration", async () => {
  const result = await runOxlint(["frozenTime"], `
    describe('outer', () => {
      beforeAll(() => {
        vi.useFakeTimers();
      });
      afterAll(() => {
        vi.useRealTimers();
      });
      describe('inner', () => {
        afterAll(() => {
          Date.now();
        });
        it('t', () => Date.now());
      });
    });
  `);
  assertClean(result, "frozenTime");
});

test("allows a read after an IIFE whose argument restores first", async () => {
  const result = await runOxlint(["frozenTime"], `
    it('t', () => {
      (() => vi.useFakeTimers())(vi.useRealTimers());
      Date.now();
    });
  `);
  assertClean(result, "frozenTime");
});

test("allows a read after an IIFE that restores and then freezes", async () => {
  const result = await runOxlint(["frozenTime"], `
    it('t', () => {
      (() => {
        vi.useRealTimers();
        vi.useFakeTimers();
      })();
      Date.now();
    });
  `);
  assertClean(result, "frozenTime");
});

test("reports a teardown read after a test restores real timers", async () => {
  const result = await runOxlint(["frozenTime"], `
    beforeEach(() => {
      vi.useFakeTimers();
    });
    it('t', () => {
      vi.useRealTimers();
    });
    afterEach(() => {
      Date.now();
    });
  `);
  assertFlagged(result, "frozenTime");
});

test("reports a teardown read when the teardown is declared above the test", async () => {
  const result = await runOxlint(["frozenTime"], `
    beforeEach(() => {
      vi.useFakeTimers();
    });
    afterEach(() => {
      Date.now();
    });
    it('t', () => {
      vi.useRealTimers();
    });
  `);
  assertFlagged(result, "frozenTime");
});

test("orders nested IIFE transitions by their invocation statements", async () => {
  const result = await runOxlint(["frozenTime"], `
    it('t', () => {
      (() => {
        vi.useRealTimers();
        (() => vi.useFakeTimers())();
      })();
      Date.now();
    });
  `);
  assertClean(result, "frozenTime");
});

test("a file-level teardown sees restores from tests inside describes", async () => {
  const result = await runOxlint(["frozenTime"], `
    beforeEach(() => {
      vi.useFakeTimers();
    });
    afterEach(() => {
      Date.now();
    });
    describe('s', () => {
      it('t', () => {
        vi.useRealTimers();
      });
    });
  `);
  assertFlagged(result, "frozenTime");
});

test("nested IIFE argument ordering survives an outer invocation frame", async () => {
  const result = await runOxlint(["frozenTime"], `
    it('t', () => {
      (() => {
        (() => vi.useFakeTimers())(vi.useRealTimers());
      })();
      Date.now();
    });
  `);
  assertClean(result, "frozenTime");
});

test("a preceding teardown refreeze covers the final teardown read", async () => {
  const result = await runOxlint(["frozenTime"], `
    describe('s', () => {
      beforeEach(() => {
        vi.useFakeTimers();
      });
      afterEach(() => {
        vi.useRealTimers();
      });
      afterEach(() => {
        vi.useFakeTimers();
      });
      afterEach(() => {
        Date.now();
      });
      it('t', () => {
        Date.now();
      });
    });
  `);
  assertClean(result, "frozenTime");
});

test("a sibling suite's hook write does not cover its own suite only", async () => {
  const result = await runOxlint(["frozenTime"], `
    beforeEach(() => {
      vi.useFakeTimers();
    });
    describe('s', () => {
      afterEach(() => {
        Date.now();
      });
      it('t', () => {
        vi.useRealTimers();
      });
    });
  `);
  assertFlagged(result, "frozenTime");
});

test("a nested afterEach restoration unfreezes a file-level teardown read", async () => {
  const result = await runOxlint(["frozenTime"], `
    beforeEach(() => {
      vi.useFakeTimers();
    });
    afterEach(() => {
      Date.now();
    });
    describe('s', () => {
      afterEach(() => {
        vi.useRealTimers();
      });
      it('t', () => {});
    });
  `);
  assertFlagged(result, "frozenTime");
});

test("a test's own freeze keeps the teardown read covered", async () => {
  const result = await runOxlint(["frozenTime"], `
    describe('s', () => {
      beforeEach(() => {
        vi.useFakeTimers();
      });
      it('t', () => {
        vi.useFakeTimers();
      });
      afterEach(() => {
        Date.now();
      });
    });
  `);
  assertClean(result, "frozenTime");
});

test("reports a teardown read when nothing froze the clock", async () => {
  const result = await runOxlint(["frozenTime"], `
    it('t', () => {});
    afterEach(() => {
      Date.now();
    });
  `);
  assertFlagged(result, "frozenTime");
});

test("reports an afterAll read when nothing froze the clock", async () => {
  const result = await runOxlint(["frozenTime"], `
    it('t', () => {});
    afterAll(() => {
      Date.now();
    });
  `);
  assertFlagged(result, "frozenTime");
});

test("replays a beforeAll restoration before a later beforeEach freeze", async () => {
  const result = await runOxlint(["frozenTime"], `
    describe('s', () => {
      beforeEach(() => {
        vi.useFakeTimers();
      });
      beforeAll(() => {
        vi.useRealTimers();
      });
      it('t', () => {});
      afterEach(() => {
        Date.now();
      });
    });
  `);
  assertClean(result, "frozenTime");
});

test("an earlier file-level afterAll restoration unfreezes a later afterAll read", async () => {
  const result = await runOxlint(["frozenTime"], `
    beforeAll(() => {
      vi.useFakeTimers();
    });
    afterAll(() => {
      vi.useRealTimers();
    });
    afterAll(() => {
      Date.now();
    });
    it('t', () => {});
  `);
  assertFlagged(result, "frozenTime");
});

test("an afterAll read sees the suite's final state, not every test's", async () => {
  const result = await runOxlint(["frozenTime"], `
    describe('s', () => {
      beforeAll(() => {
        vi.useFakeTimers();
      });
      it('first', () => {
        vi.useRealTimers();
      });
      it('second', () => {
        vi.useFakeTimers();
      });
      afterAll(() => {
        Date.now();
      });
    });
  `);
  assertClean(result, "frozenTime");
});

test("does not treat a computed member named like a restoration as one", async () => {
  const result = await runOxlint(["frozenTime"], `
    const useRealTimers = 'clearAllMocks';
    beforeEach(() => {
      vi.useFakeTimers();
    });
    it('t', () => {
      vi[useRealTimers]();
      expect(Date.now()).toBeGreaterThan(0);
    });
  `);
  assertClean(result, "frozenTime");
});

// An afterAll callback runs once after the whole suite, so it observes the
// accumulated state: the first test's restoration stands unless a later
// test (or its beforeEach) refreezes, and the once-only beforeAll freeze
// must not be re-applied when reconstructing the final state.
test("an afterAll read observes the suite's accumulated state", async () => {
  const result = await runOxlint(["frozenTime"], `
    beforeAll(() => {
      vi.useFakeTimers();
    });
    it('a', () => {
      vi.useRealTimers();
    });
    it('b', () => {});
    afterAll(() => {
      Date.now();
    });
  `);
  assertFlagged(result, "frozenTime");
});

// A named helper invoked explicitly runs its body where the call sits, so
// its freeze covers the reads after the invocation — a helper that is
// merely declared still executes nothing.
test("an explicitly invoked helper's freeze covers the following reads", async () => {
  const result = await runOxlint(["frozenTime"], `
    it('t', () => {
      function freeze() {
        vi.useFakeTimers();
      }
      freeze();
      Date.now();
    });
  `);
  assertClean(result, "frozenTime");
});

// Each beforeAll callback runs once before the suite's first test: a
// sibling beforeAll of the same suite still runs after the first one.
test("multiple beforeAll callbacks apply in registration order", async () => {
  const result = await runOxlint(["frozenTime"], `
    describe('s', () => {
      beforeAll(() => {
        vi.useRealTimers();
      });
      beforeAll(() => {
        vi.useFakeTimers();
      });
      it('t', () => {});
      afterAll(() => {
        Date.now();
      });
    });
  `);
  assertClean(result, "frozenTime");
});

// A parameter shadows an outer helper of the same name: a call through the
// parameter is not a call through the helper, so the outer freeze never
// executes and the read stays on the wall clock.
test("a parameter shadowing a freezing helper stops helper resolution", async () => {
  const result = await runOxlint(["frozenTime"], `
    function freeze() {
      vi.useFakeTimers();
    }
    function run(freeze) {
      freeze();
    }
    it('t', () => {
      run(() => {});
      Date.now();
    });
  `);
  assertFlagged(result, "frozenTime");
});

// A block-local binding shadows an outer helper only inside its block: the
// invocation after the block resolves to the freezing function.
test("a block-local shadow does not outlive its block", async () => {
  const result = await runOxlint(["frozenTime"], `
    it('t', () => {
      function freeze() {
        vi.useFakeTimers();
      }
      {
        const freeze = () => {};
      }
      freeze();
      Date.now();
    });
  `);
  assertClean(result, "frozenTime");
});

// A defaulted parameter shadows the outer helper like a plain one: the
// lookup must read AssignmentPattern's left binding.
test("a defaulted parameter shadowing a freezing helper stops resolution", async () => {
  const result = await runOxlint(["frozenTime"], `
    function freeze() {
      vi.useFakeTimers();
    }
    function run(freeze = () => {}) {
      freeze();
    }
    it('t', () => {
      run();
      Date.now();
    });
  `);
  assertFlagged(result, "frozenTime");
});

// A destructured binding shadows the outer helper like any other binding.
test("a destructured shadowing binding stops helper resolution", async () => {
  const result = await runOxlint(["frozenTime"], `
    function freeze() {
      vi.useFakeTimers();
    }
    it('t', () => {
      const { freeze } = { freeze: () => {} };
      freeze();
      Date.now();
    });
  `);
  assertFlagged(result, "frozenTime");
});

// Function declarations are initialized at block entry: an invocation
// textually above the declaration inside the same block resolves.
test("a block hoists its function declarations to the block entry", async () => {
  const result = await runOxlint(["frozenTime"], `
    it('t', () => {
      {
        freeze();
        function freeze() {
          vi.useFakeTimers();
        }
      }
      Date.now();
    });
  `);
  assertClean(result, "frozenTime");
});

// A read in a helper invocation's argument runs before the helper's body:
// the beforeEach freeze still covers it, and the body's restoration has
// not happened yet.
test("an argument read is judged before the invoked helper's body", async () => {
  const result = await runOxlint(["frozenTime"], `
    beforeEach(() => {
      vi.useFakeTimers();
    });
    it('t', () => {
      function restore() {
        vi.useRealTimers();
      }
      restore(Date.now());
    });
  `);
  assertClean(result, "frozenTime");
});

// A reassignment replaces the callable a binding holds: the invocation
// resolves to the callable held at that point, and its freeze applies.
test("an invoked replacement callable applies its transitions", async () => {
  const result = await runOxlint(["frozenTime"], `
    it('t', () => {
      let freeze = () => {};
      freeze = () => vi.useFakeTimers();
      freeze();
      Date.now();
    });
  `);
  assertClean(result, "frozenTime");
});

// A reassignment captured inside an UNCALLED helper stays isolated: the
// invoked callable is still the original no-op.
test("an uncalled helper's reassignment does not change the registry", async () => {
  const result = await runOxlint(["frozenTime"], `
    let freeze = () => {};
    function init() {
      freeze = () => vi.useFakeTimers();
    }
    it('t', () => {
      freeze();
      Date.now();
    });
  `);
  assertFlagged(result, "frozenTime");
});

// A loop-header binding lives in the loop's own scope: after the loop
// exits, the outer freezing function resolves again.
test("a loop-header shadow expires with the loop", async () => {
  const result = await runOxlint(["frozenTime"], `
    it('t', () => {
      function freeze() {
        vi.useFakeTimers();
      }
      for (let freeze of [() => {}]) {
        freeze();
      }
      freeze();
      Date.now();
    });
  `);
  assertClean(result, "frozenTime");
});

// A `var` binding outlives the block it is declared in: the call after the
// block resolves it and its freeze applies.
test("a var-declared callable outlives its block", async () => {
  const result = await runOxlint(["frozenTime"], `
    it('t', () => {
      {
        var freeze = () => vi.useFakeTimers();
      }
      freeze();
      Date.now();
    });
  `);
  assertClean(result, "frozenTime");
});

// A module-level helper invoked from a hook contributes its transitions to
// that hook: the freeze covers the tests the hook precedes.
test("a hook invoking a module-level freezing helper covers its tests", async () => {
  const result = await runOxlint(["frozenTime"], `
    function freeze() {
      vi.useFakeTimers();
    }
    beforeEach(() => {
      freeze();
    });
    it('t', () => {
      Date.now();
    });
  `);
  assertClean(result, "frozenTime");
});

// An IIFE's argument runs before its body: a freeze in the argument
// protects the body's reads even though the source order says otherwise.
test("an IIFE argument freeze covers the body's reads", async () => {
  const result = await runOxlint(["frozenTime"], `
    it('t', () => {
      (() => {
        Date.now();
      })(vi.useFakeTimers());
    });
  `);
  assertClean(result, "frozenTime");
});

// A named helper's body runs after the invocation's arguments: a read in
// the argument happens before the freeze and stays on the wall clock.
test("a read in a helper invocation's argument precedes the freeze", async () => {
  const result = await runOxlint(["frozenTime"], `
    function freeze() {
      vi.useFakeTimers();
    }
    it('t', () => {
      freeze(Date.now());
    });
  `);
  assertFlagged(result, "frozenTime");
});

// afterEach executions carry the timer state forward: the first teardown's
// restoration leaves the second teardown on the real clock.
test("an afterEach read sees the state earlier executions left behind", async () => {
  const result = await runOxlint(["frozenTime"], `
    beforeAll(() => {
      vi.useFakeTimers();
    });
    afterEach(() => {
      Date.now();
      vi.useRealTimers();
    });
    it('a', () => {});
    it('b', () => {});
  `);
  assertFlagged(result, "frozenTime");
});
