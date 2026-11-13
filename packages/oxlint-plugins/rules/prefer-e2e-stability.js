/**
 * Oxlint JS plugin rule: acts/prefer-e2e-stability (ACTS003)
 *
 * Enforces high-signal anti-flake patterns for Playwright E2E tests:
 *   1. No `page.waitForTimeout(...)`
 *   2. No `page.waitForLoadState("networkidle")`
 *
 * The check follows page-like bindings (`page`, `*Page`, `pageN`, aliases and
 * destructured parameters) so helper wrappers keep the guarantee. Scoping to
 * E2E files is the consuming configuration's job (.oxlintrc.json overrides).
 */

const PAGE_LIKE_ALIAS = "__PAGE_LIKE_ALIAS__";
const SHADOWED_BINDING = Symbol("shadowed-binding");

function resolveBindingValue(valueOrResolver, name) {
  if (typeof valueOrResolver === "function") {
    return valueOrResolver(name);
  }
  return valueOrResolver;
}

function recordPatternBindings(pattern, valueOrResolver, scope) {
  if (!pattern) return;

  if (pattern.type === "Identifier") {
    scope.set(pattern.name, resolveBindingValue(valueOrResolver, pattern.name));
    return;
  }

  if (pattern.type === "RestElement") {
    recordPatternBindings(pattern.argument, valueOrResolver, scope);
    return;
  }

  if (pattern.type === "AssignmentPattern") {
    recordPatternBindings(pattern.left, valueOrResolver, scope);
    return;
  }

  if (pattern.type === "ArrayPattern") {
    for (const element of pattern.elements) {
      if (element) recordPatternBindings(element, valueOrResolver, scope);
    }
    return;
  }

  if (pattern.type === "ObjectPattern") {
    for (const property of pattern.properties) {
      if (property.type === "Property") {
        recordPatternBindings(property.value, valueOrResolver, scope);
      } else if (property.type === "RestElement") {
        recordPatternBindings(property.argument, valueOrResolver, scope);
      }
    }
    return;
  }

  if (pattern.type === "TSParameterProperty") {
    recordPatternBindings(pattern.parameter, valueOrResolver, scope);
  }
}

function bindingValueFromName(name) {
  return isPageLikeName(name) ? PAGE_LIKE_ALIAS : SHADOWED_BINDING;
}

function recordParameterBindings(pattern, scope) {
  if (!pattern) return;

  if (pattern.type === "ObjectPattern") {
    for (const property of pattern.properties) {
      if (property.type === "Property") {
        const keyName = property.key.type === "Identifier" ? property.key.name : null;

        if (keyName && isPageLikeName(keyName)) {
          if (property.value.type === "Identifier") {
            scope.set(property.value.name, PAGE_LIKE_ALIAS);
            continue;
          }
          if (
            property.value.type === "AssignmentPattern" &&
            property.value.left.type === "Identifier"
          ) {
            scope.set(property.value.left.name, PAGE_LIKE_ALIAS);
            continue;
          }
        }

        recordParameterBindings(property.value, scope);
      } else if (property.type === "RestElement") {
        recordPatternBindings(property.argument, SHADOWED_BINDING, scope);
      }
    }
    return;
  }

  recordPatternBindings(pattern, bindingValueFromName, scope);
}

function resolveConstant(scopeStack, name) {
  for (let i = scopeStack.length - 1; i >= 0; i--) {
    if (scopeStack[i].has(name)) {
      return scopeStack[i].get(name);
    }
  }
  return undefined;
}

function getStaticString(node) {
  if (!node) return null;
  if (node.type === "Literal" && typeof node.value === "string") {
    return node.value;
  }
  if (
    node.type === "TemplateLiteral" &&
    node.expressions.length === 0 &&
    node.quasis.length === 1
  ) {
    return node.quasis[0].value.cooked ?? null;
  }
  return null;
}

function isPageLikeName(name) {
  return name === "page" || name.endsWith("Page") || /^page\d+$/.test(name);
}

function unwrapExpression(node) {
  let current = node;
  while (current) {
    if (
      current.type === "TSAsExpression" ||
      current.type === "AsExpression" ||
      current.type === "TSTypeAssertion" ||
      current.type === "TypeAssertionExpression"
    ) {
      current = current.expression;
      continue;
    }
    if (current.type === "TSNonNullExpression" || current.type === "NonNullExpression") {
      current = current.expression;
      continue;
    }
    if (current.type === "ParenthesizedExpression") {
      current = current.expression;
      continue;
    }
    if (current.type === "ChainExpression") {
      current = current.expression;
      continue;
    }
    if (
      current.expression &&
      typeof current.expression === "object" &&
      current.type !== "Identifier" &&
      current.type !== "MemberExpression" &&
      current.type !== "CallExpression"
    ) {
      current = current.expression;
      continue;
    }
    break;
  }
  return current;
}

function getMemberName(node) {
  const unwrapped = unwrapExpression(node);
  if (!unwrapped) return null;
  if (unwrapped.type === "Identifier") return unwrapped.name;
  if (unwrapped.type === "MemberExpression" && unwrapped.property.type === "Identifier") {
    return unwrapped.property.name;
  }
  return null;
}

function isPageLikeObject(node, scopeStack) {
  const unwrapped = unwrapExpression(node);
  const name = getMemberName(unwrapped);
  if (typeof name !== "string") return false;

  if (unwrapped.type === "Identifier") {
    const resolved = resolveConstant(scopeStack, unwrapped.name);
    if (resolved === SHADOWED_BINDING) return false;
    if (resolved === PAGE_LIKE_ALIAS) return true;
    if (resolved !== undefined) return false;
  }

  return isPageLikeName(name);
}

function isNetworkIdleArgument(arg, scopeStack) {
  const directValue = getStaticString(arg);
  if (directValue === "networkidle") {
    return true;
  }

  if (arg.type === "Identifier") {
    const resolved = resolveConstant(scopeStack, arg.name);
    return resolved === "networkidle";
  }

  return false;
}

function getMemberCall(node, methodName) {
  const callee = unwrapExpression(node.callee);
  if (
    callee &&
    callee.type === "MemberExpression" &&
    callee.property.type === "Identifier" &&
    callee.property.name === methodName
  ) {
    return callee;
  }
  return null;
}

const rule = {
  meta: {
    type: "problem",
    docs: {
      description:
        "Prefer explicit readiness/navigation helpers in web E2E tests to avoid flaky Playwright patterns",
    },
    messages: {
      noWaitForTimeout:
        "ACTS003: avoid `waitForTimeout()` in E2E tests. Wait for a specific response, URL, locator or other observable condition instead.",
      noNetworkIdle:
        "ACTS003: avoid `waitForLoadState(\"networkidle\")` for app readiness in E2E tests. Use explicit API/UI readiness signals instead.",
    },
    schema: [],
  },
  create(context) {
    const scopeStack = [new Map()];
    const currentScope = () => scopeStack[scopeStack.length - 1];

    return {
      Program() {
        scopeStack.length = 1;
      },

      BlockStatement() {
        scopeStack.push(new Map());
      },

      "BlockStatement:exit"() {
        if (scopeStack.length > 1) {
          scopeStack.pop();
        }
      },

      ForStatement() {
        scopeStack.push(new Map());
      },

      "ForStatement:exit"() {
        if (scopeStack.length > 1) {
          scopeStack.pop();
        }
      },

      ForInStatement() {
        scopeStack.push(new Map());
      },

      "ForInStatement:exit"() {
        if (scopeStack.length > 1) {
          scopeStack.pop();
        }
      },

      ForOfStatement() {
        scopeStack.push(new Map());
      },

      "ForOfStatement:exit"() {
        if (scopeStack.length > 1) {
          scopeStack.pop();
        }
      },

      FunctionDeclaration(node) {
        if (node.id) {
          currentScope().set(node.id.name, SHADOWED_BINDING);
        }

        scopeStack.push(new Map());
        for (const param of node.params) {
          recordParameterBindings(param, currentScope());
        }
      },

      "FunctionDeclaration:exit"() {
        if (scopeStack.length > 1) {
          scopeStack.pop();
        }
      },

      FunctionExpression(node) {
        scopeStack.push(new Map());
        if (node.id) {
          currentScope().set(node.id.name, SHADOWED_BINDING);
        }
        for (const param of node.params) {
          recordParameterBindings(param, currentScope());
        }
      },

      "FunctionExpression:exit"() {
        if (scopeStack.length > 1) {
          scopeStack.pop();
        }
      },

      ArrowFunctionExpression(node) {
        scopeStack.push(new Map());
        for (const param of node.params) {
          recordParameterBindings(param, currentScope());
        }
      },

      "ArrowFunctionExpression:exit"() {
        if (scopeStack.length > 1) {
          scopeStack.pop();
        }
      },

      CatchClause(node) {
        scopeStack.push(new Map());
        if (node.param) {
          recordPatternBindings(node.param, SHADOWED_BINDING, currentScope());
        }
      },

      "CatchClause:exit"() {
        if (scopeStack.length > 1) {
          scopeStack.pop();
        }
      },

      ClassDeclaration(node) {
        if (!node.id) return;
        currentScope().set(node.id.name, SHADOWED_BINDING);
      },

      ImportDeclaration(node) {
        for (const specifier of node.specifiers) {
          currentScope().set(specifier.local.name, SHADOWED_BINDING);
        }
      },

      VariableDeclaration(node) {
        for (const declaration of node.declarations) {
          recordPatternBindings(declaration.id, bindingValueFromName, currentScope());

          if (node.kind !== "const" || declaration.id.type !== "Identifier" || !declaration.init) {
            continue;
          }

          // Track string constants so `waitForLoadState(READINESS)` with
          // `const READINESS = "networkidle"` is still recognised.
          const value = getStaticString(declaration.init);
          if (value !== null) {
            currentScope().set(declaration.id.name, value);
            continue;
          }

          if (declaration.init.type === "Identifier") {
            if (isPageLikeName(declaration.init.name)) {
              currentScope().set(declaration.id.name, PAGE_LIKE_ALIAS);
              continue;
            }

            const aliased = resolveConstant(scopeStack, declaration.init.name);
            if (typeof aliased === "string" || aliased === PAGE_LIKE_ALIAS) {
              currentScope().set(declaration.id.name, aliased);
            }
          }
        }
      },

      CallExpression(node) {
        const timeoutCall = getMemberCall(node, "waitForTimeout");
        if (timeoutCall && isPageLikeObject(timeoutCall.object, scopeStack)) {
          context.report({
            node,
            messageId: "noWaitForTimeout",
          });
          return;
        }

        const loadStateCall = getMemberCall(node, "waitForLoadState");
        if (loadStateCall && isPageLikeObject(loadStateCall.object, scopeStack)) {
          const firstArg = node.arguments[0];
          if (firstArg && isNetworkIdleArgument(firstArg, scopeStack)) {
            context.report({
              node,
              messageId: "noNetworkIdle",
            });
          }
        }
      },
    };
  },
};

export default rule;
