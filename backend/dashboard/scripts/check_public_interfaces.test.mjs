import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const dashboard = fileURLToPath(new URL("../", import.meta.url));
const guard = path.join(dashboard, "scripts/check_public_interfaces.mjs");
const roots = ["bi", "home", "liquidity", "live", "risk", "workbench"];

function fixture(t) {
  const root = mkdtempSync(path.join(tmpdir(), "public-interfaces-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  for (const name of roots) {
    const directory = path.join(root, "features", name);
    mkdirSync(directory, { recursive: true });
    writeFileSync(
      path.join(directory, "public.ts"),
      'export * from "./missing_dependency";\n',
    );
  }
  return root;
}

function run(root) {
  return spawnSync(process.execPath, [guard, "--root", root], {
    encoding: "utf8",
  });
}

test("the current dashboard public interfaces satisfy the syntax contract", () => {
  const result = run(dashboard);
  assert.equal(result.status, 0, result.stderr);
  assert.match(
    result.stdout,
    /Public-interface check passed: [1-9]\d* modules/,
  );
});

test("imports, re-exports, comments and directive prologues are accepted without execution", (t) => {
  const root = fixture(t);
  const source = [
    "// Prose mentioning function, class and initialize() is harmless.",
    '"use client";',
    '"use strict";',
    'import "./missing_side_effect_dependency";',
    'import Default, { value as renamed, type Shape } from "./missing_dependency";',
    'import * as namespace from "./missing_dependency";',
    'import type { Other } from "./missing_dependency";',
    'import data from "./missing.json" with { type: "json" };',
    "export { renamed, Default as default, namespace };",
    "export type { Shape, Other };",
    'export { default as View, type Props } from "./missing_component";',
    'export * from "./missing_dependency";',
    'export * as helpers from "./missing_dependency";',
    'export type * from "./missing_dependency";',
    'export type * as contracts from "./missing_dependency";',
    'export { default as data } from "./missing.json" with { type: "json" };',
    "export {};",
    "",
  ].join("\n");
  const file = path.join(root, "features/bi/public.ts");
  writeFileSync(file, source);
  const result = run(root);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(readFileSync(file, "utf8"), source);
});

for (const [name, source, diagnostic] of [
  ["function", "export function calculate() {}", "FunctionDeclaration"],
  [
    "async function",
    "export async function calculate() {}",
    "FunctionDeclaration",
  ],
  ["class", "export class Result {}", "ClassDeclaration"],
  ["call", "initialize();", "ExpressionStatement"],
  ["initializer", "export const result = calculate();", "VariableStatement"],
  ["literal initializer", "export const result = 1;", "VariableStatement"],
  [
    "reference initializer",
    "const result = dependency.result;",
    "VariableStatement",
  ],
  ["arrow", "export const calculate = () => 1;", "VariableStatement"],
  ["default function", "export default function () {}", "FunctionDeclaration"],
  ["default call", "export default calculate();", "ExportAssignment"],
  ["default literal", "export default 1;", "ExportAssignment"],
  ["export equals", "export = calculate();", "ExportAssignment"],
  [
    "require initializer",
    'import value = require("./dependency");',
    "ImportEqualsDeclaration",
  ],
  ["dynamic import", 'import("./dependency");', "ExpressionStatement"],
  ["type alias", "export type Result = string;", "TypeAliasDeclaration"],
  ["interface", "export interface Result {}", "InterfaceDeclaration"],
  [
    "ambient declaration",
    "export declare function calculate(): void;",
    "FunctionDeclaration",
  ],
  ["enum", "export enum Result { Ready }", "EnumDeclaration"],
  ["namespace", "export namespace Result {}", "ModuleDeclaration"],
  ["conditional", "if (true) initialize();", "IfStatement"],
  ["block", "{ initialize(); }", "Block"],
  ["late string", 'export {};\n"use client";', "ExpressionStatement"],
  ["template", "`use client`;", "ExpressionStatement"],
  ["template call", "`${initialize()}`;", "ExpressionStatement"],
  [
    "decorated import",
    '@initialize() import value from "./dependency";',
    "ImportDeclaration",
  ],
  [
    "decorated re-export",
    '@initialize() export { value } from "./dependency";',
    "ExportDeclaration",
  ],
  [
    "modified re-export",
    'declare export { value } from "./dependency";',
    "ExportDeclaration",
  ],
]) {
  test(`rejects ${name} in newly discovered nested public modules`, (t) => {
    const root = fixture(t);
    const directory = path.join(root, "features/new_feature/nested");
    mkdirSync(directory, { recursive: true });
    writeFileSync(path.join(directory, "public.ts"), source);
    const result = run(root);
    assert.equal(result.status, 1, result.stdout);
    assert.ok(
      result.stderr.includes(`features/new_feature/nested/public.ts:`),
      result.stderr,
    );
    assert.ok(
      result.stderr.includes(
        `${diagnostic} is not an import, re-export or directive`,
      ),
      result.stderr,
    );
  });
}

for (const source of [
  'import value from "./dependency" with { type: configure() };',
  'export { value } from "./dependency" with { type: configure() };',
  'import value from "./dependency" with { [configure()]: "json" };',
  "import value from `./dependency`;",
  "export { value } from getModule();",
]) {
  test(`rejects computed import/export syntax: ${source}`, (t) => {
    const root = fixture(t);
    writeFileSync(path.join(root, "features/bi/public.ts"), source);
    const result = run(root);
    assert.equal(result.status, 1, result.stdout);
    assert.match(result.stderr, /features\/bi\/public\.ts:/);
    assert.match(
      result.stderr,
      /invalid syntax|is not an import, re-export or directive/,
    );
  });
}

for (const name of roots) {
  test(`fails when the established ${name} public root disappears`, (t) => {
    const root = fixture(t);
    unlinkSync(path.join(root, "features", name, "public.ts"));
    const result = run(root);
    assert.equal(result.status, 1);
    assert.equal(
      result.stderr,
      `features/${name}/public.ts: missing required public interface\n`,
    );
  });
}

test("fails on an empty features directory", (t) => {
  const root = fixture(t);
  rmSync(path.join(root, "features"), { recursive: true });
  mkdirSync(path.join(root, "features"));
  const result = run(root);
  assert.equal(result.status, 1);
  assert.equal(result.stderr.split("\n").filter(Boolean).length, roots.length);
});

test("fails on a missing features directory", (t) => {
  const root = fixture(t);
  rmSync(path.join(root, "features"), { recursive: true });
  const result = run(root);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /ENOENT/);
});

test("rejects malformed exports rather than trusting parser recovery", (t) => {
  const root = fixture(t);
  writeFileSync(
    path.join(root, "features/bi/public.ts"),
    'export { value from "./dependency";',
  );
  const result = run(root);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /features\/bi\/public\.ts:1: invalid syntax/);
});
