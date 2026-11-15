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

test("reports a helper declared before its weak dependency", async () => {
  const result = await runOxlint(["weakAwaits"], `
    function wait(ms) {
      return sleep(ms);
    }
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    it('t', async () => {
      await wait(100);
    });
  `);
  assertFlagged(result, "weakAwaits");
});

test("resolves an unresolved helper's dependency in its defining scope", async () => {
  const result = await runOxlint(["weakAwaits"], `
    function wait(ms) {
      return sleep(ms);
    }
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    it('t', async () => {
      const sleep = (ms) => loadReady(ms);
      await wait(100);
    });
  `);
  assertFlagged(result, "weakAwaits");
});

test("allows an unresolved pure helper handed to a callback invoker", async () => {
  const result = await runOxlint(["weakAwaits"], `
    function toCapability(value) {
      return { module: value };
    }
    it('t', async () => {
      await Promise.all([1, 2].map(toCapability));
      await fc.assert(fc.asyncProperty(fc.integers(), async (n) => {
        await screen.findAllByText(String(n));
      }));
    });
  `);
  assertClean(result, "weakAwaits");
});

test("still allows a parameter that shadows an unresolved helper's name", async () => {
  const result = await runOxlint(["weakAwaits"], `
    const wait = (ms) => new Promise((r) => setTimeout(r, ms));
    function ready(wait) {
      return wait();
    }
    it('t', async () => {
      await ready(loadReady());
    });
  `);
  assertClean(result, "weakAwaits");
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

// -- Hardening findings (PR #899 review round) ------------------------------

// 4567895306: a declared sleep helper that is only REFERENCED (never called)
// inside an awaited expression is not a weak wait.
test("does not flag a helper that is only referenced, never invoked", async () => {
  const result = await runOxlint(["weakAwaits"], `
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    it('t', async () => {
      await vi.waitFor(() => expect(sleep).toBeDefined());
      expect(true).toBe(true);
    });
  `);
  assertClean(result, "weakAwaits");
});

// 4567941738: a noncomputed property named like the helper is a property name,
// not a reference to the helper.
test("does not flag a property that shares a weak helper's name", async () => {
  const result = await runOxlint(["weakAwaits"], `
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    it('t', async () => {
      await vi.waitFor(() => expect(state.sleep).toBe(false));
      expect(true).toBe(true);
    });
  `);
  assertClean(result, "weakAwaits");
});

// 4567941776: a forward-declared helper wrapping a later-resolved timer helper
// must be reported regardless of declaration order.
test("reports a forward-declared helper wrapping a timer helper", async () => {
  const result = await runOxlint(["weakAwaits"], `
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    it('t', async () => {
      await wait(100);
    });
    function wait(ms) {
      return sleep(ms);
    }
  `);
  assertFlagged(result, "weakAwaits");
});

// 4567941793: assignments inside uncalled functions do not change the
// enclosing helper classification.
test("does not let an uncalled function's reassignment clear a weak helper", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    function reset() {
      sleep = loadReady;
    }
    it('t', async () => {
      await sleep(100);
    });
  `);
  assertFlagged(result, "weakAwaits");
});

// CodeRabbit hardening round: a computed property's VALUE is live code, so a
// timer promise stored under a computed key is still a weak wait.
test("reports a timer promise held in a computed property value", async () => {
  const result = await runOxlint(["weakAwaits"], `
    it('t', async () => {
      await ({ ['pending']: new Promise((r) => setTimeout(r, 100)) }).pending;
    });
  `);
  assertFlagged(result, "weakAwaits");
});

// -- Review wave (2026-11-14/15, PR #922) ----------------------------------

// PRRT_kwDOSK8LJc63jdsC: a reassignment AFTER an awaited expression must not
// retroactively make the earlier await weak.
test("does not retroactively report an await of a later-reassigned binding", async () => {
  const result = await runOxlint(["weakAwaits"], `
    it('t', async () => {
      let wait = loadData;
      await wait();
      wait = () => new Promise((r) => setTimeout(r, 10));
      expect(wait).toBeDefined();
    });
  `);
  assertClean(result, "weakAwaits");
});

// PRRT_kwDOSK8LJc63jdsE: a setup hook's assignment to an outer binding executes
// before the tests, so the hook-initialized helper stays enforced.
test("reports a weak helper assigned by a setup hook", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let sleep;
    beforeEach(() => {
      sleep = () => new Promise((r) => setTimeout(r, 10));
    });
    it('t', async () => {
      await sleep();
    });
  `);
  assertFlagged(result, "weakAwaits");
});

// PRRT_kwDOSK8LJc63jdsJ: a timer helper passed to a callback-invoking array method
// waits exclusively on timer promises.
test("reports a weak helper invoked as an array callback", async () => {
  const result = await runOxlint(["weakAwaits"], `
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    it('t', async () => {
      await Promise.all([10, 20].map(sleep));
    });
  `);
  assertFlagged(result, "weakAwaits");
});

// PRRT_kwDOSK8LJc63jg-M + PRRT_kwDOSK8LJc63jzd2: sleep.call/sleep.apply invoke the helper
// and return its timer promise.
test("reports a weak helper invoked through call and apply", async () => {
  const flagged = await runOxlint(["weakAwaits"], `
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    it('t', async () => {
      await sleep.call(null, 100);
    });
  `);
  assertFlagged(flagged, "weakAwaits");
  const applied = await runOxlint(["weakAwaits"], `
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    it('t', async () => {
      await sleep.apply(null, [10]);
    });
  `);
  assertFlagged(applied, "weakAwaits");
});

// PRRT_kwDOSK8LJc63jg-Q: an assignment inside the test body reclassifies the
// captured binding for the awaits that follow it.
test("reports a captured helper reassigned inside the test body", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let pause = () => loadReady();
    it('t', async () => {
      pause = (ms) => new Promise((r) => setTimeout(r, ms));
      await pause(100);
    });
  `);
  assertFlagged(result, "weakAwaits");
});

// Review round: a `var` inside a hook callback must not clear the module
// helper's classification.
test("keeps a module helper visible beside a var redeclaration in a hook", async () => {
  const result = await runOxlint(["weakAwaits"], `
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    beforeEach(() => {
      var sleep = loadReady;
    });
    it('t', async () => {
      await sleep(100);
    });
  `);
  assertFlagged(result, "weakAwaits");
});

// Review round: a later helper declaration must not reclassify an await that
// already ran under the earlier binding value.
test("keeps the earlier binding value for an await before a reassignment", async () => {
  const result = await runOxlint(["weakAwaits"], `
    it('t', async () => {
      let wait = loadData;
      await wait();
      wait = (ms) => new Promise((r) => setTimeout(r, 10));
      function other() {
        return new Promise((r) => setTimeout(r, 10));
      }
    });
  `);
  assertClean(result, "weakAwaits");
});

// Review round: member-style hooks isolate their assignments like bare hooks.
test("reports an assignment inside a member-style setup hook", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let sleep;
    test.beforeEach(() => {
      sleep = (ms) => new Promise((r) => setTimeout(r, 10));
    });
    it('t', async () => {
      await sleep(100);
    });
  `);
  assertFlagged(result, "weakAwaits");
});

// Review round: a hook assignment updates the nearest visible binding, so a
// suite-level shadow wins over the module binding.
test("updates a suite-level shadow before the module binding from a hook", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let sleep = () => loadReady();
    describe('suite', () => {
      let sleep = () => loadReady();
      beforeEach(() => {
        sleep = (ms) => new Promise((r) => setTimeout(r, 10));
      });
      it('t', async () => {
        await sleep(100);
      });
    });
  `);
  assertFlagged(result, "weakAwaits");
});

