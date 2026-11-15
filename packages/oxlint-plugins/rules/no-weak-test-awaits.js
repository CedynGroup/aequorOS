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
 * Whether an unresolved function declaration (one whose body references
 * helpers that may be declared after it) reads as a weak helper at a site
 * whose bindings are `chain`. The body is classified with the parameters
 * bound, so a parameter shadowing an outer helper name wins over the
 * registry; `visiting` breaks recursion on self-referencing helpers.
 */
function unresolvedHelperIsWeak(name, chain, functions, visiting) {
  const fn = functions?.get(name);
  visiting ??= new Set();
  if (!fn || visiting.has(fn)) {
    return false;
  }
  visiting.add(fn);
  try {
    return weakUseIn(fn.body, [...chain, parameterBindings(fn)], false, functions, visiting);
  } finally {
    visiting.delete(fn);
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
 * `functions` (a name → FunctionDeclaration map) resolves unresolved helper
 * chains at the read site; `visiting` breaks recursion.
 */
function weakUseIn(node, chain, awaitMode, functions = null, visiting = null) {
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
      return !awaitMode && unresolvedHelperIsWeak(node.name, chain, functions, visiting);
    }
    return !awaitMode || kind === WEAK_PROMISE;
  }

  if (node.type === "CallExpression") {
    const callee = node.callee;
    if (callee.type === "Identifier") {
      const kind = resolveInChain(chain, callee.name);
      if (kind === WEAK_UNRESOLVED) {
        if (unresolvedHelperIsWeak(callee.name, chain, functions, visiting)) {
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
      if (
        (methodName === "call" || methodName === "apply") &&
        resolveInChain(chain, callee.object.name ?? "")
      ) {
        return true;
      }
      // A timer helper handed to a callback-invoking array method waits
      // exclusively on timer promises.
      if (
        CALLBACK_INVOKERS.has(methodName) &&
        node.arguments[0]?.type === "Identifier" &&
        resolveInChain(chain, node.arguments[0].name)
      ) {
        return true;
      }
      // `obj.sleep()`: the property name is not a helper reference; only the
      // receiver can carry a weak binding.
      if (weakUseIn(callee.object, chain, awaitMode, functions, visiting)) return true;
    } else if (weakUseIn(callee, chain, awaitMode, functions, visiting)) {
      return true;
    }
    return node.arguments.some((argument) => weakUseIn(argument, chain, awaitMode, functions, visiting));
  }

  if (node.type === "MemberExpression") {
    if (weakUseIn(node.object, chain, awaitMode, functions, visiting)) return true;
    // A noncomputed property name never references an outer binding.
    return node.computed ? weakUseIn(node.property, chain, awaitMode, functions, visiting) : false;
  }

  if (
    node.type === "Property" ||
    node.type === "MethodDefinition" ||
    node.type === "PropertyDefinition"
  ) {
    // The value is always live code (a computed key may be too); only a
    // noncomputed key is a mere name.
    if (weakUseIn(node.value, chain, awaitMode, functions, visiting)) return true;
    return node.computed ? weakUseIn(node.key, chain, awaitMode, functions, visiting) : false;
  }

  return Object.entries(node).some(([key, value]) => {
    if (key === "parent" || key === "label") {
      return false;
    }
    if (Array.isArray(value)) {
      return value.some((child) => weakUseIn(child, chain, awaitMode, functions, visiting));
    }
    return weakUseIn(value, chain, awaitMode, functions, visiting);
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
    // Every function declaration in the file, keyed by name. Unresolved
    // helper chains resolve through it at each read site, so a helper whose
    // body calls a weak helper declared after it is still classified.
    const functionDeclarations = new Map();
    // Awaits that looked clean when visited, kept with the binding-map chain
    // they were seen under. A helper declared or assigned LATER can make them
    // weak (forward-declared helpers wrap timer helpers), so every weak
    // declaration re-evaluates the pending awaits against their own chain.
    const pendingAwaits = [];

    function pushScope(type = "block") {
      scopes.push({ type, bindings: new Map() });
    }

    function popScope() {
      scopes.pop();
    }

    function currentChain() {
      return scopes.map((scope) => scope.bindings);
    }

    /** The index of the innermost function/program scope. */
    function functionDepth() {
      for (let index = scopes.length - 1; index >= 0; index -= 1) {
        if (scopes[index].type === "function" || scopes[index].type === "program") {
          return index;
        }
      }
      return 0;
    }

    function resolveKind(name) {
      return resolveInChain(currentChain(), name);
    }

    function declareIdentifier(node, kind, varKind, ordered = false) {
      if (node?.type === "Identifier") {
        const targetScope =
          varKind === "var"
            ? scopes.findLast((scope) => scope.type === "function" || scope.type === "program")
            : scopes.at(-1);
        targetScope?.bindings.set(node.name, kind);
        if (kind) {
          // A `const`/`let` initializer executes in source order: an await
          // that already ran in the same function saw the previous value. A
          // function-declaration classification is hoisted and covers every
          // pending await.
          if (ordered) reevaluatePendingAwaits(functionDepth(), node);
          else reevaluatePendingAwaits();
        }
      }
    }

    function declareParameters(node) {
      for (const parameter of node.params ?? []) {
        declareIdentifier(parameter, false);
      }
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
      let boundary = scopes.length - 1;
      while (
        boundary >= 0 &&
        scopes[boundary].type !== "function" &&
        scopes[boundary].type !== "program" &&
        scopes[boundary].type !== "hook"
      ) {
        boundary -= 1;
      }
      if (boundary >= 0 && scopes[boundary].type === "hook") {
        for (let index = 0; index <= boundary; index += 1) {
          if (scopes[index].bindings.has(node.name)) {
            scopes[index].bindings.set(node.name, kind);
            if (kind) reevaluatePendingAwaits(boundary, node);
            return;
          }
        }
      }
      for (let index = scopes.length - 1; index >= boundary; index -= 1) {
        if (scopes[index].bindings.has(node.name)) {
          scopes[index].bindings.set(node.name, kind);
          if (kind) reevaluatePendingAwaits(boundary, node);
          return;
        }
      }
      // A captured outer binding reassigned inside this function: record a
      // function-local override so the awaits that follow it see the new
      // kind, without changing the classification the enclosing scopes
      // already have.
      if (boundary >= 0) {
        scopes[boundary].bindings.set(node.name, kind);
        if (kind) reevaluatePendingAwaits(boundary, node);
      }
    }

    /** The kind an alias inherits: helpers alias to helpers, promises to promises. */
    function aliasedKind(init) {
      if (init?.type !== "Identifier") return null;
      const kind = resolveKind(init.name);
      return kind === WEAK_HELPER || kind === WEAK_PROMISE ? kind : null;
    }

    /** Classify the kind a declarator/assignment binds, from its right side. */
    function boundKind(right) {
      const alias = aliasedKind(right);
      if (alias) return alias;
      if (isFunctionNode(right)) {
        return functionContainsWeakTimerPromise(right) ? WEAK_HELPER : false;
      }
      return weakUseIn(right, currentChain(), false, functionDeclarations) ? WEAK_PROMISE : false;
    }

    function reevaluatePendingAwaits(boundary) {
      for (let index = pendingAwaits.length - 1; index >= 0; index -= 1) {
        const pending = pendingAwaits[index];
        if (boundary !== undefined) {
          // An update that executes in source order cannot retroactively
          // reach an await that already ran in the same function: skip
          // pendings inside that function recorded before the update. A
          // pending in a SIBLING function (a test beside the hook that
          // assigned, or vice versa) has its own binding map, so it is
          // reevaluated; so is a pending in a function nested inside the
          // assigning one, which may be called after the update.
          const sameFunction =
            pending.depth <= boundary &&
            pending.chain[boundary] === scopes[boundary]?.bindings;
          if (sameFunction) {
            continue;
          }
        }
        if (weakUseIn(pending.argument, pending.chain, true, functionDeclarations)) {
          context.report({ node: pending.node, messageId: "weakAwait" });
          pendingAwaits.splice(index, 1);
        }
      }
    }

    // Classify a function's own body with its parameters already bound, so a
    // parameter that shadows an outer weak-timer helper is not mistaken for
    // the helper it shadows. (Functions nested inside the scanned body are
    // still classified by plain recursion; their params are not bound.)
    function functionContainsWeakTimerPromise(fn) {
      pushScope("function");
      declareParameters(fn);
      const contains = weakUseIn(fn.body, currentChain(), false, functionDeclarations);
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
          declarations.push(declaration);
          functionDeclarations.set(declaration.id.name, declaration);
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
      if (
        node.parent?.type === "CallExpression" &&
        callee?.type === "Identifier" &&
        SETUP_HOOKS.has(callee.name)
      ) {
        return "hook";
      }
      return "function";
    }

    return {
      Program(node) {
        pushScope("program");
        declareFunctionDeclarations(node.body);
      },
      "Program:exit": popScope,
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
        // read site against the bindings standing there.
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
        declareParameters(node);
      },
      "FunctionExpression:exit": popScope,
      ArrowFunctionExpression(node) {
        pushScope(scopeTypeFor(node));
        declareParameters(node);
      },
      "ArrowFunctionExpression:exit": popScope,
      CatchClause(node) {
        pushScope();
        declareIdentifier(node.param, false);
      },
      "CatchClause:exit": popScope,
      VariableDeclarator(node) {
        declareIdentifier(node.id, boundKind(node.init), node.parent?.kind, true);
      },
      AssignmentExpression(node) {
        if (node.operator === "=") {
          assignIdentifier(node.left, boundKind(node.right));
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
        if (weakUseIn(node.argument, chain, true, functionDeclarations)) {
          context.report({ node, messageId: "weakAwait" });
          return;
        }

        // Clean for now: a later weak declaration can still make it weak.
        pendingAwaits.push({
          node,
          argument: node.argument,
          chain,
          depth: functionDepth(),
        });
      },
    };
  },
};

export default rule;
