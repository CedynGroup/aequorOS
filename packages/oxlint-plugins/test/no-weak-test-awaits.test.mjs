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

test("allows a clean helper invoked through .call and as a map callback", async () => {
  const result = await runOxlint(["weakAwaits"], `
    function loadData() {
      return fetch('/data');
    }
    it('t', async () => {
      await loadData.call(null);
      await Promise.all(urls.map(loadData));
    });
  `);
  assertClean(result, "weakAwaits");
});

test("keeps same-name helpers in sibling suites apart", async () => {
  // The second suite's wait is weak (its known finding is suppressed); the
  // first suite's await must stay clean even though the second helper
  // reuses the name.
  const result = await runOxlint(["weakAwaits"], `
    describe('a', () => {
      function wait() {
        return Promise.resolve('ready');
      }
      it('first', async () => {
        await wait();
      });
    });
    describe('b', () => {
      function wait() {
        return new Promise((resolve) => setTimeout(resolve, 100));
      }
      it('second', async () => {
        // oxlint-disable-next-line acts/no-weak-test-awaits -- pinned weak wait
        await wait();
      });
    });
  `);
  assertClean(result, "weakAwaits");
});

test("does not crash on an anonymous default-exported function", async () => {
  const result = await runOxlint(["weakAwaits"], `
    export default function () {}
    it('t', async () => {
      await new Promise((resolve) => setTimeout(resolve, 100));
    });
  `);
  assertFlagged(result, "weakAwaits");
});

test("lets a helper's local declaration shadow an outer weak helper", async () => {
  const result = await runOxlint(["weakAwaits"], `
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    void sleep;
    function ready() {
      const sleep = () => Promise.resolve('ready');
      return sleep();
    }
    it('t', async () => {
      await ready();
    });
  `);
  assertClean(result, "weakAwaits");
});

test("resolves an alias created by assignment without crashing", async () => {
  const result = await runOxlint(["weakAwaits"], `
    function loadData() {
      return fetch('/data');
    }
    it('t', async () => {
      let alias;
      alias = loadData;
      await alias();
    });
  `);
  assertClean(result, "weakAwaits");
});

test("does not let a block-local declaration shadow the whole helper body", async () => {
  const result = await runOxlint(["weakAwaits"], `
    const sleep = () => new Promise((resolve) => setTimeout(resolve, 10));
    function wait() {
      {
        const sleep = () => Promise.resolve('ready');
      }
      return sleep();
    }
    it('t', async () => {
      await wait();
    });
  `);
  assertFlagged(result, "weakAwaits");
});

test("classifies an alias against its target's defining scope", async () => {
  const result = await runOxlint(["weakAwaits"], `
    function load() {
      return fetchData();
    }
    it('t', async () => {
      const fetchData = () => new Promise((resolve) => setTimeout(resolve, 100));
      const alias = load;
      await alias();
    });
  `);
  assertClean(result, "weakAwaits");
});

test("preserves a helper's defining scope through an alias", async () => {
  const result = await runOxlint(["weakAwaits"], `
    function ready() {
      return loadReady();
    }
    const loadReady = () => Promise.resolve('ready');
    it('t', async () => {
      await ready();
    });
  `);
  assertClean(result, "weakAwaits");
});

test("keeps same-name helpers in different scopes apart", async () => {
  // The nested describe-level wait shadows the module-level one; the outer
  // test awaits the clean module helper and must not inherit the nested
  // classification (the nested weak body is never awaited, so nothing else
  // fires).
  const clean = await runOxlint(["weakAwaits"], `
    function wait(ms) {
      return loadReady(ms);
    }
    describe('suite', () => {
      function wait(ms) {
        return sleep(ms);
      }
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      const tainted = () => wait(100);
    });
    it('outer', async () => {
      await wait(100);
    });
  `);
  assertClean(clean, "weakAwaits");
  // The nested helper itself is weak where it is in scope.
  const flagged = await runOxlint(["weakAwaits"], `
    describe('suite', () => {
      function wait(ms) {
        return sleep(ms);
      }
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      it('inner', async () => {
        await wait(100);
      });
    });
  `);
  assertFlagged(flagged, "weakAwaits");
});

test("resolves an unresolved alias's dependency when it becomes known", async () => {
  const result = await runOxlint(["weakAwaits"], `
    function wait(ms) {
      return sleep(ms);
    }
    const pause = wait;
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    it('t', async () => {
      await pause(100);
    });
  `);
  assertFlagged(result, "weakAwaits");
});

test("lets a helper see a dependency declared later in its own scope", async () => {
  const result = await runOxlint(["weakAwaits"], `
    it('t', async () => {
      function wait(ms) {
        return sleep(ms);
      }
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      await wait(100);
    });
  `);
  assertFlagged(result, "weakAwaits");
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


test("does not report an await when a hook replaces its temporary assignment", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let sleep;
    function loadData() {
      return fetch('/data');
    }
    beforeEach(() => {
      sleep = () => new Promise((resolve) => setTimeout(resolve, 10));
      sleep = loadData;
    });
    it('t', async () => {
      await sleep();
    });
  `);
  assertClean(result, "weakAwaits");
});

test("flags an await of a bound timer helper", async () => {
  const result = await runOxlint(["weakAwaits"], `
    it('t', async () => {
      const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
      await sleep.bind(null, 10)();
    });
  `);
  assertFlagged(result, "weakAwaits");
});

test("classifies long forwarding chains without exponential work", async () => {
  const helpers = Array.from({ length: 16 }, (_, index) =>
    index === 0
      ? "function f0() { return fetch('/data'); }"
      : `function f${index}() { return f${index - 1}() || f${index - 1}(); }`,
  ).join("\n");
  const result = await runOxlint(["weakAwaits"], `
    ${helpers}
    it('t', async () => {
      await f16();
    });
  `);
  assertClean(result, "weakAwaits");
});

test("reports an await covered by an IIFE assignment before the test", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let sleep;
    (() => {
      sleep = () => new Promise((resolve) => setTimeout(resolve, 10));
    })();
    it('t', async () => {
      await sleep();
    });
  `);
  assertFlagged(result, "weakAwaits");
});

test("reports an await of a sequence-expression callee", async () => {
  const result = await runOxlint(["weakAwaits"], `
    it('t', async () => {
      const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
      await (0, sleep)(10);
    });
  `);
  assertFlagged(result, "weakAwaits");
});

test("reports an await covered by a describe-callback assignment", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let sleep;
    describe('setup', () => {
      sleep = () => new Promise((resolve) => setTimeout(resolve, 10));
    });
    it('t', async () => {
      await sleep();
    });
  `);
  assertFlagged(result, "weakAwaits");
});

test("lets a hook replace an unresolved helper with a clean one", async () => {
  const result = await runOxlint(["weakAwaits"], `
    function wait() {
      return new Promise((resolve) => setTimeout(resolve, 10));
    }
    function load() {
      return fetch('/data');
    }
    beforeEach(() => {
      wait = load;
    });
    it('t', async () => {
      await wait();
    });
  `);
  assertClean(result, "weakAwaits");
});

test("a sibling suite's hook does not taint an earlier suite's await", async () => {
  const result = await runOxlint(["weakAwaits"], `
    function load() {
      return fetch('/data');
    }
    describe('first', () => {
      it('t', async () => {
        await load();
      });
    });
    describe('second', () => {
      beforeEach(() => {
        load = () => new Promise((resolve) => setTimeout(resolve, 10));
      });
      it('t', async () => {
        await load();
      });
    });
  `);
  assertFlagged(result, "weakAwaits");
});
