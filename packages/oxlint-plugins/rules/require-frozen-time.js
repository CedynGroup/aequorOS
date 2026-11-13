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
 * Top-level `const` bindings whose value is a plain array of string literals,
 * collected once per program so an `apis: <identifier>` option can be resolved
 * one level deep (module-scoped lists are the common shape). Returns the
 * literal string elements, or null when the binding is missing or unresolvable.
 */
function constStringArrayElements(program, name, depth = 0) {
  if (!program || depth > 1) return null;
  for (const statement of program.body) {
    if (statement.type !== "VariableDeclaration" || statement.kind !== "const") {
      continue;
    }
    for (const declaration of statement.declarations) {
      if (
        declaration.id.type !== "Identifier" ||
        declaration.id.name !== name ||
        declaration.init?.type !== "ArrayExpression"
      ) {
        continue;
      }
      const elements = [];
      for (const element of declaration.init.elements) {
        if (element == null) continue;
        if (element.type === "Literal" && typeof element.value === "string") {
          elements.push(element.value);
          continue;
        }
        if (
          element.type === "SpreadElement" &&
          element.argument.type === "Identifier"
        ) {
          const spread = constStringArrayElements(
            program,
            element.argument.name,
            depth + 1,
          );
          if (spread) elements.push(...spread);
        }
      }
      return elements;
    }
  }
  return null;
}

/**
 * Whether a declaration of `name` exists anywhere inside `container`
 * (bindings only: variable declarators and named function declarations).
 */
function declaresName(container, name) {
  const stack = [container];
  while (stack.length) {
    const current = stack.pop();
    if (!current || typeof current.type !== "string") continue;
    if (
      (current.type === "VariableDeclarator" &&
        current.id?.type === "Identifier" &&
        current.id.name === name) ||
      (current.type === "FunctionDeclaration" &&
        current.id?.type === "Identifier" &&
        current.id.name === name)
    ) {
      return true;
    }
    for (const key of Object.keys(current)) {
      if (key === "parent" || key === "loc" || key === "range") continue;
      const value = current[key];
      if (Array.isArray(value)) {
        stack.push(...value.filter((item) => item && typeof item.type === "string"));
      } else if (value && typeof value.type === "string") {
        stack.push(value);
      }
    }
  }
  return false;
}

/**
 * Whether a binding of `name` sits in any scope between the program root and
 * `node`: a shadowing binding makes the module-level const lookup unreliable,
 * so an `apis: <identifier>` reference must stay unresolved.
 */
function nameIsShadowedAbove(node, name) {
  for (let current = node.parent; current; current = current.parent) {
    if (current.type === "Program") break;
    if (
      current.type === "FunctionDeclaration" ||
      current.type === "FunctionExpression" ||
      current.type === "ArrowFunctionExpression" ||
      current.type === "BlockStatement"
    ) {
      if (declaresName(current, name)) return true;
    }
  }
  return false;
}

/**
 * Whether a node:test `timers.enable(...)` call also freezes `Date`: either
 * with no options (the default mocked apis include "Date") or with an `apis`
 * value that provably names "Date" — an array of literals or a resolvable
 * top-level const array. Options that exclude "Date", or whose `apis` value
 * the rule cannot resolve to literals naming "Date", leave the wall clock
 * live and must not silence the rule.
 */
function nodeTestTimersEnableFreezesDate(node, program) {
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
  let elements = null;
  if (apis.value.type === "ArrayExpression") {
    elements = apis.value.elements;
  } else if (
    apis.value.type === "Identifier" &&
    !nameIsShadowedAbove(node, apis.value.name)
  ) {
    const resolved = constStringArrayElements(program, apis.value.name);
    if (!resolved) return false;
    elements = resolved.map((value) => ({ type: "Literal", value }));
  } else {
    return false;
  }
  // Spread or hole elements are not proof that "Date" is mocked.
  return elements.some(
    (element) => element?.type === "Literal" && element.value === "Date",
  );
}

/**
 * Extract the test-framework scope name from a callee, unwrapping variants
 * like `describe.only(...)`, `it.each(data)(...)`, `it.each`...`(...)`, and
 * the Playwright member forms `test.describe(...)` / `test.beforeEach(...)`.
 * For a member callee the property name wins when it is itself a scope name,
 * so `test.beforeEach` is classified as a hook rather than a test.
 * Returns the scope name (e.g. "describe", "beforeEach") or `null`.
 */
function getCalleeBaseName(callee) {
  if (callee.type === "Identifier") return callee.name;
  if (callee.type === "MemberExpression" && callee.property.type === "Identifier") {
    if (SCOPE_NAMES.has(callee.property.name)) return callee.property.name;
    if (callee.object.type === "Identifier") return callee.object.name;
  }
  if (callee.type === "CallExpression") return getCalleeBaseName(callee.callee);
  if (callee.type === "TaggedTemplateExpression") return getCalleeBaseName(callee.tag);
  return null;
}

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
    // Freeze calls recorded with their enclosing test/hook callback info.
    const freezeCalls = [];
    // Time-dependent API usages to validate at the end.
    const timeDependentNodes = [];
    // True when a top-level beforeEach/beforeAll (outside any describe) has a freeze call.
    let hasFileLevelFreezing = false;
    // The program root, for resolving top-level const `apis` lists.
    let programNode = null;

    return {
      Program(node) {
        programNode = node;
      },

      CallExpression(node) {
        const callee = node.callee;

        // Detect vi.useFakeTimers(), jest.useFakeTimers(), vi.setSystemTime(), jest.setSystemTime()
        if (
          callee.type === "MemberExpression" &&
          callee.object.type === "Identifier" &&
          RUNNER_OBJECTS.has(callee.object.name) &&
          callee.property.type === "Identifier" &&
          (callee.property.name === "useFakeTimers" || callee.property.name === "setSystemTime")
        ) {
          const enclosing = findEnclosingTestCallback(node);
          freezeCalls.push({ node, enclosing });
          return;
        }

        // Detect page.clock.install(), context.clock.setFixedTime(), ... —
        // Playwright's clock API freezes both timers and Date.
        if (isClockFreezeCall(callee)) {
          const enclosing = findEnclosingTestCallback(node);
          freezeCalls.push({ node, enclosing });
          return;
        }

        // Detect node:test's timer mock freezing Date.
        if (isNodeTestTimersEnableCall(callee)) {
          if (nodeTestTimersEnableFreezesDate(node, programNode)) {
            const enclosing = findEnclosingTestCallback(node);
            freezeCalls.push({ node, enclosing });
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

        // Build a set of describe callback nodes that have a beforeEach/beforeAll with freezing.
        const frozenDescribeScopes = new Set();
        // Build a set of individual test/hook callback nodes that contain a freeze call.
        const frozenCallbackScopes = new Set();

        for (const { enclosing } of freezeCalls) {
          if (!enclosing) {
            // Freeze at file/module scope — not scoped to any test callback.
            continue;
          }

          frozenCallbackScopes.add(enclosing.fn);

          // If the freeze is in a hook that runs before tests, mark the parent
          // describe as frozen; a top-level such hook freezes the entire file.
          // Teardown hooks (afterEach/afterAll) run after the tests, so their
          // freezes never cover them.
          if (BEFORE_HOOKS.has(enclosing.callType)) {
            const parentDescribe = findEnclosingDescribe(enclosing.callNode);
            if (parentDescribe) {
              frozenDescribeScopes.add(parentDescribe);
            } else {
              hasFileLevelFreezing = true;
            }
          }
        }

        for (const { node, api } of timeDependentNodes) {
          const enclosing = findEnclosingTestCallback(node);

          // 0) A top-level beforeEach/beforeAll covers the tests that run after
          //    it. Module-level and describe-body reads execute during
          //    collection, before any hook, so they stay reported.
          if (hasFileLevelFreezing && enclosing && TEST_NAMES.has(enclosing.callType)) {
            continue;
          }

          // 1) Node is inside a callback that itself contains a freeze call, and
          //    the freeze occurs before this node.
          if (enclosing && frozenCallbackScopes.has(enclosing.fn)) {
            const freezeBefore = freezeCalls.some(
              (fc) => fc.enclosing?.fn === enclosing.fn && fc.node.range[0] < node.range[0]
            );
            if (freezeBefore) continue;
          }

          // 2) Node is inside a test/hook whose ancestor describe has a frozen
          //    beforeEach/beforeAll (check all ancestor describes, not just immediate parent).
          if (enclosing) {
            const ancestors = findAllEnclosingDescribes(enclosing.callNode);
            if (ancestors.some((d) => frozenDescribeScopes.has(d))) {
              continue;
            }
          }

          // 3) Node is directly inside a describe callback (not in any it/beforeEach)
          //    — check if any ancestor describe is frozen.
          if (!enclosing) {
            const ancestors = findAllEnclosingDescribes(node);
            if (ancestors.some((d) => frozenDescribeScopes.has(d))) {
              continue;
            }
          }

          context.report({
            node,
            messageId: "missingFreezeTime",
            data: { api },
          });
        }
      },
    };
  },
};

export default rule;
