#!/usr/bin/env node
// Syntax-only guard: do not load dependencies or reorder interface declarations.
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import ts from "typescript";

// Coverage floor, not an exception list. Newly added public.ts files are scanned
// recursively as well. A removed/renamed established root needs explicit review.
const REQUIRED_ROOTS = ["bi", "home", "liquidity", "live", "risk", "workbench"];

function publicFiles(directory) {
  const files = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    if (entry.name.startsWith(".") || entry.name === "node_modules") continue;
    const full = path.join(directory, entry.name);
    if (entry.isDirectory()) files.push(...publicFiles(full));
    else if (entry.isFile() && entry.name === "public.ts") files.push(full);
  }
  return files.sort();
}

function literalAttributes(attributes) {
  return (
    attributes === undefined ||
    attributes.elements.every(
      (element) =>
        (ts.isIdentifier(element.name) || ts.isStringLiteral(element.name)) &&
        ts.isStringLiteral(element.value),
    )
  );
}

function declaration(statement) {
  if (ts.isImportDeclaration(statement)) {
    return (
      !statement.modifiers?.length &&
      ts.isStringLiteral(statement.moduleSpecifier) &&
      literalAttributes(statement.attributes)
    );
  }
  if (ts.isExportDeclaration(statement)) {
    return (
      !statement.modifiers?.length &&
      (statement.moduleSpecifier === undefined ||
        ts.isStringLiteral(statement.moduleSpecifier)) &&
      literalAttributes(statement.attributes)
    );
  }
  return false;
}

export function scanPublicInterfaces(root) {
  const features = path.join(root, "features");
  const files = publicFiles(features);
  const relativeFiles = new Set(
    files.map((file) => path.relative(features, file)),
  );
  const failures = REQUIRED_ROOTS.filter(
    (name) => !relativeFiles.has(path.join(name, "public.ts")),
  ).map(
    (name) => `features/${name}/public.ts: missing required public interface`,
  );
  for (const file of files) {
    const relative = path.relative(root, file);
    const source = ts.createSourceFile(
      file,
      readFileSync(file, "utf8"),
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TS,
    );
    if (source.parseDiagnostics.length > 0) {
      for (const diagnostic of source.parseDiagnostics) {
        const { line } = source.getLineAndCharacterOfPosition(
          diagnostic.start ?? 0,
        );
        failures.push(
          `${relative}:${line + 1}: invalid syntax: ${ts.flattenDiagnosticMessageText(diagnostic.messageText, " ")}`,
        );
      }
      continue;
    }
    let prologue = true;
    for (const statement of source.statements) {
      if (
        prologue &&
        ts.isExpressionStatement(statement) &&
        ts.isStringLiteral(statement.expression)
      ) {
        continue;
      }
      prologue = false;
      if (!declaration(statement)) {
        const { line } = source.getLineAndCharacterOfPosition(
          statement.getStart(source),
        );
        const kind = ts.isVariableStatement(statement)
          ? "VariableStatement"
          : ts.SyntaxKind[statement.kind];
        failures.push(
          `${relative}:${line + 1}: ${kind} is not an import, re-export or directive`,
        );
      }
    }
  }
  return { files, failures };
}

function main() {
  const args = process.argv.slice(2);
  if (args.length !== 0 && (args.length !== 2 || args[0] !== "--root")) {
    throw new Error("usage: check_public_interfaces.mjs [--root ROOT]");
  }
  const root = args[1] ?? path.resolve(import.meta.dirname, "..");
  const { files, failures } = scanPublicInterfaces(root);
  if (failures.length > 0) {
    process.stderr.write(`${failures.join("\n")}\n`);
    process.exitCode = 1;
  } else {
    process.stdout.write(
      `Public-interface check passed: ${files.length} modules.\n`,
    );
  }
}

if (import.meta.filename === process.argv[1]) main();
