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
    // Statically resolvable named helpers: function declarations and
    // variable-declared callable expressions, scoped to the function they
    // are declared in. An explicit invocation `helper()` resolves to its
    // body, so the body's timer transitions apply at the invocation site —
    // a helper that is merely declared still executes nothing.
    const helperScopes = [new Map()];
    const helperInvocations = new Map();
    const invocationHelpers = new Map();
    const registerHelper = (name, fn) => {
      if (name) {
        helperScopes[helperScopes.length - 1].set(name, fn);
      }
    };
    const resolveHelper = (name) => {
      for (let index = helperScopes.length - 1; index >= 0; index -= 1) {
        if (helperScopes[index].has(name)) {
          return helperScopes[index].get(name);
        }
      }
      return null;
    };
    const recordFreezeState = (node, calls) => {
      const enclosing = findEnclosingTestCallback(node);
      // Whether the transition executes when the enclosing callback runs is
      // decided once every invocation site is known (Program:exit): a helper
      // declared above may be invoked below — or never — so the traversal
      // order alone cannot tell.
      calls.push({ node, enclosing });
    };
    /** Register every identifier a binding pattern declares as a shadowing
     * (noncallable) name: helper resolution must not pass through it. */
    const registerShadowNames = (pattern) => {
      if (!pattern) return;
      if (pattern.type === "Identifier") {
        registerHelper(pattern.name, null);
      } else if (pattern.type === "AssignmentPattern") {
        registerShadowNames(pattern.left);
      } else if (pattern.type === "RestElement") {
        registerShadowNames(pattern.argument);
      } else if (pattern.type === "ArrayPattern") {
        for (const element of pattern.elements ?? []) registerShadowNames(element);
      } else if (pattern.type === "ObjectPattern") {
        for (const property of pattern.properties ?? []) {
          registerShadowNames(
            property.type === "RestElement" ? property.argument : property.value,
          );
        }
      }
    };
    const pushHelperScope = (fn) => {
      helperScopes.push(new Map());
      // The function's own parameters shadow outer helpers of the same
      // name: a call through a parameter is not a call through the helper.
      for (const parameter of fn.params ?? []) {
        registerShadowNames(parameter);
      }
      // Hoisted function declarations resolve for calls that textually
      // precede them within the same body.
      for (const statement of fn.body?.type === "BlockStatement" ? fn.body.body : []) {
        if (statement.type === "FunctionDeclaration") {
          registerHelper(statement.id?.name, statement);
        }
      }
    };
    return {
      Program(node) {
        for (const statement of node.body) {
          if (statement.type === "FunctionDeclaration") {
            registerHelper(statement.id?.name, statement);
          }
        }
      },
      FunctionDeclaration(node) {
        if (node.id) {
          registerHelper(node.id.name, node);
        }
        pushHelperScope(node);
      },
      "FunctionDeclaration:exit"() {
        helperScopes.pop();
      },
      FunctionExpression(node) {
        pushHelperScope(node);
      },
      "FunctionExpression:exit"() {
        helperScopes.pop();
      },
      ArrowFunctionExpression(node) {
        pushHelperScope(node);
      },
      "ArrowFunctionExpression:exit"() {
        helperScopes.pop();
      },
      BlockStatement(node) {
        // Lexical block scope: a block-local callable registration must not
        // outlive its block, and a block-local noncallable binding shadows
        // outer helpers only inside it. Function declarations are
        // initialized at block entry, so hoist them before descending (a
        // call textually above the declaration still resolves).
        helperScopes.push(new Map());
        for (const statement of node.body) {
          if (statement.type === "FunctionDeclaration") {
            registerHelper(statement.id?.name, statement);
          }
        }
      },
      "BlockStatement:exit"() {
        helperScopes.pop();
      },
      CatchClause(node) {
        helperScopes.push(new Map());
        registerShadowNames(node.param);
      },
      "CatchClause:exit"() {
        helperScopes.pop();
      },
      VariableDeclarator(node) {
        if (
          node.id.type === "Identifier" &&
          (node.init?.type === "FunctionExpression" ||
            node.init?.type === "ArrowFunctionExpression")
        ) {
          registerHelper(node.id.name, node.init);
        } else {
          // Any other binding shadows outer helpers of the same name —
          // including destructured patterns' bound names.
          registerShadowNames(node.id);
        }
      },
      AssignmentExpression(node) {
        if (node.operator !== "=" || node.left.type !== "Identifier") {
          return;
        }
        // A reassignment updates the callable the binding holds: each
        // invocation must resolve to the callable held at that point, not
        // the original initializer.
        for (let index = helperScopes.length - 1; index >= 0; index -= 1) {
          if (helperScopes[index].has(node.left.name)) {
            const callable =
              node.right.type === "FunctionExpression" ||
              node.right.type === "ArrowFunctionExpression";
            helperScopes[index].set(node.left.name, callable ? node.right : null);
            break;
          }
        }
      },
      CallExpression(node) {
        const callee = node.callee;

        // Record explicit invocations of statically resolvable named
        // helpers: a helper's transitions apply at the sites that invoke it.
        // The resolved identity is kept per call: ordering decisions made at
        // Program:exit cannot re-resolve names (the lexical scopes are gone
        // by then).
        if (callee.type === "Identifier") {
          const helper = resolveHelper(callee.name);
          if (helper) {
            const sites = helperInvocations.get(helper) ?? [];
            sites.push(node);
            helperInvocations.set(helper, sites);
            invocationHelpers.set(node, helper);
          }
        }

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

        // Detect vi.useFakeTimers(), jest.useFakeTimers(), vi.setSystemTime(), jest.setSystemTime().
        // The property must be NONCOMPUTED: a computed member's property
        // name is an expression to evaluate, not the invoked method's
        // identity — `vi[useRealTimers]()` may call anything.
        if (
          callee.type === "MemberExpression" &&
          !callee.computed &&
          callee.object.type === "Identifier" &&
          RUNNER_OBJECTS.has(callee.object.name) &&
          callee.property.type === "Identifier" &&
          (callee.property.name === "useFakeTimers" || callee.property.name === "setSystemTime")
        ) {
          recordFreezeState(node, freezeCalls);
          return;
        }

        // Detect vi.useRealTimers() / jest.useRealTimers(): the wall clock is
        // back, so any earlier freeze no longer covers later reads. Must be
        // noncomputed, like the freeze detection above.
        if (
          callee.type === "MemberExpression" &&
          !callee.computed &&
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

        /** The test/hook callback `fn` is the direct argument of, if any. */
        const callbackInfoOf = (fn) => {
          const call = fn.parent;
          if (call?.type !== "CallExpression") return null;
          const base = getCalleeBaseName(call.callee);
          if (base === null || !SCOPE_NAMES.has(base) || !call.arguments.includes(fn)) {
            return null;
          }
          return { fn, callType: base, callNode: call };
        };

        /**
         * Every context `fn`'s body runs in, as `{ root, sites }`: root is
         * the test/hook callback containing the outermost execution (null =
         * module evaluation) and sites the chain of invocation call nodes
         * (outermost first) leading into the body. An immediately-invoked
         * function runs wherever its own invocation sits, so IIFE boundaries
         * are ordering detail handled by the execution keys, not context
         * boundaries; a named helper runs at the sites that invoke it — if
         * nothing invokes it, it never runs.
         */
        const contextsOf = (fn, active) => {
          if (!fn) {
            return [{ root: null, sites: [] }];
          }
          if (active.has(fn)) {
            return [];
          }
          active.add(fn);
          try {
            const parent = fn.parent;
            if (parent?.type === "CallExpression" && parent.callee === fn) {
              return contextsOf(nearestFunction(parent), active);
            }
            if (callbackInfoOf(fn)) {
              return [{ root: fn, sites: [] }];
            }
            const contexts = [];
            for (const site of helperInvocations.get(fn) ?? []) {
              for (const outer of contextsOf(nearestFunction(site), active)) {
                contexts.push({ root: outer.root, sites: [...outer.sites, site] });
              }
            }
            return contexts;
          } finally {
            active.delete(fn);
          }
        };

        /**
         * executionKey limited to the IIFE frames between `node` and
         * `boundary` (the first non-IIFE function around it): frames above
         * the boundary belong to the invocation chain's keys.
         */
        const executionKeyWithin = (node, boundary) => {
          const chain = [];
          let current = node;
          while (current && current !== boundary) {
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
            const contained = index > 0 ? chain[index - 1] : node;
            key.push(invocation.range[0]);
            key.push(inBody(invocation, contained) ? 1 : 0);
          }
          key.push(node.range[0]);
          return key;
        };

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
          const fn = nearestFunction(call.node);
          // The first non-IIFE function boundary around the transition: the
          // context boundary whose invocation chain decides immediacy.
          let boundary = fn;
          while (boundary) {
            const parent = boundary.parent;
            if (parent?.type === "CallExpression" && parent.callee === boundary) {
              boundary = nearestFunction(parent);
              continue;
            }
            break;
          }
          const ownEntry = {
            node: call.node,
            frozen,
            immediate: false,
            fn,
            order: executionKey(call.node),
          };
          const pushOwnEntry = () => {
            // A transition trapped inside a closure that nothing here
            // invokes (an uncalled helper, a hook's unused restoration)
            // executes only if that closure is called later; it still
            // covers the reads inside its own body.
            if (call.enclosing && !DESCRIBE_NAMES.has(call.enclosing.callType)) {
              const list = transitionsByCallback.get(call.enclosing.fn) ?? [];
              list.push(ownEntry);
              transitionsByCallback.set(call.enclosing.fn, list);
            }
          };
          const contexts = contextsOf(boundary, new Set());
          if (contexts.length === 0) {
            pushOwnEntry();
            return;
          }
          for (const ctx of contexts) {
            const order = [];
            for (const site of ctx.sites) {
              order.push(...executionKey(site), 1);
            }
            order.push(...executionKeyWithin(call.node, boundary));
            const entry = {
              node: call.node,
              // The position the transition takes effect: the invocation
              // site that runs it, not the helper body's declaration.
              effNode: ctx.sites.length > 0 ? ctx.sites[ctx.sites.length - 1] : call.node,
              frozen,
              immediate: true,
              fn,
              order,
            };
            if (!ctx.root) {
              moduleTransitions.push(entry);
              continue;
            }
            const info = callbackInfoOf(ctx.root);
            if (DESCRIBE_NAMES.has(info.callType)) {
              describeTransitions.push(entry);
            }
            const list = transitionsByCallback.get(ctx.root) ?? [];
            list.push(entry);
            transitionsByCallback.set(ctx.root, list);
          }
          pushOwnEntry();
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
        // The callbacks come from the RESOLVED execution contexts (a
        // module-level helper invoked from a hook contributes its transitions
        // to that hook), not the transitions' lexical enclosing callback.
        const collectedTests = [];
        // Every before-hook (beforeAll/beforeEach) whose callback touches the
        // timer state, keyed by the describe it is registered in (`null` =
        // file level). `netFrozen` is the state the hook LEAVES behind: a
        // hook that freezes and then restores provides no coverage, and a
        // restoring hook actively undoes an earlier hook's freeze.
        const hooksByDescribe = new Map();
        const fileLevelHooks = [];
        const hookCallbacks = new Map();
        for (const callback of transitionsByCallback.keys()) {
          const info = callbackInfoOf(callback);
          if (!info) continue;
          if (
            !(
              BEFORE_HOOKS.has(info.callType) ||
              info.callType === "afterEach" ||
              info.callType === "afterAll"
            )
          ) {
            continue;
          }
          hookCallbacks.set(callback, info);
        }
        for (const [callback, info] of hookCallbacks) {
          const transitions = (transitionsByCallback.get(callback) ?? []).filter(
            (t) => t.immediate,
          );
          const hook = {
            hookType: info.callType,
            callNode: info.callNode,
            netFrozen: transitions.length > 0 ? transitions.at(-1).frozen : null,
          };
          const parentDescribe = findEnclosingDescribe(info.callNode);
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
         * Whether a transition executes before a read, by EXECUTION order —
         * the transition's ordering key against the read's execution key.
         * Source positions alone are wrong across invocation frames: an
         * IIFE's argument (a freeze) runs before the body that follows it in
         * the source, and a named helper's body runs after the invocation's
         * arguments, which precede it in the source.
         */
        const inSubtree = (ancestor, node) => {
          let probe = node;
          while (probe) {
            if (probe === ancestor) {
              return true;
            }
            probe = probe.parent;
          }
          return false;
        };
        const readExecutionKey = (node) => {
          // Invocation frames from the node upward: an IIFE's body runs
          // after its arguments (marker 1 vs 0), and a named-helper
          // invocation's arguments run before the helper's body (marker 0),
          // exactly the frames the transition orders are built from.
          const frames = [];
          let current = node;
          while (current) {
            const parent = current.parent;
            if (parent?.type === "CallExpression" || parent?.type === "NewExpression") {
              const callee = parent.callee;
              if (
                callee.type === "ArrowFunctionExpression" ||
                callee.type === "FunctionExpression"
              ) {
                frames.push({
                  call: parent,
                  marker: inSubtree(callee.body, node) ? 1 : 0,
                });
              } else if (
                callee.type === "Identifier" &&
                invocationHelpers.get(parent) &&
                !inSubtree(callee, current)
              ) {
                frames.push({ call: parent, marker: 0 });
              }
            }
            current = parent;
          }
          if (frames.length === 0) {
            return [node.range[0]];
          }
          const key = [];
          for (let index = frames.length - 1; index >= 0; index -= 1) {
            key.push(frames[index].call.range[0], frames[index].marker);
          }
          key.push(node.range[0]);
          return key;
        };
        const executesBefore = (transition, readNode) => {
          const readEntry = { node: readNode, order: readExecutionKey(readNode) };
          return compareExecution(transition, readEntry) < 0;
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
                executesBefore(t, node),
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
              .filter((t) => executesBefore(t, node))
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
              const applyTeardown = (state, hook) =>
                hook.netFrozen !== null ? hook.netFrozen : state;
              const readDepthOf = (test) =>
                test.describes.length - enclosingDescribes.length;
              const nestedHooks = (state, test, depth, phase) => {
                for (const hook of hooksByDescribe.get(test.describes[depth]) ?? []) {
                  if (hook.hookType === phase) {
                    state = applyTeardown(state, hook);
                  }
                }
                return state;
              };
              const ownEarlierHooks = (state, phase) => {
                if (enclosingDescribes.length > 0) {
                  for (const hook of hooksByDescribe.get(enclosingDescribes[0]) ?? []) {
                    if (
                      hook.hookType === phase &&
                      hook.callNode.range[0] < enclosing.callNode.range[0]
                    ) {
                      state = applyTeardown(state, hook);
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
                      state = applyTeardown(state, hook);
                    }
                  }
                }
                return state;
              };
              const collectSetupHooks = (test) => {
                // Setup hooks in lifecycle order — every beforeAll before
                // every beforeEach, outer describes before inner ones within
                // a phase, registration order within a describe — not in
                // source registration order across phases.
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
                return setupHooks;
              };
              // Both teardown reads replay the suite cumulatively in
              // execution order. An afterAll callback runs ONCE after the
              // entire suite, so it observes the accumulated state the
              // suite's tests leave behind; an afterEach callback runs after
              // EVERY covered test, so it stays covered only while the state
              // at each of its executions is frozen. An independently
              // replayed per-test snapshot would discard an earlier test's
              // restoration and re-apply a once-only beforeAll freeze. The
              // replay: a describe's beforeAll applies once before its first
              // covered test, beforeEach hooks re-apply before every test,
              // each test's immediate transitions follow, and the afterEach
              // hooks (innermost first, file level last) plus the completing
              // nested suites' afterAll hooks carry the state to the next
              // test.
              const isAfterAll = enclosing.callType === "afterAll";
              let state = collectionState;
              let covered = true;
              const beforeAllDone = new Set();
              const suiteTestTotals = new Map();
              const suiteTestSeen = new Map();
              for (const test of suiteTests) {
                for (const describeNode of test.describes) {
                  suiteTestTotals.set(
                    describeNode,
                    (suiteTestTotals.get(describeNode) ?? 0) + 1,
                  );
                }
              }
              for (const test of suiteTests) {
                for (const { hook } of collectSetupHooks(test)) {
                  if (hook.netFrozen === null) continue;
                  if (hook.hookType === "beforeAll") {
                    // Each beforeAll CALLBACK runs once before its suite's
                    // first covered test — not again before each later
                    // test, and not skipped because a sibling beforeAll of
                    // the same suite already ran.
                    if (beforeAllDone.has(hook)) continue;
                    beforeAllDone.add(hook);
                  }
                  state = hook.netFrozen;
                }
                for (const transition of transitionsByCallback.get(test.fn) ?? []) {
                  // Lifecycle order, not source order: a teardown
                  // registered above a test still runs after it, so every
                  // immediate transition the test body executes shapes the
                  // state the teardown callback sees.
                  if (transition.immediate) {
                    state = transition.frozen;
                  }
                }
                // The state the READ observes at this execution: the nested
                // suites' afterEach hooks and the read's own suite's earlier
                // registrations have run; ancestor describes' and file-level
                // afterEach hooks run after it.
                let readState = state;
                for (let depth = 0; depth < readDepthOf(test); depth += 1) {
                  readState = nestedHooks(readState, test, depth, "afterEach");
                }
                readState = ownEarlierHooks(readState, "afterEach");
                if (!readState) {
                  covered = false;
                }
                // Carry the state to the next test: EVERY afterEach hook
                // runs after each test — a restoration in one execution
                // leaves the next test's teardown on the real clock.
                for (let depth = 0; depth < test.describes.length; depth += 1) {
                  state = nestedHooks(state, test, depth, "afterEach");
                }
                for (const hook of fileLevelHooks) {
                  if (hook.hookType === "afterEach") {
                    state = applyTeardown(state, hook);
                  }
                }
                // Nested suites whose last covered test just finished run
                // their afterAll callbacks before the next test starts.
                for (let depth = 0; depth < readDepthOf(test); depth += 1) {
                  const describeNode = test.describes[depth];
                  const seen = (suiteTestSeen.get(describeNode) ?? 0) + 1;
                  suiteTestSeen.set(describeNode, seen);
                  if (seen === suiteTestTotals.get(describeNode)) {
                    state = nestedHooks(state, test, depth, "afterAll");
                  }
                }
              }
              if (isAfterAll) {
                // The read's own suite's earlier afterAll registrations run
                // after every test, right before the read.
                frozen = ownEarlierHooks(state, "afterAll");
              } else {
                frozen = covered;
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
