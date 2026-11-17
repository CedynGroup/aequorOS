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

test("two sibling hooks keep each suite's own write", async () => {
  const result = await runOxlint(["weakAwaits"], `
    function load() {
      return fetch('/data');
    }
    describe('first', () => {
      beforeEach(() => {
        load = () => fetch('/clean');
      });
      it('t', async () => {
        await load();
      });
    });
    describe('second', () => {
      beforeEach(() => {
        load = () => fetch('/also-clean');
      });
      it('t', async () => {
        await load();
      });
    });
  `);
  assertClean(result, "weakAwaits");
});

// -- CodeRabbit round (2026-11-17, teardown/replay soundness) ---------------

// A setup hook registered after the test still runs before it, so a clean
// replacement must unreport the await; the verdict is deferred to the final
// hook replay instead of being fixed at the visit.
test("does not report an await a later-registered hook replaces with a clean one", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    it('t', async () => {
      await sleep(10);
    });
    beforeEach(() => {
      sleep = () => fetch('/data');
    });
  `);
  assertClean(result, "weakAwaits");
});

// Binding produces a function value; awaiting the bound expression waits on
// nothing. Only invoking the bound result waits on the timer promise.
test("does not flag an await of a bound helper that is never invoked", async () => {
  const result = await runOxlint(["weakAwaits"], `
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    it('t', async () => {
      await sleep.bind(null, 10);
    });
  `);
  assertClean(result, "weakAwaits");
});

// A block-local declaration shadows an outer weak helper only inside its
// block; the helper's call inside the block resolves to the local binding.
test("resolves a call inside a block to the block-local shadow", async () => {
  const result = await runOxlint(["weakAwaits"], `
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    function ready() {
      {
        const sleep = () => fetch('/data');
        return sleep();
      }
    }
    it('t', async () => {
      await ready();
    });
  `);
  assertClean(result, "weakAwaits");
});

// Setup hooks replay in execution order: a beforeAll declared after a
// beforeEach still runs first, so the beforeEach's timer assignment is the
// value the test observes.
test("replays setup hooks in execution order, not registration order", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let wait;
    beforeEach(() => {
      wait = (ms) => new Promise((r) => setTimeout(r, ms));
    });
    beforeAll(() => {
      wait = () => fetch('/data');
    });
    it('t', async () => {
      await wait();
    });
  `);
  assertFlagged(result, "weakAwaits");
});

// A forwarding helper's weakness depends on its dependency's current value:
// a hook reassigning the dependency re-classifies the helper at the await.
test("re-resolves a forwarding helper's dependency reassigned by a hook", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let dependency = (ms) => new Promise((r) => setTimeout(r, ms));
    function wait() {
      return dependency();
    }
    beforeEach(() => {
      dependency = () => fetch('/data');
    });
    it('t', async () => {
      await wait();
    });
  `);
  assertClean(result, "weakAwaits");
});

// Sibling hooks assigning unresolved helpers to one binding must replay the
// declaration-registry entry together with the binding value: suite A's
// alias of the clean helper survives suite B's write.
test("a sibling suite's alias write does not leak its declaration entry", async () => {
  const flagged = await runOxlint(["weakAwaits"], `
    function clean() {
      return fetch('/data');
    }
    function timer() {
      return sleep();
    }
    let sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    let wait = clean;
    describe('A', () => {
      beforeEach(() => {
        wait = clean;
      });
      it('t', async () => {
        await wait();
      });
    });
    describe('B', () => {
      beforeEach(() => {
        wait = timer;
      });
      it('t', async () => {
        await wait();
      });
    });
  `);
  // Suite B's await is legitimately weak (its beforeEach leaves the timer
  // forwarding helper in place).
  assertFlagged(flagged, "weakAwaits");
  const cleanSuite = await runOxlint(["weakAwaits"], `
    function clean() {
      return fetch('/data');
    }
    function timer() {
      return sleep();
    }
    let sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    let wait = clean;
    describe('A', () => {
      beforeEach(() => {
        wait = clean;
      });
      it('t', async () => {
        await wait();
      });
    });
    describe('B', () => {
      beforeEach(() => {
        wait = timer;
      });
    });
  `);
  // With B's await removed, no finding may remain: A's replay must resolve
  // through the clean helper's declaration, not the last sibling's.
  assertClean(cleanSuite, "weakAwaits");
});

// A write from a setup hook that executes AFTER the pending's own hook must
// not reach its awaits: a beforeEach assignment has not happened while a
// beforeAll's callback runs.
test("does not apply a hook write that executes after the pending's hook", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let sleep = () => new Promise((r) => setTimeout(r, 10));
    beforeAll(async () => {
      await sleep();
    });
    beforeEach(() => {
      sleep = () => fetch('/data');
    });
    it('t', () => {});
  `);
  assertFlagged(result, "weakAwaits");
});

// A named helper invoked explicitly runs its body where the call sits, so
// its captured assignments initialize the shared binding like an IIFE's.
test("propagates captured writes from an explicitly invoked helper", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let sleep;
    function init() {
      sleep = () => new Promise((r) => setTimeout(r, 10));
    }
    init();
    it('t', async () => {
      await sleep();
    });
  `);
  assertFlagged(result, "weakAwaits");
});

// An alias reassigned after the await in the same callback must not
// re-classify that await: it resolves through the declaration standing at
// the await, while enclosing scopes stay live for forward declarations.
test("a same-callback reassignment after the await keeps its verdict", async () => {
  const result = await runOxlint(["weakAwaits"], `
    function clean() {
      return fetch('/data');
    }
    function timer() {
      return sleep();
    }
    function sleep(ms) {
      return new Promise((resolve) => setTimeout(resolve, ms));
    }
    it('t', async () => {
      let wait = clean;
      await wait();
      wait = timer;
    });
  `);
  assertClean(result, "weakAwaits");
});

// A write to the helper's own local variable stays inside the helper: the
// local merely shadows the captured binding, so replaying the invocation
// must not clobber the caller's clean helper.
test("an invoked helper's local shadow does not clobber the captured binding", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let sleep = () => fetch('/data');
    function init() {
      let sleep;
      sleep = () => new Promise((resolve) => setTimeout(resolve, 10));
    }
    init();
    it('t', async () => {
      await sleep();
    });
  `);
  assertClean(result, "weakAwaits");
});

// A reassignment in the same hook that executes after the await must not
// reach its verdict: only the writes before the await have happened.
test("a same-hook reassignment after the await does not reach it", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let sleep = () => new Promise((resolve) => setTimeout(resolve, 10));
    beforeEach(async () => {
      await sleep();
      sleep = () => fetch('/data');
    });
    it('t', () => {});
  `);
  assertFlagged(result, "weakAwaits");
});

// A variable-declared callable is invocable by name exactly like a function
// declaration: invoking it replays its captured writes.
test("an invoked arrow initializer propagates its captured writes", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let sleep;
    const init = () => {
      sleep = () => new Promise((resolve) => setTimeout(resolve, 10));
    };
    init();
    it('t', async () => {
      await sleep();
    });
  `);
  assertFlagged(result, "weakAwaits");
});

// The helper's writes resolve against its DEFINING scope: a caller's
// shadowing local must not capture them.
test("a caller's shadowing local does not capture an invoked helper's write", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let sleep;
    function init() {
      sleep = () => new Promise((resolve) => setTimeout(resolve, 10));
    }
    it('t', async () => {
      let sleep = () => fetch('/data');
      init();
      await sleep();
    });
  `);
  assertClean(result, "weakAwaits");
});

// A nested function's parameters are its own locals, not the enclosing
// helper's: they must not suppress the helper's captured write.
test("a nested function's parameter is not the helper's local", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let sleep = () => fetch('/data');
    function init() {
      const other = (sleep) => {};
      sleep = () => new Promise((resolve) => setTimeout(resolve, 10));
    }
    init();
    it('t', async () => {
      await sleep();
    });
  `);
  assertFlagged(result, "weakAwaits");
});

// A local helper assigned to a captured binding carries its own kind: the
// replay must classify the initializer's locals, not treat the reference
// as clean because it does not exist in the defining scopes.
test("an invoked helper's local helper propagates its kind", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let sleep = () => fetch('/data');
    function init() {
      const timer = () => new Promise((resolve) => setTimeout(resolve, 10));
      sleep = timer;
    }
    init();
    it('t', async () => {
      await sleep();
    });
  `);
  assertFlagged(result, "weakAwaits");
});

// A local reassigned before it is copied to a captured binding carries the
// NEW initializer's kind: the replay updates local values on assignment.
test("a reassigned local propagates its latest value's kind", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let sleep = () => fetch('/');
    function init() {
      let local = () => fetch('/');
      local = () => new Promise((resolve) => setTimeout(resolve, 10));
      sleep = local;
    }
    init();
    it('t', async () => {
      await sleep();
    });
  `);
  assertFlagged(result, "weakAwaits");
});

// A copy taken from a local holds the value the source had at the copy's
// execution position, not the source's later value.
test("a local copy keeps the value it was taken from", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let sleep = () => fetch('/');
    function init() {
      let x = () => fetch('/');
      let y = x;
      x = () => new Promise((resolve) => setTimeout(resolve, 10));
      sleep = y;
    }
    init();
    it('t', async () => {
      await sleep();
    });
  `);
  assertClean(result, "weakAwaits");
});

// A copy taken AFTER an intervening assignment sees the assigned value:
// initializers classify when the replay reaches each declaration.
test("a local copy after a reassignment carries the new value", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let sleep = () => fetch('/');
    function init() {
      let x = () => fetch('/');
      x = () => new Promise((resolve) => setTimeout(resolve, 10));
      let y = x;
      sleep = y;
    }
    init();
    it('t', async () => {
      await sleep();
    });
  `);
  assertFlagged(result, "weakAwaits");
});

// A callee declared inside the replaying helper resolves through the
// helper's own declarations: its captured writes propagate.
test("a helper's nested function declaration propagates its writes", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let sleep;
    function init() {
      function set() {
        sleep = () => new Promise((resolve) => setTimeout(resolve, 10));
      }
      set();
    }
    init();
    it('t', async () => {
      await sleep();
    });
  `);
  assertFlagged(result, "weakAwaits");
});

// A variable-declared local callable is invocable by name like a function
// declaration: invoking it replays its captured writes.
test("a helper's local arrow callable propagates its writes", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let sleep;
    function init() {
      const set = () => {
        sleep = () => new Promise((resolve) => setTimeout(resolve, 10));
      };
      set();
    }
    init();
    it('t', async () => {
      await sleep();
    });
  `);
  assertFlagged(result, "weakAwaits");
});

// A local alias of a local callable is invocable through its own name: the
// invocation replays the same captured writes.
test("a helper's local callable alias propagates its writes", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let sleep;
    function init() {
      const set = () => {
        sleep = () => new Promise((resolve) => setTimeout(resolve, 10));
      };
      const invoke = set;
      invoke();
    }
    init();
    it('t', async () => {
      await sleep();
    });
  `);
  assertFlagged(result, "weakAwaits");
});

// An assignment copies the source's CURRENT value: a later reassignment of
// the source must not change the copy.
test("an assigned copy keeps the value at its execution position", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let sleep = () => fetch('/');
    function init() {
      let x = () => fetch('/');
      let y;
      y = x;
      x = () => new Promise((resolve) => setTimeout(resolve, 10));
      sleep = y;
    }
    init();
    it('t', async () => {
      await sleep();
    });
  `);
  assertClean(result, "weakAwaits");
});

// A replay-local alias REPLACED by a callable no longer resolves to its
// previous callable: the invocation replays the replacement's writes.
test("a replaced local alias resolves to its replacement", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let sleep;
    function init() {
      const noop = () => {};
      let invoke = noop;
      invoke = () => {
        sleep = () => new Promise((resolve) => setTimeout(resolve, 10));
      };
      invoke();
    }
    init();
    it('t', async () => {
      await sleep();
    });
  `);
  assertFlagged(result, "weakAwaits");
});

// A nested helper invoked during the replay reads the invoking
// initializer's locals: its captured write carries the local's kind.
test("a nested helper reads the enclosing initializer's locals", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let sleep;
    function init() {
      const local = () => new Promise((resolve) => setTimeout(resolve, 10));
      function set() {
        sleep = local;
      }
      set();
    }
    init();
    it('t', async () => {
      await sleep();
    });
  `);
  assertFlagged(result, "weakAwaits");
});

// A nested helper resolves its captures through its DEFINITION-time
// environment: a caller scope that shadows a captured local must not
// change what the helper sees.
test("a nested helper ignores a caller's shadowing scopes", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let sleep = () => fetch('/');
    function init() {
      const local = () => new Promise((resolve) => setTimeout(resolve, 10));
      function set() {
        sleep = local;
      }
      {
        const local = () => fetch('/');
        set();
      }
    }
    init();
    it('t', async () => {
      await sleep();
    });
  `);
  assertFlagged(result, "weakAwaits");
});

// A `var` binding in a replayed block outlives the block, as in JavaScript.
test("a replayed var binding outlives its block", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let sleep = () => fetch('/');
    function init() {
      {
        var local = () => new Promise((resolve) => setTimeout(resolve, 10));
      }
      sleep = local;
    }
    init();
    it('t', async () => {
      await sleep();
    });
  `);
  assertFlagged(result, "weakAwaits");
});

// A block-local declaration expires with its block: the same-named
// assignment after the block updates the captured binding.
test("a block-local shadow does not exclude assignments outside its block", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let sleep = () => fetch('/data');
    function init() {
      {
        let sleep;
      }
      sleep = () => new Promise((resolve) => setTimeout(resolve, 10));
    }
    init();
    it('t', async () => {
      await sleep();
    });
  `);
  assertFlagged(result, "weakAwaits");
});

// A reset invoked after an await in the same hook has not happened when
// that await runs: the write logs at the invocation's position.
test("a helper invoked after an await in the same hook does not reach it", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let sleep = () => new Promise((resolve) => setTimeout(resolve, 10));
    beforeEach(async () => {
      function reset() {
        sleep = () => fetch('/data');
      }
      await sleep();
      reset();
    });
    it('t', () => {});
  `);
  assertFlagged(result, "weakAwaits");
});

// A reassignment replaces the callable a binding holds: a later invocation
// replays the NEW value's body, so the obsolete write is not replayed.
test("a reassigned callable no longer replays its old body", async () => {
  const result = await runOxlint(["weakAwaits"], `
    let wait;
    function init() {
      wait = () => new Promise((resolve) => setTimeout(resolve, 10));
    }
    init = () => {};
    init();
    it('t', async () => {
      await wait();
    });
  `);
  assertClean(result, "weakAwaits");
});

// A long acyclic forwarding chain must classify in linear time: each
// helper forwards twice, so an unbounded replay would expand
// exponentially and hang the lint run (28 levels take minutes unfixed,
// well under a second with the write-summary cache).
test("a forwarding-helper chain classifies without exponential expansion", async () => {
  let chain = "function f0() {}\n";
  for (let index = 1; index <= 28; index += 1) {
    chain += `function f${index}() { f${index - 1}(); f${index - 1}(); }\n`;
  }
  chain += `it('t', async () => { await f28(); });\n`;
  const result = await runOxlint(["weakAwaits"], chain);
  assertClean(result, "weakAwaits");
});

// A mutation at the end of the chain must still propagate: the summary
// cache only skips helpers proven not to write captured state.
test("a writing helper at the end of a forwarding chain still propagates", async () => {
  let chain = "let sleep;\n";
  chain += "function f0() { sleep = () => new Promise((resolve) => setTimeout(resolve, 10)); }\n";
  for (let index = 1; index <= 12; index += 1) {
    chain += `function f${index}() { f${index - 1}(); f${index - 1}(); }\n`;
  }
  chain += `f12();\nit('t', async () => { await sleep(); });\n`;
  const result = await runOxlint(["weakAwaits"], chain);
  assertFlagged(result, "weakAwaits");
});
