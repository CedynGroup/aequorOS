/** ACTS002 acts/no-weak-test-awaits — runs the real pinned oxlint binary. */
import test from "node:test";
import { assertClean, assertFlagged, runOxlint } from "./helpers.mjs";

test("plugin exposes the ACTS002 rule id", async () => {
  const result = await runOxlint(["weakAwaits"], "it('t', async () => { await Promise.resolve(); });\n");
  assertFlagged(result, "weakAwaits");
});

test("reports await new Promise(r => setTimeout(r, n))", async () => {
  const result = await runOxlint(["weakAwaits"], `
    it('t', async () => {
      await new Promise((r) => setTimeout(r, 100));
      expect(true).toBe(true);
    });
  `);
  assertFlagged(result, "weakAwaits");
});

test("reports globalThis/window/global setTimeout forms", async () => {
  for (const receiver of ["globalThis", "window", "global"]) {
    const result = await runOxlint(["weakAwaits"], `
      it('t', async () => {
        await new Promise((r) => ${receiver}.setTimeout(r, 100));
      });
    `);
    assertFlagged(result, "weakAwaits");
  }
});

test("reports awaits through an aliased helper", async () => {
  const result = await runOxlint(["weakAwaits"], `
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    function wait(ms) {
      return new Promise((r) => setTimeout(r, ms));
    }
    it('t', async () => {
      await sleep(100);
      await wait(100);
      expect(true).toBe(true);
    });
  `);
  assertFlagged(result, "weakAwaits");
});

test("reports awaits through a chain of aliases", async () => {
  const result = await runOxlint(["weakAwaits"], `
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    const pause = sleep;
    it('t', async () => {
      await pause(100);
    });
  `);
  assertFlagged(result, "weakAwaits");
});

test("reports a bare await Promise.resolve() statement", async () => {
  const result = await runOxlint(["weakAwaits"], `
    it('t', async () => {
      await Promise.resolve();
      expect(true).toBe(true);
    });
  `);
  assertFlagged(result, "weakAwaits");
});

test("allows a promise resolved by an event, not a timer", async () => {
  const result = await runOxlint(["weakAwaits"], `
    it('t', async () => {
      await new Promise((resolve) => emitter.once('ready', resolve));
      expect(true).toBe(true);
    });
  `);
  assertClean(result, "weakAwaits");
});

test("allows awaited real conditions", async () => {
  const result = await runOxlint(["weakAwaits"], `
    it('t', async () => {
      await expect(page.getByText('Ready')).toBeVisible();
      await vi.waitFor(() => expect(rows()).toHaveLength(3));
      await Promise.all([a(), b()]);
      const value = await loadValue();
      expect(value).toBe(1);
    });
  `);
  assertClean(result, "weakAwaits");
});

test("allows awaiting a resolved promise bound to a value", async () => {
  const result = await runOxlint(["weakAwaits"], `
    it('t', async () => {
      const value = await Promise.resolve(1);
      expect(value).toBe(1);
    });
  `);
  assertClean(result, "weakAwaits");
});

test("allows a standalone await of Promise.resolve with a real value", async () => {
  const result = await runOxlint(["weakAwaits"], `
    it('t', async () => {
      await Promise.resolve(loadReady());
      expect(true).toBe(true);
    });
  `);
  assertClean(result, "weakAwaits");
});

test("allows a helper whose parameter shadows a weak helper name", async () => {
  const result = await runOxlint(["weakAwaits"], `
    const wait = (ms) => new Promise((r) => setTimeout(r, ms));
    function ready(wait) {
      return wait();
    }
    it('t', async () => {
      await ready(loadReady());
      expect(true).toBe(true);
    });
  `);
  assertClean(result, "weakAwaits");
});

test("a disable comment with a reason suppresses the finding", async () => {
  const result = await runOxlint(["weakAwaits"], `
    it('t', async () => {
      // oxlint-disable-next-line acts/no-weak-test-awaits -- must outlive the 1s idle-in-transaction timeout (#847)
      await new Promise((r) => setTimeout(r, 1500));
      expect(true).toBe(true);
    });
  `);
  assertClean(result, "weakAwaits");
});
