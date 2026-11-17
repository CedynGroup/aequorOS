/**
 * Oxlint JS plugin rule: acts/require-frozen-time (ACTS001)
 *
 * Warns when test files use time-dependent APIs (`new Date()`, `Date.now()`)
 * without freezing time via `vi.useFakeTimers()`/`vi.setSystemTime()` (or the
 * jest equivalents), via node:test's `mock.timers.enable(...)` (which freezes
 * `Date` when its mocked apis include it), or via
 * `page.clock.install()`/`page.clock.setFixedTime()` in Playwright tests.
 *
 * The check is scoped per test/hook callback: a freeze call in one test does
 * not silence warnings in a sibling test. `beforeEach`/`beforeAll` freezes
 * cover all tests within the same `describe` block, and a top-level
 * `beforeEach`/`beforeAll` covers the whole file.
 *
 * Enable via .oxlintrc.json overrides scoped to test file globs.
 */

/** Names of test-runner globals that define test scopes or lifecycle hooks. */
const HOOK_NAMES = new Set(["beforeEach", "beforeAll", "afterEach", "afterAll"]);
const TEST_NAMES = new Set(["it", "test"]);
const DESCRIBE_NAMES = new Set(["describe"]);
const SCOPE_NAMES = new Set([...HOOK_NAMES, ...TEST_NAMES, ...DESCRIBE_NAMES]);

/** Names of known test-runner globals whose timer methods we recognise. */
const RUNNER_OBJECTS = new Set(["vi", "jest"]);

/** Hooks that run BEFORE tests, so their freeze covers the tests that follow. */
const BEFORE_HOOKS = new Set(["beforeEach", "beforeAll"]);

/** Clock methods on a Playwright `page.clock` (or context/browser clock) that fix time. */
const CLOCK_METHODS = new Set(["install", "setFixedTime"]);

/**
 * node:test's timer mock: `<t.mock.|mock.>timers.enable(...)` — node:test's
 * equivalent of `vi.useFakeTimers()`, used by node:test suites (the console's
 * tests) that have no `vi`/`jest` to freeze `Date` with.
 */
function isNodeTestTimersEnableCall(callee) {
  return (
    callee.type === "MemberExpression" &&
    callee.property.type === "Identifier" &&
    callee.property.name === "enable" &&
    callee.object.type === "MemberExpression" &&
    !callee.object.computed &&
    callee.object.property.type === "Identifier" &&
    callee.object.property.name === "timers"
  );
}

/**
 * Whether a node:test `timers.enable(...)` call also freezes `Date`: either
 * with no options (the default mocked apis include "Date") or with an `apis`
 * value that is an array of literals naming "Date". An identifier reference
 * is never resolved: resolving it without real scope analysis risks reading
 * a shadowed or reassigned binding, so an unresolvable `apis` value leaves
 * the wall clock live and must not silence the rule.
 */
function nodeTestTimersEnableFreezesDate(node) {
  const options = node.arguments[0];
  // No options at all: node:test's default mocked apis include "Date".
  if (!options) return true;
  // A non-object first argument (identifier, spread call, ...) is not proof.
  if (options.type !== "ObjectExpression") return false;
  const namesApis = (property) =>
    property.type === "Property" &&
    !property.computed &&
    ((property.key.type === "Identifier" && property.key.name === "apis") ||
      (property.key.type === "Literal" && property.key.value === "apis"));
  // Object precedence: the LAST `apis` property is the effective one.
  let apisIndex = -1;
  options.properties.forEach((property, index) => {
    if (namesApis(property)) apisIndex = index;
  });
  const canOverride = (property) =>
    property.type === "SpreadElement" ||
    (property.type === "Property" && property.computed);
  if (apisIndex === -1) {
    // No explicit `apis`: the default mocked apis include "Date", unless a
    // spread or computed key could override the defaults.
    return !options.properties.some(canOverride);
  }
  // A spread or computed key after the effective `apis` property could
  // override it, so the final value is not provable.
  if (options.properties.slice(apisIndex + 1).some(canOverride)) return false;
  const apis = options.properties[apisIndex];
  if (apis.value.type !== "ArrayExpression") return false;
  // Spread or hole elements are not proof that "Date" is mocked.
  return apis.value.elements.some(
    (element) => element?.type === "Literal" && element.value === "Date",
  );
}

/**
 * Extract the test-framework scope name from a callee, unwrapping variants
 * like `describe.only(...)`, `it.each(data)(...)`, `it.each`...`(...)`, the
 * Playwright member forms `test.describe(...)` / `test.beforeEach(...)`, and
 * chained suite modifiers like `test.describe.serial(...)`.
 * For a member callee the property name wins when it is itself a scope name,
 * so `test.beforeEach` is classified as a hook rather than a test; otherwise
 * the object is resolved recursively, so `test.describe.serial` still yields
 * the `describe` scope its callbacks belong to.
 * Returns the scope name (e.g. "describe", "beforeEach") or `null`.
 */
function getCalleeBaseName(callee) {
  if (callee.type === "Identifier") return callee.name;
  if (callee.type === "MemberExpression") {
    if (callee.property.type === "Identifier" && SCOPE_NAMES.has(callee.property.name)) {
      return callee.property.name;
    }
    return getCalleeBaseName(callee.object);
  }
  if (callee.type === "CallExpression") return getCalleeBaseName(callee.callee);
  if (callee.type === "TaggedTemplateExpression") return getCalleeBaseName(callee.tag);
  return null;
}

/**
 * The nearest function node around `node`, or `null` at module level. Used to
 * tell transitions a hook/test callback executes itself from transitions
 * captured inside a nested closure it merely defines.
 */
function nearestFunction(node) {
  let current = node.parent;
  while (current) {
    if (
      current.type === "FunctionExpression" ||
      current.type === "ArrowFunctionExpression" ||
      current.type === "FunctionDeclaration"
    ) {
      return current;
    }
    current = current.parent;
  }
  return null;
}

/**
 * When `node` executes, as a lexicographic ordering key (an array of source
 * positions compared element-wise). Each immediately-invoked function the
 * code sits in opens a sub-frame: an invocation's code is ordered after the
 * statements before the call and its own arguments (frame marker 0) and
 * before the statements after the call (the body is frame marker 1 at the
 * call's start), so nested invocations interleave with the surrounding
 * statements that invoke them instead of all collapsing onto the outermost
 * invocation's end, while a body still always runs after its arguments.
 */
function executionKey(node) {
  // Innermost-to-outermost chain of invocations containing the node.
  const chain = [];
  let current = node;
  while (current) {
    const parent = current.parent;
    if (
      parent &&
      (parent.type === "CallExpression" || parent.type === "NewExpression") &&
      (parent.callee.type === "ArrowFunctionExpression" ||
        parent.callee.type === "FunctionExpression")
    ) {
      chain.push(parent);
    }
    current = parent;
  }
  if (chain.length === 0) {
    return [node.range[0]];
  }
  // The invocation frame a node sits in: frame marker 0 for an argument
  // (evaluated before the body), 1 for the body. `node.range[0]` inside an
  // invocation's body or arguments orders code within that frame.
  const inBody = (outer, inner) => {
    let probe = inner;
    while (probe && probe !== outer) {
      if (probe === outer.callee.body) {
        return true;
      }
      probe = probe.parent;
    }
    return false;
  };
  const key = [];
  for (let index = chain.length - 1; index >= 0; index -= 1) {
    const invocation = chain[index];
    // What this frame contains: the next inner invocation, or the node
    // itself at the innermost frame. Body membership decides the frame
    // marker (an argument evaluates before the body).
    const contained = index > 0 ? chain[index - 1] : node;
    key.push(invocation.range[0]);
    key.push(inBody(invocation, contained) ? 1 : 0);
  }
  key.push(node.range[0]);
  return key;
}

/** Ordering key for timer-state transitions. */
const transitionOrder = (transition) => transition.order ?? [transition.node.range[0]];

/** Lexicographic comparison of two transitions' execution keys. */
const compareExecution = (a, b) => {
  const keyA = transitionOrder(a);
  const keyB = transitionOrder(b);
  const length = Math.max(keyA.length, keyB.length);
  for (let index = 0; index < length; index += 1) {
    const valueA = keyA[index] ?? -1;
    const valueB = keyB[index] ?? -1;
    if (valueA !== valueB) {
      return valueA - valueB;
    }
  }
  return a.node.range[0] - b.node.range[0];
};

/**
 * Walk up from `node` to find the nearest enclosing callback that is a direct
 * argument of a test-framework call (it/test/describe/beforeEach/…).
 * Handles variants like describe.only, it.each(data)("name", fn), etc.
 * Returns `{ fn, callType, callNode }` or `null`.
 */
function findEnclosingTestCallback(node) {
  let current = node.parent;
  while (current) {
    if (
      (current.type === "FunctionExpression" || current.type === "ArrowFunctionExpression") &&
      current.parent?.type === "CallExpression"
    ) {
      const baseName = getCalleeBaseName(current.parent.callee);
      if (baseName && SCOPE_NAMES.has(baseName)) {
        return { fn: current, callType: baseName, callNode: current.parent };
      }
    }
    current = current.parent;
  }
  return null;
}

/**
 * Walk up from `node` to find the nearest enclosing `describe` callback.
 * Handles variants like describe.only, test.describe, describe.each, etc.
 * Returns the describe's callback function node, or `null`.
 */
function findEnclosingDescribe(node) {
  let current = node.parent;
  while (current) {
    if (
      (current.type === "FunctionExpression" || current.type === "ArrowFunctionExpression") &&
      current.parent?.type === "CallExpression"
    ) {
      const baseName = getCalleeBaseName(current.parent.callee);
      if (baseName && DESCRIBE_NAMES.has(baseName)) {
        return current;
      }
    }
    current = current.parent;
  }
  return null;
}

/**
 * Collect ALL enclosing `describe` callback nodes from `node` up to the root.
 * Handles variants like describe.only, test.describe, describe.each, etc.
 * Returns an array ordered from innermost to outermost.
 */
function findAllEnclosingDescribes(node) {
  const describes = [];
  let current = node.parent;
  while (current) {
    if (
      (current.type === "FunctionExpression" || current.type === "ArrowFunctionExpression") &&
      current.parent?.type === "CallExpression"
    ) {
      const baseName = getCalleeBaseName(current.parent.callee);
      if (baseName && DESCRIBE_NAMES.has(baseName)) {
        describes.push(current);
      }
    }
    current = current.parent;
  }
  return describes;
}

/**
 * Whether `testDescribes` (innermost first) contains `suiteDescribes`
 * (innermost first) as a contiguous, ordered slice — the teardown registered
 * in those describes runs after every test of that suite and of the suites
 * nested inside it. An empty `suiteDescribes` is a file-level teardown: it
 * runs after every test in the file.
 */
function coversDescribes(testDescribes, suiteDescribes) {
  if (suiteDescribes.length === 0) {
    return true;
  }
  for (let start = 0; start + suiteDescribes.length <= testDescribes.length; start += 1) {
    let matches = true;
    for (let index = 0; index < suiteDescribes.length; index += 1) {
      if (testDescribes[start + index] !== suiteDescribes[index]) {
        matches = false;
        break;
      }
    }
    if (matches) {
      return true;
    }
  }
  return false;
}

/**
 * True when the callee is `<anything>.clock.install(...)` or
 * `<anything>.clock.setFixedTime(...)` — Playwright's clock API
 * (page.clock, context.clock, browser.clock all share the shape).
 */
function isClockFreezeCall(callee) {
  return (
    callee.type === "MemberExpression" &&
    callee.property.type === "Identifier" &&
    CLOCK_METHODS.has(callee.property.name) &&
    callee.object.type === "MemberExpression" &&
    !callee.object.computed &&
    callee.object.property.type === "Identifier" &&
    callee.object.property.name === "clock"
  );
}

/**
 * Whether the transition at `node` executes when `top` — the enclosing test
 * or hook callback, or null for module evaluation — runs. Every function
 * boundary between the transition and `top` must be immediately invoked
 * where it sits (an IIFE); a closure the callback merely defines executes
 * only if something later calls it, so its transitions say nothing about
 * the callback's own timer state.
 */
function executesWhenEnclosingRuns(node, top) {
  let current = nearestFunction(node);
  while (current && current !== top) {
    const parent = current.parent;
    if (!(parent?.type === "CallExpression" && parent.callee === current)) {
      return false;
    }
    current = nearestFunction(parent);
  }
  return true;
}

const rule = {
  meta: {
    type: "problem",
    docs: {
      description:
        "Require test files that use time-dependent APIs to freeze time with useFakeTimers/setSystemTime or page.clock",
    },
    messages: {
      missingFreezeTime:
        "ACTS001: {{ api }} reads the real wall clock. Freeze time with vi.useFakeTimers() + vi.setSystemTime() in a beforeEach/beforeAll or in the test itself, with mock.timers.enable({ apis: ['Date'] }) in node:test suites, or with page.clock.install()/page.clock.setFixedTime() in Playwright tests.",
    },
    schema: [],
  },
  create(context) {
    // Freeze/unfreeze calls recorded with their enclosing test/hook callback
    // info. A freeze is `vi.useFakeTimers()`/`vi.setSystemTime()` (or the jest
    // equivalents), `page.clock.install()`/`page.clock.setFixedTime()`, or
    // node:test's Date-freezing `mock.timers.enable(...)`; an unfreeze is
    // `vi.useRealTimers()`/`jest.useRealTimers()`, which restores the wall
    // clock and invalidates an earlier freeze in the same callback.
    const freezeCalls = [];
    const unfreezeCalls = [];
    // Time-dependent API usages to validate at the end.
    const timeDependentNodes = [];
    // Every registered test callback, with its describe chain.
    const allTestCallbacks = [];
    const recordFreezeState = (node, calls) => {
      const enclosing = findEnclosingTestCallback(node);
      // A transition directly in the callback's body (or inside a closure
      // the body immediately invokes) executes when the callback runs; one
      // captured inside a closure nobody invokes here runs only if that
      // closure is called later, so it does not describe the callback's own
      // timer state — nor, for uncalled module-level helpers, the module's.
      const immediate = executesWhenEnclosingRuns(node, enclosing ? enclosing.fn : null);
      calls.push({ node, enclosing, immediate });
    };
    return {
      CallExpression(node) {
        const callee = node.callee;

        // Every test callback, timer-touching or not: a teardown's state is
        // shaped by each covered test's own transitions, and a test without
        // any still runs between the setup and teardown hooks.
        const testBaseName = getCalleeBaseName(callee);
        if (testBaseName !== null && TEST_NAMES.has(testBaseName)) {
          const callback = node.arguments.find(
            (argument) =>
              argument.type === "FunctionExpression" ||
              argument.type === "ArrowFunctionExpression",
          );
          if (callback) {
            allTestCallbacks.push({
              fn: callback,
              describes: findAllEnclosingDescribes(node),
            });
          }
        }

        // Detect vi.useFakeTimers(), jest.useFakeTimers(), vi.setSystemTime(), jest.setSystemTime()
        if (
          callee.type === "MemberExpression" &&
          callee.object.type === "Identifier" &&
          RUNNER_OBJECTS.has(callee.object.name) &&
          callee.property.type === "Identifier" &&
          (callee.property.name === "useFakeTimers" || callee.property.name === "setSystemTime")
        ) {
          recordFreezeState(node, freezeCalls);
          return;
        }

        // Detect vi.useRealTimers() / jest.useRealTimers(): the wall clock is
        // back, so any earlier freeze no longer covers later reads.
        if (
          callee.type === "MemberExpression" &&
          callee.object.type === "Identifier" &&
          RUNNER_OBJECTS.has(callee.object.name) &&
          callee.property.type === "Identifier" &&
          callee.property.name === "useRealTimers"
        ) {
          recordFreezeState(node, unfreezeCalls);
          return;
        }

        // Detect page.clock.install(), context.clock.setFixedTime(), ... —
        // Playwright's clock API freezes both timers and Date.
        if (isClockFreezeCall(callee)) {
          recordFreezeState(node, freezeCalls);
          return;
        }

        // Detect node:test's timer mock freezing Date.
        if (isNodeTestTimersEnableCall(callee)) {
          if (nodeTestTimersEnableFreezesDate(node)) {
            recordFreezeState(node, freezeCalls);
          }
          return;
        }

        // Match: Date.now()
        if (
          callee.type === "MemberExpression" &&
          callee.object.type === "Identifier" &&
          callee.object.name === "Date" &&
          callee.property.type === "Identifier" &&
          callee.property.name === "now"
        ) {
          timeDependentNodes.push({ node, api: "Date.now()" });
        }
      },

      // Match: new Date() with no arguments
      NewExpression(node) {
        if (
          node.callee.type === "Identifier" &&
          node.callee.name === "Date" &&
          node.arguments.length === 0
        ) {
          timeDependentNodes.push({ node, api: "new Date()" });
        }
      },

      // Validate each time-dependent node against its scope
      "Program:exit"() {
        if (timeDependentNodes.length === 0) return;

        // Timer-state transitions grouped by where they execute, each list
        // ordered by source position:
        //  - per enclosing test/hook callback (its own body);
        //  - module level (no enclosing callback) for collection-time reads.
        const transitionsByCallback = new Map();
        const moduleTransitions = [];
        // Transitions inside describe callbacks: they all execute during
        // module evaluation, wherever the describe sits, so they shape the
        // timer state of everything collected after them.
        const describeTransitions = [];
        const recordTransition = (call, frozen) => {
          const entry = {
            node: call.node,
            frozen,
            immediate: call.immediate,
            // The function the transition sits in: a deferred helper's own
            // freeze still covers the reads inside that same helper, even
            // though the helper's transitions say nothing about its caller.
            fn: nearestFunction(call.node),
            // Execution point: an IIFE body's transition runs when the
            // invocation's body runs, after its arguments.
            order: executionKey(call.node),
          };
          // A transition trapped inside a closure that nothing here invokes
          // (an uncalled helper, a hook's unused restoration) executes only
          // if that closure is called later; it contributes no state to the
          // enclosing callback or to module evaluation.
          if (!call.immediate) {
            if (call.enclosing && !DESCRIBE_NAMES.has(call.enclosing.callType)) {
              const list = transitionsByCallback.get(call.enclosing.fn) ?? [];
              list.push(entry);
              transitionsByCallback.set(call.enclosing.fn, list);
            }
            return;
          }
          if (!call.enclosing) {
            moduleTransitions.push(entry);
            return;
          }
          if (DESCRIBE_NAMES.has(call.enclosing.callType)) {
            describeTransitions.push(entry);
          }
          const list = transitionsByCallback.get(call.enclosing.fn) ?? [];
          list.push(entry);
          transitionsByCallback.set(call.enclosing.fn, list);
        };
        for (const call of freezeCalls) recordTransition(call, true);
        for (const call of unfreezeCalls) recordTransition(call, false);
        // Equal execution positions (transitions inside one IIFE body)
        // keep source order.
        const byExecution = compareExecution;
        for (const list of transitionsByCallback.values()) {
          list.sort(byExecution);
        }
        moduleTransitions.sort(byExecution);
        describeTransitions.sort(byExecution);

        /**
         * Lifecycle phases: beforeAll < beforeEach < test < afterEach <
         * afterAll. A freeze in an earlier phase covers callbacks in the same
         * or a LATER phase; describe bodies (collection) and module scope run
         * before every hook, so no hook ever covers them.
         */
        const phaseRank = (callType) => {
          if (callType === "beforeAll") return 0;
          if (callType === "beforeEach") return 1;
          if (TEST_NAMES.has(callType)) return 2;
          if (callType === "afterEach") return 3;
          if (callType === "afterAll") return 4;
          return undefined;
        };

        // Test callbacks that touch the timer state, each with its chain of
        // enclosing describes: a teardown callback runs after every test of
        // its suite — including tests registered in suites nested inside it —
        // so each test's own transitions shape the state the teardown sees.
        const collectedTests = [];
        // Every before-hook (beforeAll/beforeEach) whose callback touches the
        // timer state, keyed by the describe it is registered in (`null` =
        // file level). `netFrozen` is the state the hook LEAVES behind: a
        // hook that freezes and then restores provides no coverage, and a
        // restoring hook actively undoes an earlier hook's freeze.
        const hooksByDescribe = new Map();
        const fileLevelHooks = [];
        for (const { enclosing } of [...freezeCalls, ...unfreezeCalls]) {
          if (
            !enclosing ||
            !(
              BEFORE_HOOKS.has(enclosing.callType) ||
              enclosing.callType === "afterEach" ||
              enclosing.callType === "afterAll"
            )
          ) {
            continue;
          }
          const transitions = (transitionsByCallback.get(enclosing.fn) ?? []).filter(
            (t) => t.immediate,
          );
          const hook = {
            hookType: enclosing.callType,
            callNode: enclosing.callNode,
            netFrozen: transitions.length > 0 ? transitions.at(-1).frozen : null,
          };
          const parentDescribe = findEnclosingDescribe(enclosing.callNode);
          if (parentDescribe) {
            const hooks = hooksByDescribe.get(parentDescribe) ?? [];
            hooks.push(hook);
            hooksByDescribe.set(parentDescribe, hooks);
          } else {
            fileLevelHooks.push(hook);
          }
        }
        collectedTests.push(...allTestCallbacks);
        for (const hooks of hooksByDescribe.values()) {
          hooks.sort((a, b) => a.callNode.range[0] - b.callNode.range[0]);
        }
        fileLevelHooks.sort((a, b) => a.callNode.range[0] - b.callNode.range[0]);

        /**
         * Whether a transition executes before a read `node`. Source order is
         * the base, but a transition inside an immediately-invoked function
         * runs when the invocation's BODY runs — after its arguments. A read
         * inside an argument of that same invocation therefore happens before
         * the transition, not after it.
         */
        const executesBefore = (transitionNode, readNode) => {
          if (transitionNode.range[0] >= readNode.range[0]) {
            return false;
          }
          let current = readNode.parent;
          let previous = readNode;
          while (current) {
            if (
              (current.type === "CallExpression" &&
                (current.callee.type === "ArrowFunctionExpression" ||
                  current.callee.type === "FunctionExpression")) ||
              (current.type === "NewExpression" &&
                (current.callee.type === "ArrowFunctionExpression" ||
                  current.callee.type === "FunctionExpression"))
            ) {
              const body = current.callee.body;
              // A block-bodied IIFE nests the transition below the body, so
              // walk up from the transition itself.
              let inner = transitionNode;
              while (inner) {
                if (inner === body) {
                  if (previous !== current.callee) {
                    // The read reached this invocation through an argument:
                    // the body — and the transition — runs after it.
                    return false;
                  }
                  // The read is inside the invocation body itself: source
                  // order within the body decides, so keep scanning upward
                  // for an outer invocation that might wrap the read in an
                  // argument.
                  break;
                }
                inner = inner.parent;
              }
            }
            previous = current;
            current = current.parent;
          }
          return true;
        };

        for (const { node, api } of timeDependentNodes) {
          const enclosing = findEnclosingTestCallback(node);
          const report = () =>
            context.report({ node, messageId: "missingFreezeTime", data: { api } });

          /**
           * The timer state at a read = the last transition that executed
           * before it. Hooks contribute through the hook sequence below (a
           * hook runs entirely before the callbacks it precedes, so its net
           * end state is what the next callback inherits); the read's own
           * callback contributes its transitions up to the read.
           */
          const readFunction = nearestFunction(node);
          const stateAt = (transitions) => {
            const own = transitions.filter(
              (t) =>
                // A transition the callback executes directly, or one inside
                // the same deferred function the read sits in (its own
                // freeze covers its own reads), and one that executed
                // before the read.
                (t.immediate || t.fn === readFunction) &&
                executesBefore(t.node, node),
            );
            return own.length > 0 ? own.at(-1).frozen : null;
          };

          if (!enclosing || enclosing.callType === "describe") {
            // Collection callbacks (describe bodies) and module-level reads
            // execute during module evaluation: no hook covers them, but an
            // explicit module-level freeze that already executed does — and
            // every enclosing describe body's transitions ran before this
            // read too (outer describes collect before inner ones).
            const candidates = [...moduleTransitions, ...describeTransitions];
            const before = candidates
              .filter((t) => executesBefore(t.node, node))
              .sort(compareExecution);
            if (before.length === 0 || !before.at(-1).frozen) {
              report();
            }
            continue;
          }

          // Hooks and tests: replay the hook sequence that runs before this
          // callback. Execution order is phase (beforeAll before beforeEach),
          // then, within a phase, outer describes before inner ones for setup
          // phases and inner describes before outer ones for teardown phases
          // (an inner afterEach runs before the file-level one), then
          // registration order within a describe. An equal-phase hook of the
          // SAME describe runs before this callback only when it is
          // registered earlier.
          const rank = phaseRank(enclosing.callType);
          const enclosingDescribes = findAllEnclosingDescribes(enclosing.callNode);
          const sequence = [];
          const isTeardown = (hookType) => hookType === "afterEach" || hookType === "afterAll";
          const consider = (hook, depth) => {
            const hookRank = phaseRank(hook.hookType);
            if (hookRank < rank) {
              sequence.push({ hook, depth });
            } else if (
              hookRank === rank &&
              // Teardown hooks run inner describe first, so a same-phase
              // hook from an OUTER describe executes after this callback
              // and contributes nothing; only an earlier-registered hook of
              // the same describe runs before it. Setup hooks run outer
              // describe first, so any outer hook of the same phase counts.
              (isTeardown(hook.hookType)
                ? depth === 0 && hook.callNode.range[0] < enclosing.callNode.range[0]
                : depth !== 0 || hook.callNode.range[0] < enclosing.callNode.range[0])
            ) {
              sequence.push({ hook, depth });
            }
          };
          // File level sits outside every describe, so it is the outermost.
          for (const hook of fileLevelHooks) consider(hook, enclosingDescribes.length);
          for (let depth = enclosingDescribes.length - 1; depth >= 0; depth -= 1) {
            for (const hook of hooksByDescribe.get(enclosingDescribes[depth]) ?? []) {
              consider(hook, depth);
            }
          }
          sequence.sort(
            (a, b) =>
              phaseRank(a.hook.hookType) - phaseRank(b.hook.hookType) ||
              (isTeardown(a.hook.hookType) ? a.depth - b.depth : b.depth - a.depth) ||
              a.hook.callNode.range[0] - b.hook.callNode.range[0],
          );

          // Module-level transitions and enclosing describe bodies all run
          // during module evaluation, in source order: a describe freeze
          // before a later module-level restoration does not cover the
          // tests, and a module freeze before a describe-body restoration
          // does. Merge them in source order and take the last state.
          const collectionTransitions = [...moduleTransitions, ...describeTransitions];
          collectionTransitions.sort(compareExecution);
          let frozen =
            collectionTransitions.length > 0
              ? collectionTransitions.at(-1).frozen
              : false;
          // A teardown callback runs after every test of its suite — its
          // own describe and every suite nested inside it. Per test run the
          // state it observes is: the setup hooks (file level, then each
          // enclosing describe outermost first), that test's own
          // transitions, then the teardown hooks of the suites nested inside
          // the read's suite (innermost first) and the read's suite's own
          // earlier-registered teardowns. The read stays covered only while
          // that state is frozen for EVERY covered test. A teardown's
          // describe chain must appear in the test's chain, contiguous and
          // in order; a file-level teardown covers every test in the file.
          if (isTeardown(enclosing.callType)) {
            const suiteTests = collectedTests.filter((test) =>
              coversDescribes(test.describes, enclosingDescribes),
            );
            if (suiteTests.length === 0) {
              // No test of this suite touches the timer state: the hooks
              // alone decide the state the read observes.
              for (const { hook } of sequence) {
                if (hook.netFrozen !== null) frozen = hook.netFrozen;
              }
            } else {
              // The read stays covered only while the replayed state is
              // frozen for EVERY covered test; any test ending unfrozen
              // uncovers it. An afterAll callback is the exception: it runs
              // ONCE after the entire suite, so the state it observes is the
              // one the suite's LAST test leaves behind (its own transitions
              // and the teardown hooks that run after it) — not the state
              // after every individual test.
              const collectionState = frozen;
              const replayedTests =
                enclosing.callType === "afterAll" ? suiteTests.slice(-1) : suiteTests;
              frozen = true;
              for (const test of replayedTests) {
                // Each replay starts from the timer state module evaluation
                // leaves behind (the collection-time state computed above):
                // when no freeze has executed anywhere the state is
                // unfrozen, not frozen.
                let state = collectionState;
                // Setup hooks replay in lifecycle order — every beforeAll
                // before every beforeEach, outer describes before inner ones
                // within a phase, registration order within a describe —
                // not in source registration order across phases.
                const setupHooks = [];
                for (const hook of fileLevelHooks) {
                  if (!isTeardown(hook.hookType)) {
                    setupHooks.push({ hook, depth: test.describes.length });
                  }
                }
                for (let depth = test.describes.length - 1; depth >= 0; depth -= 1) {
                  for (const hook of hooksByDescribe.get(test.describes[depth]) ?? []) {
                    if (!isTeardown(hook.hookType)) {
                      setupHooks.push({ hook, depth });
                    }
                  }
                }
                setupHooks.sort(
                  (a, b) =>
                    phaseRank(a.hook.hookType) - phaseRank(b.hook.hookType) ||
                    b.depth - a.depth ||
                    a.hook.callNode.range[0] - b.hook.callNode.range[0],
                );
                for (const { hook } of setupHooks) {
                  if (hook.netFrozen !== null) {
                    state = hook.netFrozen;
                  }
                }
                for (const transition of transitionsByCallback.get(test.fn) ?? []) {
                  // Lifecycle order, not source order: a teardown registered
                  // above a test still runs after it, so every immediate
                  // transition the test body executes shapes the state the
                  // teardown callback sees.
                  if (transition.immediate) {
                    state = transition.frozen;
                  }
                }
                // The teardown hooks that run before the read. An
                // afterEach read sees the nested suites' afterEach hooks
                // (innermost first) and its own suite's earlier
                // registrations; an afterAll read additionally sees every
                // afterEach hook (ancestor describes and file level run
                // last) and the afterAll hooks of the nested suites and of
                // its own suite registered above it.
                const applyTeardown = (hook) => {
                  if (hook.netFrozen !== null) {
                    state = hook.netFrozen;
                  }
                };
                const readDepth = test.describes.length - enclosingDescribes.length;
                const nestedHooks = (depth, phase) => {
                  for (const hook of hooksByDescribe.get(test.describes[depth]) ?? []) {
                    if (hook.hookType === phase) {
                      applyTeardown(hook);
                    }
                  }
                };
                const ownEarlierHooks = (phase) => {
                  if (enclosingDescribes.length > 0) {
                    for (const hook of hooksByDescribe.get(enclosingDescribes[0]) ?? []) {
                      if (
                        hook.hookType === phase &&
                        hook.callNode.range[0] < enclosing.callNode.range[0]
                      ) {
                        applyTeardown(hook);
                      }
                    }
                  } else {
                    // File-level read: earlier file-level hooks of the read's
                    // own phase run before it (an earlier file-level afterAll
                    // restoration invalidates an earlier freeze just like a
                    // beforeEach's does for afterEach).
                    for (const hook of fileLevelHooks) {
                      if (
                        hook.hookType === phase &&
                        hook.callNode.range[0] < enclosing.callNode.range[0]
                      ) {
                        applyTeardown(hook);
                      }
                    }
                  }
                };
                if (enclosing.callType === "afterAll") {
                  for (let depth = 0; depth < test.describes.length; depth += 1) {
                    nestedHooks(depth, "afterEach");
                  }
                  for (const hook of fileLevelHooks) {
                    if (hook.hookType === "afterEach") {
                      applyTeardown(hook);
                    }
                  }
                  for (let depth = 0; depth < readDepth; depth += 1) {
                    nestedHooks(depth, "afterAll");
                  }
                  ownEarlierHooks("afterAll");
                } else {
                  for (let depth = 0; depth < readDepth; depth += 1) {
                    nestedHooks(depth, "afterEach");
                  }
                  ownEarlierHooks("afterEach");
                }
                if (!state) {
                  frozen = false;
                  break;
                }
              }
            }
          } else {
            for (const { hook } of sequence) {
              if (hook.netFrozen !== null) frozen = hook.netFrozen;
            }
          }
          const ownState = stateAt(transitionsByCallback.get(enclosing.fn) ?? []);
          if (ownState !== null) frozen = ownState;

          if (!frozen) {
            report();
          }
        }
      },
    };
  },
};

export default rule;
