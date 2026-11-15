/**
 * Oxlint JS plugin rule: acts/no-weak-test-awaits (ACTS002)
 *
 * Prevents tests from synchronizing with implementation-specific promise-chain
 * depth or wall-clock sleeps:
 *   - `await new Promise((r) => setTimeout(r, n))`, including through aliased
 *     helpers that return such a promise;
 *   - a bare statement `await Promise.resolve()`.
 *
 * Enable only for test-file globs via .oxlintrc.json overrides.
 */

function isIdentifier(node, name) {
  return node?.type === "Identifier" && node.name === name;
}

function isPromiseResolveCall(node) {
  return (
    node?.type === "CallExpression" &&
    node.callee.type === "MemberExpression" &&
    !node.callee.computed &&
    isIdentifier(node.callee.object, "Promise") &&
    isIdentifier(node.callee.property, "resolve") &&
    // An awaited `Promise.resolve(value)` carries a real promise; only the
    // argument-free flush is the weak microtask wait.
    node.arguments.length === 0
  );
}

function invokesResolver(node, resolverName, allowBareIdentifier = false) {
  if (allowBareIdentifier && isIdentifier(node, resolverName)) {
    return true;
  }

  if (!node || typeof node !== "object") {
    return false;
  }

  if (node.type === "CallExpression" && isIdentifier(node.callee, resolverName)) {
    return true;
  }

  return Object.entries(node).some(([key, value]) => {
    if (key === "parent") {
      return false;
    }
    if (Array.isArray(value)) {
      return value.some((child) => invokesResolver(child, resolverName));
    }
    return invokesResolver(value, resolverName);
  });
}

function isSetTimeoutCall(node) {
  if (isIdentifier(node, "setTimeout")) {
    return true;
  }

  return (
    node?.type === "MemberExpression" &&
    !node.computed &&
    (isIdentifier(node.object, "global") ||
      isIdentifier(node.object, "globalThis") ||
      isIdentifier(node.object, "window")) &&
    isIdentifier(node.property, "setTimeout")
  );
}

function containsSetTimeoutWait(node, resolverName) {
  if (!node || typeof node !== "object") {
    return false;
  }

  if (
    node.type === "CallExpression" &&
    isSetTimeoutCall(node.callee) &&
    invokesResolver(node.arguments[0], resolverName, true)
  ) {
    return true;
  }

  return Object.entries(node).some(([key, value]) => {
    if (key === "parent") {
      return false;
    }
    if (Array.isArray(value)) {
      return value.some((child) => containsSetTimeoutWait(child, resolverName));
    }
    return containsSetTimeoutWait(value, resolverName);
  });
}

function isSetTimeoutExecutor(node) {
  if (node?.type !== "ArrowFunctionExpression" && node?.type !== "FunctionExpression") {
    return false;
  }

  const resolver = node.params[0];
  if (resolver?.type !== "Identifier") {
    return false;
  }

  return containsSetTimeoutWait(node.body, resolver.name);
}

function isSetTimeoutPromise(node) {
  return (
    node?.type === "NewExpression" &&
    isIdentifier(node.callee, "Promise") &&
    isSetTimeoutExecutor(node.arguments[0])
  );
}

/** Binding kinds: a weak timer-promise VALUE, or a function RETURNING one. */
const WEAK_PROMISE = "promise";
const WEAK_HELPER = "helper";
/**
 * A function declaration whose body references helpers that may be declared
 * after it. Its weakness is resolved at each read site against the bindings
 * standing there, so a weak helper declared later still taints the chain.
 */
const WEAK_UNRESOLVED = "unresolved";

/** Array methods whose first argument is invoked as a callback. */
const CALLBACK_INVOKERS = new Set([
  "map",
  "filter",
  "forEach",
  "some",
  "every",
  "find",
  "findIndex",
  "flatMap",
]);

/** Setup hooks whose callbacks run before the tests they precede. */
const SETUP_HOOKS = new Set(["beforeEach", "beforeAll"]);
const DESCRIBE_NAMES = new Set(["describe"]);

/** Look a name up along a chain of binding maps, innermost last. */
function resolveInChain(chain, name) {
  for (let index = chain.length - 1; index >= 0; index -= 1) {
    if (chain[index].has(name)) {
      return chain[index].get(name);
    }
  }
  return false;
}

/** Parameter bindings for a function, as an extra innermost scope map. */
function parameterBindings(fn) {
  const bindings = new Map();
  for (const parameter of fn.params ?? []) {
    if (parameter.type === "Identifier") {
      bindings.set(parameter.name, false);
    }
  }
  return bindings;
}

/**
 * The declaration bound as unresolved for `name` at a site whose bindings
 * are `chain`: the innermost scope holding an unresolved binding for the
 * name, and the declaration registered for that scope. The registry lives
 * on each scope object, so a nested helper never shadows an outer one and
 * sibling scopes (which reuse a stack slot) never overwrite each other.
 */
function unresolvedDeclaration(name, chain, scopeChain) {
  for (let index = chain.length - 1; index >= 0; index -= 1) {
    if (chain[index].get(name) === WEAK_UNRESOLVED) {
      const entry = scopeChain?.[index]?.declarations?.get(name);
      if (entry) {
        return entry;
      }
    }
  }
  return null;
}

/**
 * Whether an unresolved function declaration (one whose body references
 * helpers that may be declared after it) reads as a weak helper at a site
 * whose bindings are `chain`. The body is classified against the helper's
 * DEFINING scope — everything up to and including the scope its binding
 * lives in — so a caller that shadows a dependency's name cannot hide the
 * dependency, with the parameters and the body's own local declarations
 * bound on top; `ctx.visiting` breaks recursion on self-referencing helpers
 * and `ctx.memo` caches results computed at a helper's own defining scope
 * for the duration of one classification pass.
 */
function unresolvedHelperIsWeak(name, chain, scopeChain, ctx) {
  const found = unresolvedDeclaration(name, chain, scopeChain);
  ctx ??= { visiting: new Set(), memo: new Map() };
  if (!found || ctx.visiting.has(found.fn)) {
    return false;
  }
  // The classification of a helper depends only on its body and the scope
  // chain from the root to its defining scope — the site's own scopes above
  // the defining one are sliced away, and the parameters and body locals are
  // synthesized from the declaration itself. A chain that reaches the same
  // defining scope (from any site, including through recursive
  // classifications that appended local maps) therefore produces the same
  // result, so the memo keys on the defining scope's binding map.
  const cached = ctx.memo.get(found.fn);
  if (cached && cached.definingChain === chain[found.slot]) {
    return cached.result;
  }
  // The helper's body sees its parameters and its own declared names before
  // anything in the enclosing scopes: a local `sleep` must not resolve to a
  // module-level timer helper of the same name.
  const locals = bodyDeclaredNames(found.fn.body);
  const bindings = parameterBindings(found.fn);
  for (const local of locals) {
    if (!bindings.has(local)) {
      bindings.set(local, false);
    }
  }
  ctx.visiting.add(found.fn);
  let result;
  try {
    result = weakUseIn(
      found.fn.body,
      [...chain.slice(0, found.slot + 1), bindings],
      false,
      [
        ...(scopeChain?.slice(0, found.slot + 1) ?? []),
        { declarations: new Map() },
      ],
      ctx,
    );
  } finally {
    ctx.visiting.delete(found.fn);
  }
  ctx.memo.set(found.fn, { definingChain: chain[found.slot], result });
  return result;
}

/**
 * Names bound by declarations directly in the helper's top-level body. A
 * `const`/`let` declared in a nested block ends at that block, so it must
 * not shadow an outer binding for the whole body; `var` declarations are
 * function-scoped and are collected wherever they sit. Nested functions'
 * declarations end at their own boundary and never leak out.
 */
function bodyDeclaredNames(body) {
  const names = new Set();
  const collect = (statement) => {
    if (!statement || typeof statement !== "object" || !statement.type) {
      return;
    }
    if (statement.type === "VariableDeclaration") {
      for (const declarator of statement.declarations ?? []) {
        if (declarator.id?.type === "Identifier") {
          names.add(declarator.id.name);
        }
      }
      return;
    }
    if (statement.type === "FunctionDeclaration") {
      if (statement.id) {
        names.add(statement.id.name);
      }
      return;
    }
    // A `var` inside a nested block or function-level statement is still
    // function-scoped: find one anywhere below this statement.
    if (hasVarDeclaration(statement)) {
      for (const name of varDeclarations(statement)) {
        names.add(name);
      }
    }
  };
  if (body?.type === "BlockStatement") {
    for (const statement of body.body ?? []) {
      collect(statement);
    }
  }
  return names;
}

/** Whether any statement below `node` declares a function-scoped `var`. */
function hasVarDeclaration(node) {
  for (const _ of varDeclarations(node)) {
    return true;
  }
  return false;
}

/** Names declared by `var` declarations anywhere below `node`. */
function* varDeclarations(node) {
  if (!node || typeof node !== "object" || !node.type) {
    return;
  }
  if (node.type === "VariableDeclaration" && node.kind === "var") {
    for (const declarator of node.declarations ?? []) {
      if (declarator.id?.type === "Identifier") {
        yield declarator.id.name;
      }
    }
    return;
  }
  if (
    node.type === "FunctionDeclaration" ||
    node.type === "FunctionExpression" ||
    node.type === "ArrowFunctionExpression"
  ) {
    // A nested function's `var` ends at its own boundary.
    return;
  }
  for (const [key, value] of Object.entries(node)) {
    if (key === "parent") {
      continue;
    }
    if (Array.isArray(value)) {
      for (const child of value) {
        yield* varDeclarations(child);
      }
    } else {
      yield* varDeclarations(value);
    }
  }
}

/**
 * Whether `node` uses a weak timer promise, resolved against `chain` (an
 * array of binding maps, innermost last). In `classify` mode (a function body
 * being classified as a helper) any reference to a weak binding counts; in
 * `await` mode (an awaited expression) a weak HELPER counts only when it is
 * INVOKED — a bare reference such as `expect(sleep).toBeDefined()` asserts on
 * the helper, it does not wait on a timer — and a noncomputed property named
 * like a helper (`state.sleep`) is a property name, never a reference.
 * `scopeChain` (the scope objects parallel to `chain`) resolves unresolved
 * helper chains at the read site; `ctx.visiting` breaks recursion and
 * `ctx.memo` caches classifications for one pass.
 */
function weakUseIn(node, chain, awaitMode, scopeChain = null, ctx = null) {
  if (!node || typeof node !== "object") {
    return false;
  }

  if (isSetTimeoutPromise(node)) {
    return true;
  }

  if (node.type === "Identifier") {
    const kind = resolveInChain(chain, node.name);
    if (!kind) return false;
    if (kind === WEAK_UNRESOLVED) {
      return !awaitMode && unresolvedHelperIsWeak(node.name, chain, scopeChain, ctx);
    }
    return !awaitMode || kind === WEAK_PROMISE;
  }

  if (node.type === "CallExpression") {
    const callee = node.callee;
    if (callee.type === "Identifier") {
      const kind = resolveInChain(chain, callee.name);
      if (kind === WEAK_UNRESOLVED) {
        // The identifier branch fully decides the callee's contribution;
        // resolving it again in the fallback below would repeat the body
        // classification and compound exponentially through chains of
        // forwarding declarations.
        if (unresolvedHelperIsWeak(callee.name, chain, scopeChain, ctx)) {
          return true;
        }
      } else if (kind) {
        return true;
      }
    }
    if (callee.type === "MemberExpression" && !callee.computed) {
      const methodName = callee.property.type === "Identifier" ? callee.property.name : null;
      // `sleep.call(...)`/`sleep.apply(...)` invoke the helper and return its
      // timer promise.
      if (methodName === "call" || methodName === "apply") {
        const receiverKind = resolveInChain(chain, callee.object.name ?? "");
        if (receiverKind === WEAK_UNRESOLVED) {
          if (unresolvedHelperIsWeak(callee.object.name ?? "", chain, scopeChain, ctx)) {
            return true;
          }
        } else if (receiverKind) {
          return true;
        }
      }
      // `sleep.bind(null, 10)(...)` invokes the bound helper at the outer
      // call: the bound expression carries the helper's timer promise, so a
      // weak binding must survive the bind — like `.call`/`.apply`, any
      // weak receiver kind counts.
      if (methodName === "bind" && callee.object.type === "Identifier") {
        const receiverKind = resolveInChain(chain, callee.object.name);
        if (receiverKind === WEAK_UNRESOLVED) {
          if (unresolvedHelperIsWeak(callee.object.name, chain, scopeChain, ctx)) {
            return true;
          }
        } else if (receiverKind) {
          return true;
        }
      }
      // A timer helper handed to a callback-invoking array method waits
      // exclusively on timer promises.
      if (CALLBACK_INVOKERS.has(methodName) && node.arguments[0]?.type === "Identifier") {
        const callbackKind = resolveInChain(chain, node.arguments[0].name);
        if (callbackKind === WEAK_UNRESOLVED) {
          if (unresolvedHelperIsWeak(node.arguments[0].name, chain, scopeChain, ctx)) {
            return true;
          }
        } else if (callbackKind) {
          return true;
        }
      }
      // `obj.sleep()`: the property name is not a helper reference; only the
      // receiver can carry a weak binding.
      if (weakUseIn(callee.object, chain, awaitMode, scopeChain, ctx)) return true;
    } else if (
      callee.type !== "Identifier" &&
      // The callee of an awaited call is INVOKED, whatever expression
      // produces it — `await (0, sleep)(10)` waits on sleep's timer promise
      // — so the callee expression is resolved in reference mode, not
      // await mode.
      weakUseIn(callee, chain, false, scopeChain, ctx)
    ) {
      return true;
    }
    return node.arguments.some((argument) => weakUseIn(argument, chain, awaitMode, scopeChain, ctx));
  }

  if (node.type === "MemberExpression") {
    if (weakUseIn(node.object, chain, awaitMode, scopeChain, ctx)) return true;
    // A noncomputed property name never references an outer binding.
    return node.computed ? weakUseIn(node.property, chain, awaitMode, scopeChain, ctx) : false;
  }

  if (
    node.type === "Property" ||
    node.type === "MethodDefinition" ||
    node.type === "PropertyDefinition"
  ) {
    // The value is always live code (a computed key may be too); only a
    // noncomputed key is a mere name.
    if (weakUseIn(node.value, chain, awaitMode, scopeChain, ctx)) return true;
    return node.computed ? weakUseIn(node.key, chain, awaitMode, scopeChain, ctx) : false;
  }

  return Object.entries(node).some(([key, value]) => {
    if (key === "parent" || key === "label") {
      return false;
    }
    if (Array.isArray(value)) {
      return value.some((child) => weakUseIn(child, chain, awaitMode, scopeChain, ctx));
    }
    return weakUseIn(value, chain, awaitMode, scopeChain, ctx);
  });
}

const rule = {
  meta: {
    type: "problem",
    docs: {
      description: "Disallow brittle microtask flushes and sleep-style waits in tests",
    },
    messages: {
      weakAwait:
        "ACTS002: this await couples the test to promise-chain depth or elapsed wall-clock time. Wait for an observable condition instead: expect.poll, vi.waitFor, a findBy* query or a locator assertion.",
    },
    schema: [],
  },
  create(context) {
    const scopes = [];
    // Awaits that looked clean when visited, kept with the binding-map chain
    // they were seen under. A helper declared or assigned LATER can make them
    // weak (forward-declared helpers wrap timer helpers), so every weak
    // declaration re-evaluates the pending awaits against their own chain.
    const pendingAwaits = [];

    /** Record a declaration in the registry of its binding's scope. */
    function registerFunctionDeclaration(declaration) {
      if (!declaration.id) {
        // An anonymous declaration (`export default function () {}`) cannot
        // be referenced by name; there is nothing to register.
        return;
      }
      const scope = scopes[scopes.length - 1];
      if (!scope.declarations) {
        scope.declarations = new Map();
      }
      // The entry carries the declaration's DEFINING scope: an alias
      // registered in a deeper scope must still be classified against the
      // scope its function was declared in, or a shadowing dependency in
      // the alias's scope would hide the real one.
      scope.declarations.set(declaration.id.name, {
        fn: declaration,
        slot: scopes.length - 1,
      });
    }

    /** The declaration an unresolved alias forwards to, if one is visible. */
    function unresolvedAliasTarget(name) {
      for (let index = scopes.length - 1; index >= 0; index -= 1) {
        if (scopes[index].bindings.get(name) === WEAK_UNRESOLVED) {
          return scopes[index].declarations?.get(name) ?? null;
        }
      }
      return null;
    }

    /**
     * Record an alias of an unresolved helper under the alias's name. The
     * entry is stored as-is, so it keeps the target function and its
     * original defining scope.
     */
    function registerAliasDeclaration(name, entry) {
      const scope = scopes[scopes.length - 1];
      if (!scope.declarations) {
        scope.declarations = new Map();
      }
      scope.declarations.set(name, entry);
    }

    function pushScope(type = "block") {
      // `declarations` is created lazily; the scope object itself is the
      // registry key, so sibling scopes reusing a stack slot never overwrite
      // each other's entries.
      scopes.push({ type, bindings: new Map() });
    }

    function popScope() {
      scopes.pop();
    }

    function currentChain() {
      return scopes.map((scope) => scope.bindings);
    }

    /** The index of the innermost function/hook/program scope. */
    function functionDepth() {
      for (let index = scopes.length - 1; index >= 0; index -= 1) {
        if (
          scopes[index].type === "function" ||
          scopes[index].type === "program" ||
          scopes[index].type === "hook"
        ) {
          return index;
        }
      }
      return 0;
    }

    function resolveKind(name) {
      return resolveInChain(currentChain(), name);
    }

    function declareIdentifier(node, kind, varKind) {
      if (node?.type === "Identifier") {
        const targetScope =
          varKind === "var"
            ? scopes.findLast(
                (scope) =>
                  scope.type === "function" ||
                  scope.type === "program" ||
                  scope.type === "hook",
              )
            : scopes.at(-1);
        targetScope?.bindings.set(node.name, kind);
      }
    }

    function declareParameters(node) {
      for (const parameter of node.params ?? []) {
        declareIdentifier(parameter, false);
      }
    }

    /**
     * The scope an assignment to `name` updates, and where an alias of an
     * unresolved helper must be registered: innermost first when the write
     * propagates (a setup hook or a synchronously executed callback),
     * otherwise confined to the current function.
     */
    function writeTargetScope(name) {
      let boundary = scopes.length - 1;
      while (
        boundary >= 0 &&
        scopes[boundary].type !== "function" &&
        scopes[boundary].type !== "program" &&
        scopes[boundary].type !== "hook"
      ) {
        boundary -= 1;
      }
      if (boundary >= 0 && (scopes[boundary].type === "hook" || scopes[boundary].iife)) {
        // Innermost first, from the current scope (a hook-local block
        // binding wins over everything) out to the module binding: a hook
        // assignment updates the nearest visible binding, so a suite-level
        // shadow wins over the module binding.
        for (let index = scopes.length - 1; index >= 0; index -= 1) {
          if (scopes[index].bindings.has(name)) {
            return scopes[index];
          }
        }
      }
      for (let index = scopes.length - 1; index >= boundary; index -= 1) {
        if (scopes[index].bindings.has(name)) {
          return scopes[index];
        }
      }
      // A captured outer binding reassigned inside this function: the
      // function-local override is where the awaits that follow it look.
      return boundary >= 0 ? scopes[boundary] : null;
    }

    function assignIdentifier(node, kind) {
      if (node?.type !== "Identifier") {
        return;
      }

      // Confine write effects to the function whose body the assignment sits
      // in: an assignment inside an uncalled function must not change the
      // classification the enclosing scopes already have. A setup hook is
      // the exception — its callback runs before the tests, so its
      // assignment to an outer binding propagates outward.
      writeTargetScope(node.name)?.bindings.set(node.name, kind);
    }

    /** The kind an alias inherits: helpers alias to helpers, promises to
     * promises, and an unresolved helper stays unresolved through aliases so
     * its dependency is resolved when it becomes known. */
    function aliasedKind(init) {
      if (init?.type !== "Identifier") return null;
      const kind = resolveKind(init.name);
      return kind === WEAK_HELPER || kind === WEAK_PROMISE || kind === WEAK_UNRESOLVED
        ? kind
        : null;
    }

    /** Classify the kind a declarator/assignment binds, from its right side. */
    function boundKind(right) {
      const alias = aliasedKind(right);
      if (alias) return alias;
      if (isFunctionNode(right)) {
        return functionContainsWeakTimerPromise(right) ? WEAK_HELPER : false;
      }
      return weakUseIn(right, currentChain(), false, [...scopes]) ? WEAK_PROMISE : false;
    }

    /**
     * Pending awaits are only judged once the whole file has been visited:
     * a setup hook registered after a test may assign a timer helper and
     * then replace it with a real operation, and the test only ever runs
     * with the final assignment. Judging earlier would report on state the
     * test never observes.
     */
    function flushPendingAwaits() {
      for (const pending of pendingAwaits) {
        // The pending's own function scopes are evaluated with the binding
        // values as they stood at the await: reassignments that happened
        // after it in the same function must not reclassify it, while
        // enclosing scopes stay live so later helper declarations and hook
        // assignments still reach it. The scope objects are live references,
        // so a helper registered later is still resolvable; only the binding
        // VALUES are frozen per scope through `own`.
        const evaluationChain = [...pending.chain.slice(0, pending.depth), ...pending.own];
        if (weakUseIn(pending.argument, evaluationChain, true, pending.scopeChain)) {
          context.report({ node: pending.node, messageId: "weakAwait" });
        }
      }
      pendingAwaits.length = 0;
    }

    // Classify a function's own body with its parameters already bound, so a
    // parameter that shadows an outer weak-timer helper is not mistaken for
    // the helper it shadows. The body's own declared names are bound too: a
    // local `sleep` must not resolve to a module-level timer helper of the
    // same name. (Functions nested inside the scanned body are still
    // classified by plain recursion; their params are not bound.)
    function functionContainsWeakTimerPromise(fn) {
      pushScope("function");
      declareParameters(fn);
      const own = scopes[scopes.length - 1];
      for (const local of bodyDeclaredNames(fn.body)) {
        if (!own.bindings.has(local)) {
          own.bindings.set(local, false);
        }
      }
      const contains = weakUseIn(fn.body, currentChain(), false, [...scopes]);
      popScope();
      return contains;
    }

    function isFunctionNode(node) {
      return (
        node?.type === "FunctionDeclaration" ||
        node?.type === "FunctionExpression" ||
        node?.type === "ArrowFunctionExpression"
      );
    }

    function declareFunctionDeclarations(statements) {
      const declarations = [];
      for (const statement of statements ?? []) {
        const declaration =
          statement.type === "ExportNamedDeclaration" ||
          statement.type === "ExportDefaultDeclaration"
            ? statement.declaration
            : statement;
        if (declaration?.type === "FunctionDeclaration") {
          // An anonymous declaration (`export default function () {}`) has no
          // name to bind or register; it cannot be referenced as a helper.
          if (!declaration.id) {
            continue;
          }
          declarations.push(declaration);
          registerFunctionDeclaration(declaration);
          declareIdentifier(declaration.id, WEAK_UNRESOLVED, "function");
        }
      }

      for (let pass = 0; pass < declarations.length; pass += 1) {
        let changed = false;
        for (const declaration of declarations) {
          if (
            resolveKind(declaration.id.name) !== WEAK_HELPER &&
            functionContainsWeakTimerPromise(declaration)
          ) {
            declareIdentifier(declaration.id, WEAK_HELPER, "function");
            changed = true;
          }
        }
        if (!changed) {
          break;
        }
      }
    }

    /** The scope type a function pushes: a setup hook's callback or a plain function. */
    function scopeTypeFor(node) {
      const callee = node.parent?.callee;
      // Hooks are invoked as bare calls (`beforeEach(...)`) and as members of
      // the runner object (`test.beforeEach(...)`); both name the hook in the
      // callee's last identifier.
      const hookName =
        callee?.type === "Identifier"
          ? callee.name
          : callee?.type === "MemberExpression" && !callee.computed && callee.property?.type === "Identifier"
            ? callee.property.name
            : null;
      return hookName !== null && SETUP_HOOKS.has(hookName) ? "hook" : "function";
    }

    /** Whether the function's parent call runs it where it sits: an IIFE
     * (the function is the callee) or a describe callback (a synchronously
     * executed callback argument). */
    function executesWhereDefined(node) {
      const call = node.parent;
      if (call?.type !== "CallExpression") {
        return false;
      }
      if (call.callee === node) {
        // Immediately invoked: the function is the callee.
        return node.type === "ArrowFunctionExpression" || node.type === "FunctionExpression";
      }
      const callee = call.callee;
      const baseName =
        callee?.type === "Identifier"
          ? callee.name
          : callee?.type === "MemberExpression" && !callee.computed && callee.property?.type === "Identifier"
            ? callee.property.name
            : null;
      return baseName !== null && DESCRIBE_NAMES.has(baseName);
    }

    return {
      Program(node) {
        pushScope("program");
        declareFunctionDeclarations(node.body);
      },
      "Program:exit"() {
        popScope();
        flushPendingAwaits();
      },
      BlockStatement(node) {
        pushScope();
        declareFunctionDeclarations(node.body);
      },
      "BlockStatement:exit": popScope,
      ForStatement: pushScope,
      "ForStatement:exit": popScope,
      ForInStatement: pushScope,
      "ForInStatement:exit": popScope,
      ForOfStatement: pushScope,
      "ForOfStatement:exit": popScope,
      SwitchStatement: pushScope,
      "SwitchStatement:exit": popScope,
      FunctionDeclaration(node) {
        // A weak body is cached as a helper; anything else stays unresolved
        // (its helpers may be declared later) and is re-classified at each
        // read site against its defining scope's bindings — the registry
        // slot is the scope the binding lives in, so a helper nested in an
        // inner scope never shadows an outer one of the same name.
        registerFunctionDeclaration(node);
        declareIdentifier(
          node.id,
          functionContainsWeakTimerPromise(node) ? WEAK_HELPER : WEAK_UNRESOLVED,
          "function",
        );
        pushScope(scopeTypeFor(node));
        declareParameters(node);
      },
      "FunctionDeclaration:exit": popScope,
      FunctionExpression(node) {
        pushScope(scopeTypeFor(node));
        // A synchronously executed function (IIFE, describe callback) runs
        // where it sits: its writes to captured bindings reach the
        // enclosing scopes like a hook's do.
        if (executesWhereDefined(node)) {
          scopes[scopes.length - 1].iife = true;
        }
        declareParameters(node);
      },
      "FunctionExpression:exit": popScope,
      ArrowFunctionExpression(node) {
        pushScope(scopeTypeFor(node));
        if (executesWhereDefined(node)) {
          scopes[scopes.length - 1].iife = true;
        }
        declareParameters(node);
      },
      "ArrowFunctionExpression:exit": popScope,
      CatchClause(node) {
        pushScope();
        declareIdentifier(node.param, false);
      },
      "CatchClause:exit": popScope,
      VariableDeclarator(node) {
        const kind = boundKind(node.init);
        if (
          kind === WEAK_UNRESOLVED &&
          node.id.type === "Identifier" &&
          node.init.type === "Identifier"
        ) {
          // The alias forwards to an unresolved helper: record the same
          // declaration under the alias so reads through it resolve the
          // dependency when it becomes known.
          const target = unresolvedAliasTarget(node.init.name);
          if (target) {
            registerAliasDeclaration(node.id.name, target);
          }
        }
        declareIdentifier(node.id, kind, node.parent?.kind);
      },
      AssignmentExpression(node) {
        if (node.operator === "=") {
          const kind = boundKind(node.right);
          if (node.left.type === "Identifier") {
            // An assigned alias forwards to an unresolved helper the same
            // way a declarator alias does, so reads through it resolve the
            // dependency when it becomes known. The registry entry belongs
            // in the scope the binding was updated in — a hook assigning to
            // an outer binding registers there, not in the hook's scope.
            const scope = writeTargetScope(node.left.name);
            if (kind === WEAK_UNRESOLVED && node.right.type === "Identifier") {
              const target = unresolvedAliasTarget(node.right.name);
              if (target && scope) {
                if (!scope.declarations) {
                  scope.declarations = new Map();
                }
                scope.declarations.set(node.left.name, target);
              }
            }
            assignIdentifier(node.left, kind);
          }
        }
      },
      AwaitExpression(node) {
        const isBarePromiseResolve =
          node.parent?.type === "ExpressionStatement" && isPromiseResolveCall(node.argument);
        if (isBarePromiseResolve) {
          context.report({ node, messageId: "weakAwait" });
          return;
        }

        const chain = currentChain();
        if (weakUseIn(node.argument, chain, true, [...scopes])) {
          context.report({ node, messageId: "weakAwait" });
          return;
        }

        // Clean for now: a later weak declaration can still make it weak.
        pendingAwaits.push({
          node,
          argument: node.argument,
          chain,
          depth: functionDepth(),
          own: chain.slice(functionDepth()).map((map) => new Map(map)),
          // Live scope objects parallel to `chain`: the declaration registry
          // resolves through them at re-evaluation time.
          scopeChain: [...scopes],
        });
      },
    };
  },
};

export default rule;
