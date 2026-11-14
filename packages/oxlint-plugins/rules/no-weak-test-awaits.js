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

    function pushScope(type = "block") {
      scopes.push({ type, bindings: new Map() });
    }

    function popScope() {
      scopes.pop();
    }

    function declareIdentifier(node, isWeakTimerPromise, kind) {
      if (node?.type === "Identifier") {
        const targetScope =
          kind === "var"
            ? scopes.findLast((scope) => scope.type === "function" || scope.type === "program")
            : scopes.at(-1);
        targetScope?.bindings.set(node.name, isWeakTimerPromise);
      }
    }

    function declareParameters(node) {
      for (const parameter of node.params ?? []) {
        declareIdentifier(parameter, false);
      }
    }

    function assignIdentifier(node, isWeakTimerPromise) {
      if (node?.type !== "Identifier") {
        return;
      }

      for (let index = scopes.length - 1; index >= 0; index -= 1) {
        if (scopes[index].bindings.has(node.name)) {
          scopes[index].bindings.set(node.name, isWeakTimerPromise);
          return;
        }
      }
    }

    function isWeakTimerPromiseAlias(node) {
      if (node?.type !== "Identifier") {
        return false;
      }

      for (let index = scopes.length - 1; index >= 0; index -= 1) {
        if (scopes[index].bindings.has(node.name)) {
          return scopes[index].bindings.get(node.name);
        }
      }

      return false;
    }

    function containsWeakTimerPromise(node) {
      if (!node || typeof node !== "object") {
        return false;
      }

      if (isSetTimeoutPromise(node) || isWeakTimerPromiseAlias(node)) {
        return true;
      }

      return Object.entries(node).some(([key, value]) => {
        if (key === "parent") {
          return false;
        }
        if (Array.isArray(value)) {
          return value.some((child) => containsWeakTimerPromise(child));
        }
        return containsWeakTimerPromise(value);
      });
    }

    // Classify a function's own body with its parameters already bound, so a
    // parameter that shadows an outer weak-timer helper is not mistaken for
    // the helper it shadows. (Functions nested inside the scanned body are
    // still classified by plain recursion; their params are not bound.)
    function functionContainsWeakTimerPromise(fn) {
      pushScope("function");
      declareParameters(fn);
      const contains = containsWeakTimerPromise(fn.body);
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
          declareIdentifier(declaration.id, false, "function");
        }
      }

      for (let pass = 0; pass < declarations.length; pass += 1) {
        let changed = false;
        for (const declaration of declarations) {
          if (
            !isWeakTimerPromiseAlias(declaration.id) &&
            functionContainsWeakTimerPromise(declaration)
          ) {
            declareIdentifier(declaration.id, true, "function");
            changed = true;
          }
        }
        if (!changed) {
          break;
        }
      }
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
        declareIdentifier(node.id, functionContainsWeakTimerPromise(node), "function");
        pushScope("function");
        declareParameters(node);
      },
      "FunctionDeclaration:exit": popScope,
      FunctionExpression(node) {
        pushScope("function");
        declareParameters(node);
      },
      "FunctionExpression:exit": popScope,
      ArrowFunctionExpression(node) {
        pushScope("function");
        declareParameters(node);
      },
      "ArrowFunctionExpression:exit": popScope,
      CatchClause(node) {
        pushScope();
        declareIdentifier(node.param, false);
      },
      "CatchClause:exit": popScope,
      VariableDeclarator(node) {
        const isWeak = isFunctionNode(node.init)
          ? functionContainsWeakTimerPromise(node.init)
          : containsWeakTimerPromise(node.init);
        declareIdentifier(node.id, isWeak, node.parent?.kind);
      },
      AssignmentExpression(node) {
        if (node.operator === "=") {
          const isWeak = isFunctionNode(node.right)
            ? functionContainsWeakTimerPromise(node.right)
            : containsWeakTimerPromise(node.right);
          assignIdentifier(node.left, isWeak);
        }
      },
      AwaitExpression(node) {
        const isBarePromiseResolve =
          node.parent?.type === "ExpressionStatement" && isPromiseResolveCall(node.argument);
        if (!isBarePromiseResolve && !containsWeakTimerPromise(node.argument)) {
          return;
        }

        context.report({ node, messageId: "weakAwait" });
      },
    };
  },
};

export default rule;
