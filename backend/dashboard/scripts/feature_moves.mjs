#!/usr/bin/env node
// Move-and-reroute codemod for the dashboard feature layout (FEATURE_LAYOUT.md).
//
//   node scripts/feature_moves.mjs move [--shim] OLD NEW [OLD NEW ...]
//   node scripts/feature_moves.mjs rewrite
//   node scripts/feature_moves.mjs check
//
// `move` git-mv's each path, records the pair in feature_module_moves.json
// (the cumulative ledger consumed by review) and rewrites references in
// dashboard text files:
//   - `@/old/...` alias specifiers and bare `old/...` slash-path references
//     (guards, docs, workflow strings) in .ts/.tsx/.js/.mjs/.json/.md/.yml/.css
//   - relative imports, resolved against the importer's pre-move location so
//     files that moved together keep pointing at each other
// `--shim` is for App Router route pages: routes must stay under app/, so the
// moved page leaves a thin `export { default } from "@/..."` re-export at its
// old path. Shimmed moves are structural, not rename history, and are not
// recorded in the ledger.
//
// `check` fails when a ledgered module is still present at its old path.
// Validation happens before any file changes; overlapping moves must run as
// separate commands.
//
// The layout tooling itself is excluded from rewrites: the codemod's source
// and tests carry fixture paths, and FEATURE_LAYOUT.md documents the scheme
// with examples, none of which are instances of a move.
import { existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { spawnSync } from "node:child_process";

const REWRITE_EXTS = new Set([".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json", ".md", ".yml", ".yaml", ".css"]);
const SKIP_DIRS = new Set(["node_modules", "coverage", ".test-out", "test-results", "playwright-report"]);
const LEDGER_NAME = "feature_module_moves.json";
const REWRITE_EXCLUDED = new Set([
  "scripts/feature_moves.mjs",
  "scripts/feature_moves.test.mjs",
  "scripts/check_feature_boundaries.mjs",
  "scripts/check_feature_boundaries.test.mjs",
  "FEATURE_LAYOUT.md",
]);
const FOLLOW_OK = new Set(["/", '"', "'", "`", " ", "\t", ")", "]", ",", ":", ">", "*", "\n"]);

function die(message) {
  process.stderr.write(`error: ${message}\n`);
  process.exit(1);
}

export function parseArgs(argv) {
  const options = { root: null, shim: false, command: null, pairs: [] };
  const positional = [];
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--shim") {
      options.shim = true;
    } else if (argv[i] === "--root") {
      i += 1;
      if (i >= argv.length) die("--root requires a value");
      options.root = argv[i];
    } else {
      positional.push(argv[i]);
    }
  }
  [options.command] = positional.splice(0, 1);
  if (options.command === "move") {
    if (positional.length === 0 || positional.length % 2 !== 0) die("move requires OLD NEW pairs");
    for (let i = 0; i < positional.length; i += 2) options.pairs.push([positional[i], positional[i + 1]]);
  } else if (options.command !== "check" && options.command !== "rewrite") {
    die("usage: feature_moves.mjs move [--shim] OLD NEW [...] | rewrite | check");
  }
  return options;
}

function listFiles(dir, out) {
  for (const entry of readdirSync(dir).sort()) {
    if (entry.startsWith(".") || SKIP_DIRS.has(entry)) continue;
    const full = path.posix.join(dir, entry);
    if (statSync(full).isDirectory()) {
      listFiles(full, out);
    } else if (REWRITE_EXTS.has(path.posix.extname(entry))) {
      out.push(full);
    }
  }
  return out;
}

export function readLedger(root) {
  const file = path.posix.join(root, LEDGER_NAME);
  if (!existsSync(file)) return [];
  const parsed = JSON.parse(readFileSync(file, "utf8"));
  if (!Array.isArray(parsed)) throw new Error(`Malformed ledger: ${file}`);
  return parsed;
}

function writeLedger(root, moves) {
  const file = path.posix.join(root, LEDGER_NAME);
  const body = `${JSON.stringify(moves.map(([oldPath, newPath]) => [oldPath, newPath]), null, 2)}\n`;
  writeFileSync(file, body);
}

function validatePairs(root, pairs) {
  for (const [oldPath, newPath] of pairs) {
    if (!existsSync(path.posix.join(root, oldPath))) die(`old path does not exist: ${oldPath}`);
    if (existsSync(path.posix.join(root, newPath))) die(`new path already exists: ${newPath}`);
    if (oldPath === newPath) die("old and new paths are identical");
  }
}

function gitMove(root, oldPath, newPath) {
  mkdirSync(path.posix.dirname(path.posix.join(root, newPath)), { recursive: true });
  const result = spawnSync("git", ["mv", oldPath, newPath], { cwd: root, encoding: "utf8" });
  if (result.status !== 0) {
    die(`git mv ${oldPath} ${newPath} failed: ${result.stderr || result.stdout}`);
  }
}

function gitLsFiles(root) {
  const result = spawnSync("git", ["ls-files"], { cwd: root, encoding: "utf8", maxBuffer: 1024 * 1024 * 64 });
  if (result.status !== 0) return null;
  return result.stdout.split("\n").filter(Boolean);
}

// Replace every occurrence of `needle` where the preceding character is not
// part of a longer path/identifier and the following character ends the token
// or extends it as a subpath. `escaped` handles regex-literal references
// (`@\/components\/fx`): the needle's slashes become `\/`, a preceding `\/`
// is a path start rather than a longer path, and a following `\/` is a
// subpath separator.
export function replaceBounded(content, needle, replacement, escaped = false) {
  const rawNeedle = escaped ? needle.split("/").join("\\/") : needle;
  const rawReplacement = escaped ? replacement.split("/").join("\\/") : replacement;
  let index = content.indexOf(rawNeedle);
  if (index === -1) return content;
  let result = "";
  let cursor = 0;
  while (index !== -1) {
    const after = index + rawNeedle.length;
    const prev = index > 0 ? content[index - 1] : "";
    const next = content[after] ?? "";
    const prevOk =
      prev === "" ||
      !/[A-Za-z0-9_.@\-/]/.test(prev) ||
      (escaped && prev === "/" && index >= 2 && content[index - 2] === "\\");
    const nextOk =
      next === "" ||
      FOLLOW_OK.has(next) ||
      (escaped && next === "\\" && content[after + 1] === "/");
    if (prevOk && nextOk) {
      result += content.slice(cursor, index) + rawReplacement;
      cursor = after;
    }
    index = content.indexOf(rawNeedle, index + 1);
  }
  result += content.slice(cursor);
  return result;
}

// `@/`-prefixed references: the preceding character may be any quote or text,
// so only the trailing boundary matters. `escaped` handles regex literals.
export function replaceAlias(content, oldPath, newPath, escaped = false) {
  const needle = escaped ? `@/${oldPath}`.split("/").join("\\/") : `@/${oldPath}`;
  const replacement = escaped ? `@/${newPath}`.split("/").join("\\/") : `@/${newPath}`;
  let index = content.indexOf(needle);
  if (index === -1) return content;
  let result = "";
  let cursor = 0;
  while (index !== -1) {
    const after = index + needle.length;
    const next = content[after] ?? "";
    const nextOk =
      next === "" ||
      FOLLOW_OK.has(next) ||
      (escaped && next === "\\" && content[after + 1] === "/");
    if (nextOk) {
      result += content.slice(cursor, index) + replacement;
      cursor = after;
    }
    index = content.indexOf(needle, index + 1);
  }
  result += content.slice(cursor);
  return result;
}

const RELATIVE_SPEC = /(?<=\bfrom\s*|import\(|require\()['"](\.[^'"]*)['"]/g;

// Rewrite relative imports in `relFile` (its current path). `oldPath`/`newPath`
// describe the move; a file inside the moved tree resolves specifiers against
// its pre-move location and keeps pointing at its co-moved neighbours, while a
// file outside the tree gets its depth recomputed.
export function rewriteRelativeImports(content, relFile, oldPath, newPath) {
  const oldRel = relFile === newPath || relFile.startsWith(`${newPath}/`) ? oldPath + relFile.slice(newPath.length) : relFile;
  const dirNow = path.posix.dirname(relFile);
  const dirOld = path.posix.dirname(oldRel);
  const moved = dirNow !== dirOld;
  let changed = content;
  for (const match of [...content.matchAll(RELATIVE_SPEC)]) {
    const specifier = match[1];
    const withoutExt = specifier.replace(/\.(ts|tsx|js|jsx|mjs)$/, "");
    const absOld = path.posix.normalize(path.posix.join(dirOld, withoutExt));
    const target = absOld === oldPath || absOld.startsWith(`${oldPath}/`)
      ? path.posix.normalize(newPath + absOld.slice(oldPath.length))
      : absOld;
    // Idempotence: a file inside the moved tree may already carry the
    // post-move depth. If resolving the specifier against the CURRENT
    // directory reaches the same target, the text is already correct;
    // re-deriving from the stale pre-move depth would climb one "../"
    // further on every pass.
    if (moved) {
      const absNow = path.posix.normalize(path.posix.join(dirNow, withoutExt));
      if (absNow === target) continue;
    }
    let relative = path.posix.relative(dirNow, target);
    if (!relative.startsWith(".")) relative = `./${relative}`;
    const next = relative + specifier.slice(withoutExt.length);
    if (next !== specifier) {
      changed = changed.split(`'${specifier}'`).join(`'${next}'`).split(`"${specifier}"`).join(`"${next}"`);
    }
  }
  return changed;
}

function shimContent(newPath) {
  const withoutExt = newPath.replace(/\.tsx?$/, "");
  return `export { default } from "@/${withoutExt}";\n`;
}

function moveCommand(options) {
  const root = options.root ?? path.resolve(import.meta.dirname, "..");
  validatePairs(root, options.pairs);
  const ledger = readLedger(root);

  for (const [oldPath, newPath] of options.pairs) {
    gitMove(root, oldPath, newPath);
    const isRouteEntry =
      options.shim && oldPath.startsWith("app/") && (oldPath.endsWith("page.tsx") || oldPath.endsWith("layout.tsx"));
    if (isRouteEntry) {
      writeFileSync(path.posix.join(root, oldPath), shimContent(newPath));
    } else {
      ledger.push([oldPath, newPath]);
    }
  }

  const files = gitLsFiles(root) ?? listFiles(root, []);
  const rewriteable = files.filter(
    (file) =>
      file !== LEDGER_NAME &&
      !REWRITE_EXCLUDED.has(file) &&
      REWRITE_EXTS.has(path.posix.extname(file)) &&
      existsSync(path.posix.join(root, file)),
  );
  for (const [oldPath, newPath] of options.pairs) {
    for (const relFile of rewriteable) {
      rewriteFile(root, relFile, oldPath, newPath);
    }
  }
  writeLedger(root, ledger);
  for (const [oldPath, newPath] of options.pairs) {
    process.stdout.write(`moved ${oldPath} -> ${newPath}\n`);
  }
}

function rewriteFile(root, relFile, oldPath, newPath) {
  const full = path.posix.join(root, relFile);
  const before = readFileSync(full, "utf8");
  let content = replaceAlias(before, oldPath, newPath);
  content = replaceAlias(content, oldPath, newPath, true);
  content = replaceBounded(content, oldPath, newPath);
  content = replaceBounded(content, oldPath, newPath, true);
  content = rewriteRelativeImports(content, relFile, oldPath, newPath);
  if (content !== before) writeFileSync(full, content);
}

function rewriteCommand(options) {
  const root = options.root ?? path.resolve(import.meta.dirname, "..");
  const ledger = readLedger(root);
  if (ledger.length === 0) {
    process.stdout.write("Move ledger is empty; nothing to rewrite.\n");
    return;
  }
  const files = gitLsFiles(root) ?? listFiles(root, []);
  const rewriteable = files.filter(
    (file) =>
      file !== LEDGER_NAME &&
      !REWRITE_EXCLUDED.has(file) &&
      REWRITE_EXTS.has(path.posix.extname(file)) &&
      existsSync(path.posix.join(root, file)),
  );
  for (const [oldPath, newPath] of ledger) {
    for (const relFile of rewriteable) {
      rewriteFile(root, relFile, oldPath, newPath);
    }
  }
  process.stdout.write(`Rewrote references for ${ledger.length} recorded ${ledger.length === 1 ? "move" : "moves"}.\n`);
}

function checkCommand(options) {
  const root = options.root ?? path.resolve(import.meta.dirname, "..");
  const ledger = readLedger(root);
  const problems = [];
  for (const [oldPath] of ledger) {
    if (existsSync(path.posix.join(root, oldPath))) {
      problems.push(`ledgered module still at its old path: ${oldPath}`);
    }
  }
  if (problems.length > 0) {
    for (const problem of problems) process.stderr.write(`${problem}\n`);
    process.exit(1);
  }
  process.stdout.write(`Move ledger clean: ${ledger.length} recorded ${ledger.length === 1 ? "move" : "moves"}.\n`);
}

const options = parseArgs(process.argv.slice(2));
if (options.command === "move") {
  moveCommand(options);
} else if (options.command === "rewrite") {
  rewriteCommand(options);
} else {
  checkCommand(options);
}
