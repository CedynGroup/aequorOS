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
/** Callback registrations whose callback the runner executes: a call
 * captured inside one runs; a call captured inside any other function runs
 * only if that function is itself invoked. */
const CALLBACK_NAMES = new Set([
  ...SETUP_HOOKS,
  "afterEach",
  "afterAll",
  "test",
  "it",
  ...DESCRIBE_NAMES,
]);

/** Lifecycle order of a setup-hook phase: every beforeAll runs before every
 * beforeEach, whatever the registration order in the source. */
function hookPhase(hookName) {
  if (hookName === "beforeAll") return 0;
  if (hookName === "beforeEach") return 1;
  return 2;
}

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

function isFunctionNode(node) {
  return (
    node?.type === "FunctionDeclaration" ||
    node?.type === "FunctionExpression" ||
    node?.type === "ArrowFunctionExpression"
  );
}

/**
 * Classify a function body against `chain` with its parameters and body-local
 * declarations bound, without the visitor's scope stack — the module-level
 * counterpart of the create()-closure functionContainsWeakTimerPromise for
 * functions that are not registered declarations (block-local functions).
 */
function classifyFunctionBody(fn, chain, scopeChain, ctx) {
  const bindings = parameterBindings(fn);
  for (const local of bodyDeclaredNames(fn.body)) {
    if (!bindings.has(local)) {
      bindings.set(local, false);
    }
  }
  return weakUseIn(fn.body, [...chain, bindings], false, scopeChain, ctx);
}

/**
 * The binding kind a declaration/assignment binds from its right side,
 * computed against an explicit chain — the module-level counterpart of the
 * create()-closure boundKind for classifications outside the visitor.
 */
function syntheticKind(init, chain, scopeChain, ctx) {
  if (init?.type === "Identifier") {
    const kind = resolveInChain(chain, init.name);
    return kind === WEAK_HELPER || kind === WEAK_PROMISE || kind === WEAK_UNRESOLVED
      ? kind
      : false;
  }
  if (isFunctionNode(init)) {
    return classifyFunctionBody(init, chain, scopeChain, ctx) ? WEAK_HELPER : false;
  }
  return weakUseIn(init, chain, false, scopeChain, ctx) ? WEAK_PROMISE : false;
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
  ctx ??= { visiting: new Set(), memo: new Map(), sawIdentifierWeak: false };

  if (isSetTimeoutPromise(node)) {
    return true;
  }

  if (node.type === "Identifier") {
    const kind = resolveInChain(chain, node.name);
    if (!kind) return false;
    if (kind === WEAK_UNRESOLVED) {
      const weak = !awaitMode && unresolvedHelperIsWeak(node.name, chain, scopeChain, ctx);
      if (weak) {
        // The verdict resolved through a binding's registry entry, which a
        // later alias or reassignment can replace.
        ctx.sawIdentifierWeak = true;
      }
      return weak;
    }
    if (!awaitMode) {
      // Classification mode: any reference to a weak binding contributes —
      // and ties the classification to the binding's CURRENT value, which a
      // setup hook may reassign before the classified helper is called.
      ctx.sawIdentifierWeak = true;
      return true;
    }
    return kind === WEAK_PROMISE;
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
          // Resolved through the callee's registry entry, which a later
          // alias or setup-hook reassignment can replace.
          ctx.sawIdentifierWeak = true;
          return true;
        }
      } else if (kind) {
        // The callee's BINDING carries the weakness, and a setup hook may
        // reassign that binding before the call executes.
        ctx.sawIdentifierWeak = true;
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
            ctx.sawIdentifierWeak = true;
            return true;
          }
        } else if (receiverKind) {
          ctx.sawIdentifierWeak = true;
          return true;
        }
      }
      // `sleep.bind(null, 10)(...)` invokes the bound helper at the outer
      // call: the bound expression carries the helper's timer promise, so a
      // weak binding must survive the bind — like `.call`/`.apply`, any
      // weak receiver kind counts. Binding alone produces a function VALUE:
      // awaiting the bound expression (`await sleep.bind(null, 10)`) awaits
      // that value and waits on nothing, so it stays clean; the bound result
      // is only judged where it is invoked, i.e. where this expression is
      // the callee of the surrounding call.
      if (
        methodName === "bind" &&
        callee.object.type === "Identifier" &&
        node.parent?.type === "CallExpression" &&
        node.parent.callee === node
      ) {
        const receiverKind = resolveInChain(chain, callee.object.name);
        if (receiverKind === WEAK_UNRESOLVED) {
          if (unresolvedHelperIsWeak(callee.object.name, chain, scopeChain, ctx)) {
            ctx.sawIdentifierWeak = true;
            return true;
          }
        } else if (receiverKind) {
          ctx.sawIdentifierWeak = true;
          return true;
        }
      }
      // A timer helper handed to a callback-invoking array method waits
      // exclusively on timer promises.
      if (CALLBACK_INVOKERS.has(methodName) && node.arguments[0]?.type === "Identifier") {
        const callbackKind = resolveInChain(chain, node.arguments[0].name);
        if (callbackKind === WEAK_UNRESOLVED) {
          if (unresolvedHelperIsWeak(node.arguments[0].name, chain, scopeChain, ctx)) {
            ctx.sawIdentifierWeak = true;
            return true;
          }
        } else if (callbackKind) {
          ctx.sawIdentifierWeak = true;
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

  if (node.type === "BlockStatement") {
    // A nested block's lexical declarations shadow outer bindings only
    // INSIDE the block: each declaration's initializer is classified against
    // the outer chain and bound on a synthetic innermost scope for the
    // block's own statements, so a block-local `const sleep = () => fetch()`
    // resolves to itself instead of an outer timer helper — while code after
    // the block still resolves against the outer bindings. `var` stays with
    // the enclosing function scope (already bound there); the block map
    // never leaks past the block.
    const blockBindings = new Map();
    for (const statement of node.body ?? []) {
      const declaration =
        statement.type === "ExportNamedDeclaration" || statement.type === "ExportDefaultDeclaration"
          ? statement.declaration
          : statement;
      if (declaration?.type === "VariableDeclaration" && declaration.kind !== "var") {
        for (const declarator of declaration.declarations ?? []) {
          if (declarator.id?.type === "Identifier") {
            blockBindings.set(
              declarator.id.name,
              syntheticKind(declarator.init, chain, scopeChain, ctx),
            );
          }
        }
      } else if (declaration?.type === "FunctionDeclaration" && declaration.id) {
        blockBindings.set(
          declaration.id.name,
          classifyFunctionBody(declaration, chain, scopeChain, ctx) ? WEAK_HELPER : false,
        );
      }
    }
    const inner = blockBindings.size > 0 ? [...chain, blockBindings] : chain;
    return (node.body ?? []).some((statement) =>
      weakUseIn(statement, inner, awaitMode, scopeChain, ctx),
    );
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
    // Setup-hook writes to shared bindings, in traversal order: each entry
    // records the value the binding held before the write and the describes
    // the hook was registered in, so a pending await from a suite the hook
    // does not cover can be re-evaluated without it.
    const hookWrites = [];

    /** Record a callable declaration in the registry of the current scope,
     * so an explicit invocation of its name resolves to its body. */
    function registerCallable(name, fn) {
      if (!name) {
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
      scope.declarations.set(name, {
        fn,
        slot: scopes.length - 1,
      });
    }

    /** Record a function declaration under its own name. */
    function registerFunctionDeclaration(declaration) {
      registerCallable(declaration.id?.name, declaration);
    }

    /** The registered declaration for `name`, innermost scope first,
     * bounded at `limit` (the replaying helper's defining scope). A scope
     * that binds the name without a callable entry shadows it: lookup stops
     * there instead of passing through to an outer helper. */
    function declaredFunctionEntry(name, limit = scopes.length - 1) {
      for (let index = limit; index >= 0; index -= 1) {
        if (scopes[index].declarations?.has(name)) {
          return scopes[index].declarations.get(name);
        }
        if (scopes[index].bindings.has(name)) {
          return null;
        }
      }
      return null;
    }

    /** The defining scope slot of a registered callable: the scope its
     * declaration was registered in (alias entries share the original
     * entry object, so any match carries the defining slot). */
    function declaredSlotOf(fn) {
      for (let index = scopes.length - 1; index >= 0; index -= 1) {
        for (const entry of scopes[index].declarations?.values() ?? []) {
          if (entry.fn === fn) {
            return entry.slot;
          }
        }
      }
      return scopes.length - 1;
    }

    /**
     * Whether invoking `fn` can reach a captured binding at all, cached per
     * helper: a helper that only touches its own locals and forwards to
     * non-writing helpers is skipped by the replay entirely — without the
     * cache, an acyclic forwarding chain invoked at every level expands
     * exponentially. Cycles contribute nothing new (the guard assumes a
     * computing frame is clean; the finished pass overwrites it).
     */
    const replaySummaries = new Map();
    function helperWritesCaptures(fn) {
      if (replaySummaries.has(fn)) {
        return replaySummaries.get(fn);
      }
      replaySummaries.set(fn, false);
      // Conservative shadow set: only bindings that shadow for the WHOLE
      // body (parameters and top-level declarations) exclude a write. A
      // block-local might expire before a same-named assignment, which the
      // scoped replay walk decides precisely — the summary must not skip it.
      const shadows = new Set();
      const collectNames = (pattern) => {
        if (!pattern) return;
        if (pattern.type === "Identifier") {
          shadows.add(pattern.name);
        } else if (pattern.type === "AssignmentPattern") {
          collectNames(pattern.left);
        } else if (pattern.type === "RestElement") {
          collectNames(pattern.argument);
        } else if (pattern.type === "ArrayPattern") {
          for (const element of pattern.elements ?? []) collectNames(element);
        } else if (pattern.type === "ObjectPattern") {
          for (const property of pattern.properties ?? []) {
            collectNames(property.type === "RestElement" ? property.argument : property.value);
          }
        }
      };
      for (const parameter of fn.params ?? []) {
        collectNames(parameter);
      }
      // The helper's own function declarations (outside nested functions):
      // a callee resolved here must be looked up among them first — the
      // caller's scope stack cannot see the helper's locals.
      const localCallables = new Map();
      const collectCallables = (node) => {
        if (!node || typeof node !== "object" || typeof node.type !== "string") {
          return;
        }
        if (node.type === "FunctionDeclaration" && node.id) {
          localCallables.set(node.id.name, node);
        }
        if (
          node.type === "VariableDeclarator" &&
          node.id.type === "Identifier" &&
          (node.init?.type === "FunctionExpression" ||
            node.init?.type === "ArrowFunctionExpression")
        ) {
          // A variable-declared callable is invocable by name like a
          // function declaration.
          localCallables.set(node.id.name, node.init);
        }
        if (
          node.type === "VariableDeclarator" &&
          node.id.type === "Identifier" &&
          node.init?.type === "Identifier"
        ) {
          // An alias of an already-collected callable is invocable through
          // its own name (declarations register in source order).
          const source = localCallables.get(node.init.name);
          if (source) {
            localCallables.set(node.id.name, source);
          }
        }
        for (const [key, value] of Object.entries(node)) {
          if (key === "parent") {
            continue;
          }
          if (Array.isArray(value)) {
            for (const child of value) {
              if (child && typeof child === "object" && typeof child.type === "string") {
                if (isFunctionNode(child)) {
                  // A nested function declaration's NAME binds in this
                  // helper's scope (its body's declarations do not).
                  if (child.type === "FunctionDeclaration" && child.id) {
                    localCallables.set(child.id.name, child);
                  }
                  continue;
                }
                collectCallables(child);
              }
            }
          } else if (value && typeof value === "object" && typeof value.type === "string") {
            if (isFunctionNode(value)) {
              if (value.type === "FunctionDeclaration" && value.id) {
                localCallables.set(value.id.name, value);
              }
              continue;
            }
            collectCallables(value);
          }
        }
      };
      collectCallables(fn.body);
      let result = false;
      const walk = (node) => {
        if (result || !node || typeof node !== "object" || typeof node.type !== "string") {
          return;
        }
        if (
          node.type === "AssignmentExpression" &&
          node.operator === "=" &&
          node.left.type === "Identifier" &&
          !shadows.has(node.left.name)
        ) {
          result = true;
          return;
        }
        if (node.type === "CallExpression" && node.callee.type === "Identifier") {
          const localFn = localCallables.get(node.callee.name);
          if (localFn) {
            if (helperWritesCaptures(localFn)) {
              result = true;
            }
          } else {
            const entry = declaredFunctionEntry(node.callee.name);
            if (entry?.fn && helperWritesCaptures(entry.fn)) {
              result = true;
            }
          }
          // Argument expressions execute before the call: an assignment
          // inside an argument is part of this function's writes.
          for (const argument of node.arguments ?? []) {
            if (argument && typeof argument === "object" && typeof argument.type === "string" && !isFunctionNode(argument)) {
              walk(argument);
            }
          }
          return;
        }
        for (const [key, value] of Object.entries(node)) {
          if (key === "parent") {
            continue;
          }
          if (Array.isArray(value)) {
            for (const child of value) {
              if (
                child &&
                typeof child === "object" &&
                typeof child.type === "string" &&
                !isFunctionNode(child)
              ) {
                walk(child);
              }
            }
          } else if (
            value &&
            typeof value === "object" &&
            typeof value.type === "string" &&
            !isFunctionNode(value)
          ) {
            walk(value);
          }
        }
      };
      walk(fn.body);
      replaySummaries.set(fn, result);
      return result;
    }

    /**
     * A named helper invoked explicitly (a statically resolvable call) runs
     * its body where the call sits, so its captured assignments reach the
     * enclosing scopes exactly like an IIFE's writes do — the body traversal
     * alone confined them to the helper's temporary scope. Re-apply the
     * body's assignments at the call site, recursing into helper invocations
     * it makes and guarding against recursion cycles. Genuinely uncalled
     * functions stay isolated: without a call their writes are never
     * replayed. `executionSite` is the invocation's position: a write the
     * helper performs logs at where the CALL runs, not where the helper's
     * source sits (a reset invoked after an await in the same hook has not
     * happened when that await runs).
     */
    function replayInvocationWrites(fn, active, executionSite, inherited) {
      if (active.has(fn)) {
        return;
      }
      if (!helperWritesCaptures(fn)) {
        return;
      }
      active.add(fn);
      // The helper's writes resolve against ITS defining scope chain (the
      // registry slot the invocation was resolved through): a caller's
      // shadowing local must never capture them, while the helper's captured
      // bindings update exactly where its body sees them.
      const limit = declaredSlotOf(fn);
      // Lexically scoped local bindings with their classified kinds: a
      // write to a name bound in an open scope is the helper's own local
      // state (a block-local expires with its block), and a local referenced
      // on a right-hand side carries that local's kind instead of resolving
      // against the defining scopes, where it does not exist. A nested
      // helper invoked during the replay inherits the invoking replay's
      // scopes, so its body reads the enclosing initializer's locals.
      const localScopes = inherited ? inherited.scopes : [];
      const replayScopeKinds = inherited ? inherited.kinds : [];
      const replayEnvironments = inherited?.environments ?? new Map();
      const scopeDepth = localScopes.length;
      const resolvingLocals = new Set();
      /** The lexical environment a helper captures at its DEFINITION: its
       * body must resolve captures through the scopes open where it was
       * declared, never through a caller's shadowing scopes at the
       * invocation site. */
      const captureEnvironment = () => ({
        scopes: localScopes.slice(),
        kinds: replayScopeKinds.slice(),
        environments: replayEnvironments,
      });
      const lookupLocal = (name) => {
        for (let index = localScopes.length - 1; index >= 0; index -= 1) {
          if (localScopes[index].has(name)) {
            return index;
          }
        }
        return -1;
      };
      const classifyLocal = (def) => {
        if (def.kind !== undefined) {
          return def.kind;
        }
        if (resolvingLocals.has(def)) {
          // A recursive local chain: treat as unresolved until the pass ends.
          return WEAK_UNRESOLVED;
        }
        resolvingLocals.add(def);
        try {
          if (def.fn) {
            def.kind = functionContainsWeakTimerPromise(def.fn) ? WEAK_HELPER : false;
          } else if (!def.init) {
            def.kind = false;
          } else if (def.init.type === "Identifier") {
            const index = lookupLocal(def.init.name);
            if (index >= 0) {
              const source = localScopes[index].get(def.init.name);
              def.kind = classifyLocal(source);
              // An alias carries the callable identity of its source, so an
              // invocation through the alias replays the same writes.
              if (def.fn === undefined) {
                if (source.fn !== undefined) {
                  def.fn = source.fn;
                } else if (source.init && isFunctionNode(source.init)) {
                  def.fn = source.init;
                }
              }
            } else {
              def.kind = boundKind(def.init, limit);
            }
          } else {
            def.kind =
              isFunctionNode(def.init) && localWeakCallInside(def.init)
                ? WEAK_HELPER
                : boundKind(def.init, limit);
          }
        } finally {
          resolvingLocals.delete(def);
        }
        return def.kind;
      };
      /** The replay-scope index of the nearest enclosing function body, the
       * scope a `var` binding belongs to. */
      const varReplayScopeIndex = () => {
        for (let index = replayScopeKinds.length - 1; index >= 0; index -= 1) {
          if (replayScopeKinds[index] === "function") {
            return index;
          }
        }
        return replayScopeKinds.length;
      };
      /** Whether a function body calls a replay-local whose kind is a weak
       * helper: a closure assigned inside the helper captures the local's
       * timer dependency, which boundKind (registry-only) cannot see. */
      const localWeakCallInside = (fnNode) => {
        let weak = false;
        const scan = (node) => {
          if (weak || !node || typeof node !== "object" || typeof node.type !== "string") {
            return;
          }
          if (node !== fnNode && isFunctionNode(node)) {
            return;
          }
          if (node.type === "CallExpression" && node.callee.type === "Identifier") {
            const index = lookupLocal(node.callee.name);
            if (index >= 0) {
              const source = localScopes[index].get(node.callee.name);
              if (source && classifyLocal(source) === WEAK_HELPER) {
                weak = true;
                return;
              }
            }
          }
          for (const [key, value] of Object.entries(node)) {
            if (key === "parent") {
              continue;
            }
            if (Array.isArray(value)) {
              for (const child of value) {
                scan(child);
              }
            } else if (value && typeof value === "object" && typeof value.type === "string") {
              scan(value);
            }
          }
        };
        scan(fnNode.body ?? fnNode);
        return weak;
      };
      const pushLocalScope = (statements, kind = "block") => {
        const map = new Map();
        // A `var` binding outlives any intervening block: it registers in
        // the nearest function-body scope (this scope, when the scope being
        // pushed is one) while a let/const binding registers here.
        const varTargetIndex = kind === "function" ? replayScopeKinds.length : varReplayScopeIndex();
        const varTarget = localScopes[varTargetIndex] ?? map;
        replayScopeKinds.push(kind);
        localScopes.push(map);
        for (const statement of statements ?? []) {
          if (statement.type === "VariableDeclaration") {
            const target = statement.kind === "var" ? varTarget : map;
            for (const declarator of statement.declarations) {
              if (declarator.id.type === "Identifier") {
                target.set(declarator.id.name, { init: declarator.init, kind: undefined });
              }
            }
          } else if (statement.type === "FunctionDeclaration" && statement.id) {
            map.set(statement.id.name, { fn: statement, kind: undefined });
            // A hoisted helper may be invoked before its textual declaration:
            // record its DEFINITION-time environment at scope entry.
            replayEnvironments.set(statement, captureEnvironment());
          }
        }
        // Local NAMES register at scope entry, but their initializers
        // classify when the replay REACHES each declaration: a copy taken
        // after an intervening assignment must see the assigned value, and
        // a copy taken before it must not.
      };
      pushLocalScope(fn.body?.type === "BlockStatement" ? fn.body.body : [], "function");
      const params = new Map();
      const declarePattern = (pattern) => {
        if (!pattern) return;
        if (pattern.type === "Identifier") {
          params.set(pattern.name, { init: null, kind: false });
        } else if (pattern.type === "AssignmentPattern") {
          declarePattern(pattern.left);
        } else if (pattern.type === "RestElement") {
          declarePattern(pattern.argument);
        } else if (pattern.type === "ArrayPattern") {
          for (const element of pattern.elements ?? []) declarePattern(element);
        } else if (pattern.type === "ObjectPattern") {
          for (const property of pattern.properties ?? []) {
            declarePattern(property.type === "RestElement" ? property.argument : property.value);
          }
        }
      };
      for (const parameter of fn.params ?? []) {
        declarePattern(parameter);
      }
      localScopes.push(params);
      replayScopeKinds.push("function");
      const walk = (node) => {
        if (!node || typeof node !== "object" || typeof node.type !== "string") {
          return;
        }
        if (isFunctionNode(node)) {
          // The helper's lexical environment is fixed where it is declared.
          replayEnvironments.set(node, captureEnvironment());
          return;
        }
        if (node.type === "BlockStatement") {
          pushLocalScope(node.body);
          // An unconditional return/throw ends the sequence: statements
          // after it never execute, so the replay must not apply them.
          let terminated = false;
          for (const statement of node.body) {
            if (terminated) {
              break;
            }
            walk(statement);
            if (statement.type === "ReturnStatement" || statement.type === "ThrowStatement") {
              terminated = true;
            }
          }
          localScopes.pop();
          replayScopeKinds.pop();
          return;
        }
        if (node.type === "VariableDeclarator" && node.id.type === "Identifier") {
          if (node.init && isFunctionNode(node.init)) {
            replayEnvironments.set(node.init, captureEnvironment());
          }
          // The declaration executes here: classify its initializer now, so
          // the local holds the value it has at this point in the replay.
          const index = lookupLocal(node.id.name);
          if (index >= 0) {
            const def = localScopes[index].get(node.id.name);
            if (def && def.init === node.init) {
              classifyLocal(def);
            }
          }
        }
        if (
          node.type === "AssignmentExpression" &&
          node.operator === "=" &&
          node.left.type === "Identifier"
        ) {
          if (isFunctionNode(node.right)) {
            replayEnvironments.set(node.right, captureEnvironment());
          }
          // A write to a name bound in an open local scope stays inside the
          // helper: update that local's value (a later read of the local
          // must classify the NEW initializer) without propagating the
          // write to the caller's scopes.
          const localIndex = lookupLocal(node.left.name);
          if (localIndex >= 0) {
            const def = localScopes[localIndex].get(node.left.name);
            if (def) {
              def.init = node.right;
              def.kind = undefined;
              // The cached callable identity belongs to the replaced value:
              // reclassification must not keep resolving the old callable.
              def.fn = undefined;
              // Retain the assigned value at this execution position: a
              // later read must not re-resolve the alias through the
              // source's subsequent replacement.
              classifyLocal(def);
            }
          } else {
            let kind;
            if (node.right.type === "Identifier") {
              const localKind = lookupLocal(node.right.name) >= 0
                ? classifyLocal(localScopes[lookupLocal(node.right.name)].get(node.right.name))
                : undefined;
              kind = localKind !== undefined ? localKind : boundKind(node.right, limit);
            } else {
              // An assigned closure may call a replay-local whose kind is a
              // weak helper — a dependency boundKind (registry-only) cannot
              // see.
              kind =
                isFunctionNode(node.right) && localWeakCallInside(node.right)
                  ? WEAK_HELPER
                  : boundKind(node.right, limit);
            }
            const { target, logged } = assignIdentifier(node.left, kind, limit, executionSite);
            if (kind === WEAK_UNRESOLVED && node.right.type === "Identifier") {
              const aliasTarget = unresolvedAliasTarget(node.right.name, limit);
              if (aliasTarget && target) {
                if (logged) {
                  logged.declaration = {
                    scope: target,
                    name: node.left.name,
                    entry: aliasTarget,
                    had: target.declarations?.has(node.left.name) ?? false,
                    previous: target.declarations?.get(node.left.name),
                  };
                }
                if (!target.declarations) {
                  target.declarations = new Map();
                }
                target.declarations.set(node.left.name, aliasTarget);
                // The registry changed: cached captured-write summaries that
                // resolved through the old entries are stale.
                replaySummaries.clear();
              }
            } else if (target) {
              // A reassignment replaces the callable the binding holds: a
              // later invocation must replay the NEW value's body, and a
              // non-callable value leaves nothing invocable behind.
              if (isFunctionNode(node.right)) {
                if (logged) {
                  // Record the registry state this write replaces, so the
                  // flush replay restores the sibling suite's entry exactly
                  // like an unresolved-alias write does.
                  logged.declaration = {
                    scope: target,
                    name: node.left.name,
                    entry: { fn: node.right, slot: scopes.indexOf(target) },
                    had: target.declarations?.has(node.left.name) ?? false,
                    previous: target.declarations?.get(node.left.name),
                  };
                }
                if (!target.declarations) {
                  target.declarations = new Map();
                }
                target.declarations.set(node.left.name, {
                  fn: node.right,
                  slot: scopes.indexOf(target),
                });
              } else {
                if (logged) {
                  logged.declaration = {
                    scope: target,
                    name: node.left.name,
                    entry: undefined,
                    had: target.declarations?.has(node.left.name) ?? false,
                    previous: target.declarations?.get(node.left.name),
                  };
                }
                target.declarations?.delete(node.left.name);
              }
              // The registry changed: cached captured-write summaries that
              // resolved through the old entries are stale.
              replaySummaries.clear();
            }
          }
          return;
        }
        if (node.type === "CallExpression" && node.callee.type === "Identifier") {
          // A callee declared inside the replaying helper resolves through
          // the replay's local scopes first: the caller's scope stack
          // cannot see the helper's own function declarations.
          const localIndex = lookupLocal(node.callee.name);
          const localDef = localIndex >= 0 ? localScopes[localIndex].get(node.callee.name) : null;
          const localFn = localDef?.fn ?? (localDef?.init && isFunctionNode(localDef.init) ? localDef.init : null);
          if (localFn) {
            replayInvocationWrites(
              localFn,
              active,
              executionSite,
              // The helper resolves captures through its DEFINITION-time
              // environment; a caller's shadowing scopes must not leak in.
              replayEnvironments.get(localFn),
            );
          } else {
            const entry = declaredFunctionEntry(node.callee.name, limit);
            if (entry?.fn) {
              replayInvocationWrites(entry.fn, active, executionSite, replayEnvironments.get(entry.fn));
            }
          }
          // Argument expressions execute before the call: replay their
          // assignments too (an argument assignment is part of this
          // function's writes).
          for (const argument of node.arguments ?? []) {
            if (argument && typeof argument === "object" && typeof argument.type === "string" && !isFunctionNode(argument)) {
              walk(argument);
            }
          }
          return;
        }
        for (const [key, value] of Object.entries(node)) {
          if (key === "parent") {
            continue;
          }
          if (Array.isArray(value)) {
            for (const child of value) {
              if (
                child &&
                typeof child === "object" &&
                typeof child.type === "string" &&
                !isFunctionNode(child)
              ) {
                walk(child);
              }
            }
          } else if (
            value &&
            typeof value === "object" &&
            typeof value.type === "string" &&
            !isFunctionNode(value)
          ) {
            walk(value);
          }
        }
      };
      try {
        const body = fn.body;
        // An unconditional return/throw ends the body's sequence: later
        // statements never execute and must not replay.
        let terminated = false;
        for (const statement of body?.type === "BlockStatement" ? body.body : []) {
          if (terminated) {
            break;
          }
          walk(statement);
          if (statement.type === "ReturnStatement" || statement.type === "ThrowStatement") {
            terminated = true;
          }
        }
        if (body && body.type !== "BlockStatement") {
          walk(body);
        }
      } finally {
        active.delete(fn);
        // A nested invocation inherits the caller's live scope stack: drop
        // exactly the scopes this replay pushed so the caller's environment
        // is intact when its walk continues.
        localScopes.length = scopeDepth;
        replayScopeKinds.length = scopeDepth;
      }
    }

    /** The declaration an unresolved alias forwards to, if one is visible,
     * bounded at `limit` (the replaying helper's defining scope). */
    function unresolvedAliasTarget(name, limit = scopes.length - 1) {
      for (let index = limit; index >= 0; index -= 1) {
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

    function resolveKind(name, limit = scopes.length - 1) {
      return resolveInChain(currentChain().slice(0, limit + 1), name);
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
     * otherwise confined to the current function. `limit` bounds resolution
     * at the replaying helper's defining scope: the helper's writes update
     * the bindings ITS body sees, never a caller's shadowing local.
     */
    function writeTargetScope(name, limit = scopes.length - 1) {
      let boundary = limit;
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
        for (let index = limit; index >= 0; index -= 1) {
          if (scopes[index].bindings.has(name)) {
            return scopes[index];
          }
        }
      }
      for (let index = limit; index >= boundary; index -= 1) {
        if (scopes[index].bindings.has(name)) {
          return scopes[index];
        }
      }
      // A captured outer binding reassigned inside this function: the
      // function-local override is where the awaits that follow it look.
      return boundary >= 0 ? scopes[boundary] : null;
    }

    function assignIdentifier(
      node,
      kind,
      limit = scopes.length - 1,
      site = node.range?.[0] ?? 0,
    ) {
      if (node?.type !== "Identifier") {
        return null;
      }

      // Confine write effects to the function whose body the assignment sits
      // in: an assignment inside an uncalled function must not change the
      // classification the enclosing scopes already have. A setup hook is
      // the exception — its callback runs before the tests, so its
      // assignment to an outer binding propagates outward.
      const target = writeTargetScope(node.name, limit);
      if (!target) {
        return null;
      }
      // A hook's write to a shared binding applies only to the suites the
      // hook covers: log it so a pending await from another suite can be
      // re-evaluated without it.
      const hookScope = [...scopes].reverse().find((scope) => scope.type === "hook");
      let logged = null;
      if (hookScope && !isOrContains(hookScope, target)) {
        logged = {
          scope: target,
          name: node.name,
          kind,
          had: target.bindings.has(node.name),
          previous: target.bindings.get(node.name),
          describeChain: hookScope.describeChain ?? [],
          phase: hookScope.hookName ?? "beforeEach",
          order: hookScope.hookOrder ?? 0,
          // Where the write EXECUTES: the assignment's own position for a
          // direct write, or the invocation's position when the replay runs
          // a helper's body (a reset invoked after an await in the same hook
          // has not happened when that await runs).
          site,
          // The declaration-registry entry this write installs (an aliased
          // unresolved helper), recorded by the assignment handler so the
          // replay can restore it together with the binding value.
          declaration: null,
        };
        hookWrites.push(logged);
      }
      target.bindings.set(node.name, kind);
      return { target, logged };
    }

    /** Whether `scope` is `outer` or sits above it on the scope stack: a
     * write landing there stays inside the hook's own scopes. */
    function isOrContains(outer, scope) {
      const outerIndex = scopes.indexOf(outer);
      const scopeIndex = scopes.indexOf(scope);
      return scopeIndex >= outerIndex;
    }

    /** The kind an alias inherits: helpers alias to helpers, promises to
     * promises, and an unresolved helper stays unresolved through aliases so
     * its dependency is resolved when it becomes known. `limit` bounds
     * resolution at the replaying helper's defining scope. */
    function aliasedKind(init, limit = scopes.length - 1) {
      if (init?.type !== "Identifier") return null;
      const kind = resolveKind(init.name, limit);
      return kind === WEAK_HELPER || kind === WEAK_PROMISE || kind === WEAK_UNRESOLVED
        ? kind
        : null;
    }

    /** Classify the kind a declarator/assignment binds, from its right side.
     * `limit` bounds resolution at the replaying helper's defining scope. */
    function boundKind(right, limit = scopes.length - 1) {
      const alias = aliasedKind(right, limit);
      if (alias) return alias;
      if (isFunctionNode(right)) {
        return functionContainsWeakTimerPromise(right) ? WEAK_HELPER : false;
      }
      return weakUseIn(right, currentChain().slice(0, limit + 1), false, scopes.slice(0, limit + 1))
        ? WEAK_PROMISE
        : false;
    }

    /**
     * Pending awaits are only judged once the whole file has been visited:
     * a setup hook registered after a test may assign a timer helper and
     * then replace it with a real operation, and the test only ever runs
     * with the final assignment. Judging earlier would report on state the
     * test never observes — and an await that already looked weak at its
     * visit is judged here too, because a later-registered hook may still
     * reassign the helper it waits on with a clean one.
     */
    function flushPendingAwaits() {
      // Replay hook writes in lifecycle order, not registration order: all
      // beforeAll hooks execute before all beforeEach hooks, and outer
      // describes' hooks run before inner ones within a phase. Same-phase,
      // same-suite writes keep their registration order (the traversal
      // index breaks ties). The seeding pass below still reads the log in
      // traversal order: the first write to a binding records the value the
      // binding held before any hook touched it.
      const orderedWrites = hookWrites
        .map((entry, index) => ({ entry, index }))
        .sort(
          (a, b) =>
            hookPhase(a.entry.phase) - hookPhase(b.entry.phase) ||
            a.entry.describeChain.length - b.entry.describeChain.length ||
            a.index - b.index,
        )
        .map(({ entry }) => entry);
      for (const pending of pendingAwaits) {
        // The pending's own function scopes are evaluated with the binding
        // values as they stood at the await: reassignments that happened
        // after it in the same function must not reclassify it, while
        // enclosing scopes stay live so later helper declarations and hook
        // assignments still reach it. The scope objects are live references,
        // so a helper registered later is still resolvable; only the binding
        // VALUES are frozen per scope through `own`.
        //
        // A hook write applies only to the suites the hook covers: roll back
        // the writes whose describes are not a prefix of this pending's
        // describes (a sibling suite's beforeEach never ran for it), judge
        // the await, then restore them for the next pending.
        // Reconstruct the value each logged binding holds for THIS pending's
        // suite: start from the pre-log value the log's first write recorded
        // and apply only the writes whose describes are a prefix of the
        // pending's describes, in order (a sibling suite's beforeEach never
        // ran for it). Judge the await, then restore the live state exactly
        // — restoring historical values per write instead would erase a
        // later applicable write that shares the binding.
        const targets = new Map();
        const snapshot = new Map();
        for (const entry of hookWrites) {
          if (!targets.has(entry.scope)) {
            targets.set(entry.scope, new Map());
            snapshot.set(entry.scope, new Map());
          }
          const targetValues = targets.get(entry.scope);
          const snapshotted = snapshot.get(entry.scope);
          if (!targetValues.has(entry.name)) {
            targetValues.set(entry.name, entry.had ? entry.previous : undefined);
            snapshotted.set(entry.name, entry.scope.bindings.get(entry.name));
          }
        }
        // Declaration-registry entries replay alongside the binding values:
        // a sibling hook's alias registration must not leak into this
        // pending's suite, and this suite's own alias must be installed.
        const declarations = new Map();
        const declarationSnapshots = new Map();
        for (const entry of hookWrites) {
          if (!entry.declaration) continue;
          const { scope, name } = entry.declaration;
          if (!declarations.has(scope)) {
            declarations.set(scope, new Map());
            declarationSnapshots.set(scope, new Map());
          }
          const entries = declarations.get(scope);
          if (!entries.has(name)) {
            entries.set(name, entry.declaration.had ? entry.declaration.previous : undefined);
            declarationSnapshots
              .get(scope)
              .set(name, scope.declarations?.get(name) ?? undefined);
          }
        }
        // Hook writes execute in lifecycle order — every beforeAll before
        // every beforeEach, outer describes' hooks before inner ones within
        // a phase, registration order within a suite. A write applies to a
        // pending only when it runs before the pending's own position: for
        // a pending inside a setup hook, later-executing writes (the
        // finding's beforeEach write against a beforeAll's await) have not
        // happened yet.
        const writeExecutesBeforePending = (entry) => {
          if (!pending.hookKey) {
            return true;
          }
          const phase = hookPhase(entry.phase);
          if (phase !== pending.hookKey.phase) {
            return phase < pending.hookKey.phase;
          }
          if (entry.describeChain.length !== pending.hookKey.depth) {
            return entry.describeChain.length < pending.hookKey.depth;
          }
          if (entry.order !== pending.hookKey.order) {
            return entry.order < pending.hookKey.order;
          }
          // Same hook: only the positions inside the callback tell whether
          // the write had happened before the await — a reassignment after
          // the await must not reach its verdict.
          return entry.site < pending.hookKey.awaitStart;
        };
        for (const entry of orderedWrites) {
          const applies =
            entry.describeChain.length <= pending.describeChain.length &&
            entry.describeChain.every(
              (describe, position) => pending.describeChain[position] === describe,
            ) &&
            writeExecutesBeforePending(entry);
          if (applies) {
            targets.get(entry.scope).set(entry.name, entry.kind);
            if (entry.declaration) {
              declarations
                .get(entry.declaration.scope)
                .set(entry.declaration.name, entry.declaration.entry);
            }
          }
        }
        for (const [scope, values] of targets) {
          for (const [name, value] of values) {
            if (value === undefined) {
              scope.bindings.delete(name);
            } else {
              scope.bindings.set(name, value);
            }
          }
        }
        for (const [scope, entries] of declarations) {
          if (!scope.declarations) {
            scope.declarations = new Map();
          }
          for (const [name, value] of entries) {
            if (value === undefined) {
              scope.declarations.delete(name);
            } else {
              scope.declarations.set(name, value);
            }
          }
        }
        const evaluationChain = [...pending.chain.slice(0, pending.depth), ...pending.own];
        // The pending's own scopes resolve declarations through frozen
        // registries (ownDeclarations): an alias reassigned after the await
        // in the same callback must not re-classify it. Enclosing scopes
        // stay live so forward declarations still reach the pending.
        const evaluationScopeChain = [
          ...pending.scopeChain.slice(0, pending.depth),
          ...(pending.ownDeclarations ?? pending.scopeChain.slice(pending.depth)).map(
            (declarations) => ({ declarations }),
          ),
        ];
        const weak = weakUseIn(pending.argument, evaluationChain, true, evaluationScopeChain);
        for (const [scope, values] of snapshot) {
          for (const [name, value] of values) {
            if (value === undefined) {
              scope.bindings.delete(name);
            } else {
              scope.bindings.set(name, value);
            }
          }
        }
        for (const [scope, entries] of declarationSnapshots) {
          for (const [name, value] of entries) {
            if (value === undefined) {
              scope.declarations?.delete(name);
            } else {
              if (!scope.declarations) {
                scope.declarations = new Map();
              }
              scope.declarations.set(name, value);
            }
          }
        }
        if (weak) {
          context.report({ node: pending.node, messageId: "weakAwait" });
        }
      }
      pendingAwaits.length = 0;
      hookWrites.length = 0;
    }

    // Classify a function's own body with its parameters already bound, so a
    // parameter that shadows an outer weak-timer helper is not mistaken for
    // the helper it shadows. The body's own declared names are bound too: a
    // local `sleep` must not resolve to a module-level timer helper of the
    // same name. (Functions nested inside the scanned body are still
    // classified by plain recursion; their params are not bound.)
    // `classification` collects whether the weakness flowed through binding
    // resolutions — a helper whose weakness depends on the CURRENT value of
    // a rebindable binding stays unresolved so each read site re-classifies
    // it against the bindings standing there.
    function functionContainsWeakTimerPromise(fn, classification = null) {
      pushScope("function");
      declareParameters(fn);
      const own = scopes[scopes.length - 1];
      for (const local of bodyDeclaredNames(fn.body)) {
        if (!own.bindings.has(local)) {
          own.bindings.set(local, false);
        }
      }
      const contains = weakUseIn(fn.body, currentChain(), false, [...scopes], classification);
      popScope();
      return contains;
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
          if (resolveKind(declaration.id.name) !== WEAK_HELPER) {
            const classification = {
              visiting: new Set(),
              memo: new Map(),
              sawIdentifierWeak: false,
            };
            if (functionContainsWeakTimerPromise(declaration, classification)) {
              // A weakness that flowed through binding resolutions depends
              // on the bindings' current values: keep it unresolved so each
              // read site re-classifies against the bindings standing there
              // (a setup hook may have reassigned the dependency). Only an
              // intrinsic weakness — a setTimeout promise built inside the
              // body — is cached as a helper.
              declareIdentifier(
                declaration.id,
                classification.sawIdentifierWeak ? WEAK_UNRESOLVED : WEAK_HELPER,
                "function",
              );
              changed = true;
            }
          }
        }
        if (!changed) {
          break;
        }
      }
    }

    /** The describe calls enclosing `node`, outermost first: a hook
     * registered in those describes runs for the tests they contain. */
    function describeChainOf(node) {
      const chain = [];
      let current = node;
      while (current) {
        if (
          (current.type === "FunctionExpression" || current.type === "ArrowFunctionExpression") &&
          current.parent?.type === "CallExpression"
        ) {
          const callee = current.parent.callee;
          const baseName =
            callee?.type === "Identifier"
              ? callee.name
              : callee?.type === "MemberExpression" && !callee.computed && callee.property?.type === "Identifier"
                ? callee.property.name
                : null;
          if (baseName !== null && DESCRIBE_NAMES.has(baseName)) {
            chain.push(current.parent);
          }
        }
        current = current.parent;
      }
      return chain.reverse();
    }

    /** The setup-hook name a function's parent call names, or null. */
    function hookNameFor(node) {
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
      return hookName !== null && SETUP_HOOKS.has(hookName) ? hookName : null;
    }

    /** The scope type a function pushes: a setup hook's callback or a plain function. */
    function scopeTypeFor(node) {
      return hookNameFor(node) !== null ? "hook" : "function";
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
      CallExpression(node) {
        // An explicitly invoked named helper runs its body here: replay its
        // captured assignments against the call site's scopes, so a helper
        // that initializes shared bindings before the tests is not treated
        // as uncalled. Writes the helper performs log at the invocation's
        // position, not the helper body's.
        if (node.callee.type === "Identifier") {
          // Only a call that executes IN PLACE replays here: a call captured
          // inside a function nothing invokes stays isolated, and its writes
          // propagate when a replay reaches that function's body.
          let boundary = null;
          for (let probe = node.parent; probe; probe = probe.parent) {
            if (isFunctionNode(probe)) {
              boundary = probe;
              break;
            }
          }
          while (boundary) {
            const parent = boundary.parent;
            if (parent?.type === "CallExpression" && parent.callee === boundary) {
              boundary = null;
              for (let probe = parent.parent; probe; probe = probe.parent) {
                if (isFunctionNode(probe)) {
                  boundary = probe;
                  break;
                }
              }
              continue;
            }
            break;
          }
          if (boundary) {
            const call = boundary.parent;
            const base =
              call?.type === "CallExpression"
                ? call.callee.type === "Identifier"
                  ? call.callee.name
                  : call.callee.type === "MemberExpression" && !call.callee.computed
                    ? call.callee.property?.type === "Identifier"
                      ? call.callee.property.name
                      : null
                    : null
                : null;
            if (base === null || !CALLBACK_NAMES.has(base)) {
              return;
            }
          }
          const entry = declaredFunctionEntry(node.callee.name);
          if (entry?.fn) {
            replayInvocationWrites(entry.fn, new Set(), node.range?.[0] ?? 0);
          }
        }
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
        // inner scope never shadows an outer one of the same name. A weakness
        // that flowed through binding resolutions also stays unresolved: it
        // depends on the bindings' current values, which a setup hook may
        // reassign before the helper is called.
        registerFunctionDeclaration(node);
        const classification = {
          visiting: new Set(),
          memo: new Map(),
          sawIdentifierWeak: false,
        };
        const weak = functionContainsWeakTimerPromise(node, classification);
        declareIdentifier(
          node.id,
          weak && !classification.sawIdentifierWeak ? WEAK_HELPER : WEAK_UNRESOLVED,
          "function",
        );
        pushScope(scopeTypeFor(node));
        declareParameters(node);
      },
      "FunctionDeclaration:exit": popScope,
      FunctionExpression(node) {
        pushScope(scopeTypeFor(node));
        const hookName = hookNameFor(node);
        if (hookName !== null) {
          scopes[scopes.length - 1].hookName = hookName;
          scopes[scopes.length - 1].hookOrder = node.range?.[0] ?? 0;
          scopes[scopes.length - 1].describeChain = describeChainOf(node);
        }
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
        const hookName = hookNameFor(node);
        if (hookName !== null) {
          scopes[scopes.length - 1].hookName = hookName;
          scopes[scopes.length - 1].hookOrder = node.range?.[0] ?? 0;
          scopes[scopes.length - 1].describeChain = describeChainOf(node);
        }
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
        // A variable-declared callable (`const init = () => {...}`) is
        // invocable by name exactly like a function declaration: register
        // it so an explicit call replays its captured writes.
        if (node.id.type === "Identifier") {
          if (
            node.init?.type === "FunctionExpression" ||
            node.init?.type === "ArrowFunctionExpression"
          ) {
            registerCallable(node.id.name, node.init);
          } else if (node.init?.type === "Identifier") {
            // An alias of a known callable is invocable through its own
            // name: register the same function so an explicit call of the
            // alias replays the same captured writes.
            const entry = declaredFunctionEntry(node.init.name);
            if (entry?.fn) {
              registerCallable(node.id.name, entry.fn);
            }
          }
        }
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
            const { target, logged } = assignIdentifier(node.left, kind);
            if (kind === WEAK_UNRESOLVED && node.right.type === "Identifier") {
              const aliasTarget = unresolvedAliasTarget(node.right.name);
              if (aliasTarget && target) {
                if (logged) {
                  // Record the registry state this write replaces, so the
                  // flush replay can restore the sibling suite's entry —
                  // replaying the binding value alone would leave the last
                  // sibling's declaration visible to every suite.
                  logged.declaration = {
                    scope: target,
                    name: node.left.name,
                    entry: aliasTarget,
                    had: target.declarations?.has(node.left.name) ?? false,
                    previous: target.declarations?.get(node.left.name),
                  };
                }
                if (!target.declarations) {
                  target.declarations = new Map();
                }
                target.declarations.set(node.left.name, aliasTarget);
                // The registry changed: cached captured-write summaries that
                // resolved through the old entries are stale.
                replaySummaries.clear();
              }
            } else if (target) {
              // A reassignment replaces the callable the binding holds: a
              // later invocation must replay the NEW value's body, and a
              // non-callable value leaves nothing invocable behind.
              if (isFunctionNode(node.right)) {
                if (logged) {
                  // Record the registry state this write replaces, so the
                  // flush replay can restore the sibling suite's entry —
                  // replaying the binding value alone would leave the last
                  // sibling's declaration visible to every suite.
                  logged.declaration = {
                    scope: target,
                    name: node.left.name,
                    entry: { fn: node.right, slot: scopes.indexOf(target) },
                    had: target.declarations?.has(node.left.name) ?? false,
                    previous: target.declarations?.get(node.left.name),
                  };
                }
                if (!target.declarations) {
                  target.declarations = new Map();
                }
                target.declarations.set(node.left.name, {
                  fn: node.right,
                  slot: scopes.indexOf(target),
                });
              } else {
                if (logged) {
                  logged.declaration = {
                    scope: target,
                    name: node.left.name,
                    entry: undefined,
                    had: target.declarations?.has(node.left.name) ?? false,
                    previous: target.declarations?.get(node.left.name),
                  };
                }
                target.declarations?.delete(node.left.name);
              }
              // The registry changed: cached captured-write summaries that
              // resolved through the old entries are stale.
              replaySummaries.clear();
            }
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

        // Every non-bare await is judged once the whole file has been
        // visited (flushPendingAwaits): a setup hook registered after this
        // await may still reassign the helper it waits on with a clean one,
        // and the test only ever runs with the final assignment. Reporting
        // at the visit would fire on state the test never observes. The
        // pending's frozen function-local values keep later same-function
        // reassignments from changing an earlier await's verdict. For an
        // await inside a setup hook, the hook's own lifecycle key is kept:
        // hook writes that execute AFTER that hook must not reach its
        // awaits (a beforeEach write has not happened while a beforeAll's
        // callback runs).
        const chain = currentChain();
        const pendingHookScope = [...scopes]
          .reverse()
          .find((scope) => scope.type === "hook");
        pendingAwaits.push({
          node,
          argument: node.argument,
          chain,
          describeChain: describeChainOf(node),
          depth: functionDepth(),
          own: chain.slice(functionDepth()).map((map) => new Map(map)),
          // Frozen copies of the own scopes' declaration registries: an
          // alias reassigned AFTER the await in the same callback must not
          // re-classify it — the await resolves through the declaration
          // standing at the await, while enclosing registries stay live so
          // forward declarations still reach it.
          ownDeclarations: scopes
            .slice(functionDepth())
            .map((scope) => (scope.declarations ? new Map(scope.declarations) : new Map())),
          // Live scope objects parallel to `chain`: the declaration registry
          // resolves through them at re-evaluation time.
          scopeChain: [...scopes],
          hookKey: pendingHookScope
            ? {
                phase: hookPhase(pendingHookScope.hookName ?? "beforeEach"),
                depth: (pendingHookScope.describeChain ?? []).length,
                order: pendingHookScope.hookOrder ?? 0,
                awaitStart: node.range?.[0] ?? 0,
              }
            : null,
        });
      },
    };
  },
};

export default rule;
