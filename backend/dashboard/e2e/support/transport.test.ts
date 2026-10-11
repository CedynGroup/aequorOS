/**
 * The journey harness runs on SQLite, so it must declare the transport carve-out.
 *
 * #492: the manual journeys workflow failed before a single test ran. Database
 * TLS became mandatory in #470, and `plaintext_allowed()` requires BOTH an
 * undeployed `APP_ENV` and `TLS_ALLOW_PLAINTEXT`. The harness set only the
 * first, so `create_app` refused at startup with
 * `TransportSecurityError: Database transport requires PostgreSQL with a
 * hostname` — the disposable SQLite file is not a network transport at all.
 *
 * Nothing caught it, because `dashboard-journeys.yml` is manual-dispatch: a PR
 * that tightens a backend startup policy is green while leaving the harness
 * unstartable. Until the journeys run on pull requests, this test is the only
 * thing standing between that policy and a harness nobody notices is broken.
 *
 * So the rule pinned here is the pairing itself, not the flag: ANY webServer
 * whose env points `DATABASE_URL` at SQLite must declare `TLS_ALLOW_PLAINTEXT`
 * in the same env. Stating it that way means a second harness, or a rename of
 * the existing one, is covered without editing this file.
 *
 * This is not a hole in production enforcement. `validate_service_transports`
 * raises outright when `APP_ENV` is `staging` or `production` and the flag is
 * set, which `tests/core/test_tls.py::test_production_refuses_local_escape_hatch`
 * pins from the backend side.
 *
 * Run by `pnpm --filter @aequoros/dashboard test`.
 */

import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import ts from "typescript";

/**
 * Compiled output lives under `.test-out/`, so `__dirname` is NOT the source
 * tree. Walk up to the real dashboard root, as `no-literals.test.ts` does.
 */
function dashboardRoot(): string {
  let dir = __dirname;
  for (let i = 0; i < 8; i += 1) {
    const manifest = join(dir, "package.json");
    if (existsSync(manifest)) {
      const name = JSON.parse(readFileSync(manifest, "utf8")).name as string;
      if (name === "@aequoros/dashboard") return dir;
    }
    dir = dirname(dir);
  }
  throw new Error("could not locate the dashboard package root");
}

const CONFIG = join(dashboardRoot(), "playwright.config.ts");

/** Every object literal in the file, so the pairing is checked per env block. */
function objectLiterals(source: ts.SourceFile): ts.ObjectLiteralExpression[] {
  const found: ts.ObjectLiteralExpression[] = [];
  const visit = (node: ts.Node): void => {
    if (ts.isObjectLiteralExpression(node)) found.push(node);
    ts.forEachChild(node, visit);
  };
  ts.forEachChild(source, visit);
  return found;
}

function propertyNames(literal: ts.ObjectLiteralExpression): Set<string> {
  const names = new Set<string>();
  for (const property of literal.properties) {
    const name = property.name;
    if (name && (ts.isIdentifier(name) || ts.isStringLiteral(name))) names.add(name.text);
  }
  return names;
}

/** The property's source text — enough to tell a sqlite URL from a postgres one. */
function propertyText(literal: ts.ObjectLiteralExpression, key: string): string | null {
  for (const property of literal.properties) {
    const name = property.name;
    if (!name || !(ts.isIdentifier(name) || ts.isStringLiteral(name))) continue;
    if (name.text !== key) continue;
    return ts.isPropertyAssignment(property) ? property.initializer.getText() : property.getText();
  }
  return null;
}

const text = readFileSync(CONFIG, "utf8");
const source = ts.createSourceFile(CONFIG, text, ts.ScriptTarget.Latest, true);

const sqliteEnvs = objectLiterals(source).filter((literal) => {
  const url = propertyText(literal, "DATABASE_URL");
  return url !== null && url.includes("sqlite");
});

assert.ok(
  sqliteEnvs.length > 0,
  "playwright.config.ts declares no SQLite DATABASE_URL — if the journey harness " +
    "moved to PostgreSQL, delete this test rather than leaving it passing vacuously.",
);

for (const env of sqliteEnvs) {
  const names = propertyNames(env);
  const line = source.getLineAndCharacterOfPosition(env.getStart()).line + 1;
  assert.ok(
    names.has("TLS_ALLOW_PLAINTEXT"),
    `playwright.config.ts:${line} points DATABASE_URL at SQLite without declaring ` +
      "TLS_ALLOW_PLAINTEXT. Since #470 the backend refuses to start on a non-PostgreSQL " +
      "database unless the undeployed-environment carve-out is explicit, so the journeys " +
      "would fail at startup before running (#492).",
  );
  // The VALUE, not just the key. `TLS_ALLOW_PLAINTEXT: "0"` declares the
  // variable and leaves `plaintext_allowed()` false, so the harness still
  // fails at startup — a guard that accepted it would pass on exactly the
  // broken configuration it exists to catch.
  assert.ok(
    ["\"1\"", "\"true\""].includes(propertyText(env, "TLS_ALLOW_PLAINTEXT") ?? ""),
    `playwright.config.ts:${line} declares TLS_ALLOW_PLAINTEXT as ` +
      `${propertyText(env, "TLS_ALLOW_PLAINTEXT")}, which does not enable the ` +
      "carve-out. `plaintext_allowed()` needs it truthy; anything else leaves the " +
      "journey backend refusing to start on SQLite (#492).",
  );
  assert.ok(
    names.has("APP_ENV"),
    `playwright.config.ts:${line} declares TLS_ALLOW_PLAINTEXT without APP_ENV. The ` +
      "carve-out needs both: the flag alone does nothing, and a deployed APP_ENV with " +
      "the flag set is refused outright.",
  );
  assert.equal(
    propertyText(env, "APP_ENV"),
    '"test"',
    `playwright.config.ts:${line} must pin APP_ENV to "test" — the carve-out applies ` +
      "only to local/test, and the journeys must not depend on a developer's own value.",
  );
}

console.log(
  `journey transport: ${sqliteEnvs.length} SQLite harness env(s) declare the test-only carve-out`,
);
