#!/usr/bin/env node
// Feature-boundary guard for the dashboard (see FEATURE_LAYOUT.md).
//
// Ratchet semantics mirror the backend feature layout: a violation that is not
// in the baseline fails, and a baseline entry that no longer occurs also fails,
// so feature_boundary_baseline.json only ever shrinks. Regenerate it with
// --update and review that the diff only deletes entries (a move PR may add
// entries for imports that were internal before the moved code gained an
// owner; that addition is reviewed by hand in the move PR).
//
// Foundation scope: only files under features/ are governed. Code that has not
// moved into a feature yet keeps its current imports; once a feature owns a
// module (features/<name>/, lib/<name>/ or components/<name>/), imports of it
// from another feature must go through features/<name>/public.
import { existsSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import process from "node:process";

const SKIP_DIRS = new Set([
  "node_modules",
  "coverage",
  ".test-out",
  "test-results",
  "playwright-report",
]);
const SOURCE_EXTS = new Set([".ts", ".tsx"]);
// The shared layer every feature may import (the kernel analog): the generated
// API client, shared UI primitives and shared charts. Root-level lib modules
// are shared until a feature owns them.
const SHARED_PREFIXES = ["lib/api/", "components/ui/", "components/charts/", "components/shell/"];

export function parseArgs(argv) {
  const options = { root: null, baseline: null, update: false, quiet: false };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--update") {
      options.update = true;
    } else if (arg === "--quiet") {
      options.quiet = true;
    } else if (arg === "--root" || arg === "--baseline") {
      i += 1;
      if (i >= argv.length) throw new Error(`${arg} requires a value`);
      options[arg.slice(2)] = argv[i];
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }
  return options;
}

function listFiles(dir, out) {
  for (const entry of readdirSync(dir).sort()) {
    if (entry.startsWith(".") || SKIP_DIRS.has(entry)) continue;
    const full = path.posix.join(dir, entry);
    if (statSync(full).isDirectory()) {
      listFiles(full, out);
    } else if (SOURCE_EXTS.has(path.posix.extname(entry))) {
      out.push(full);
    }
  }
  return out;
}

function featureNames(root) {
  const dir = path.posix.join(root, "features");
  if (!existsSync(dir)) return new Set();
  return new Set(readdirSync(dir).filter((name) => statSync(path.posix.join(dir, name)).isDirectory()));
}

// Ownership of a dashboard-relative module path (no extension):
//   features/<name>/**          -> that feature
//   lib/api/**, components/{ui,charts,shell}/** -> "shared"
//   lib/<name>/**, components/<name>/** when <name> is a feature -> that feature
//   lib/<file> (a root-level lib module, not a directory) -> "shared"
//   anything else               -> null (unowned legacy; importing it from a
//                                  feature is a baseline-recorded violation)
export function ownerOf(relModule, features, root) {
  if (relModule.startsWith("features/")) {
    const name = relModule.split("/")[1];
    if (features.has(name)) return name;
  }
  const [first, second] = relModule.split("/");
  if (first === "app") return "app";
  if (SHARED_PREFIXES.some((prefix) => relModule.startsWith(prefix))) return "shared";
  if ((first === "lib" || first === "components") && second !== undefined && features.has(second)) {
    return second;
  }
  if (first === "lib" && second === undefined) return "shared";
  // A root-level lib file (lib/format.ts) is shared; a lib subdirectory that
  // no feature owns yet is unowned legacy.
  if (first === "lib" && second !== undefined) {
    const dir = existsSync(path.posix.join(root, relModule)) && statSync(path.posix.join(root, relModule)).isDirectory();
    return dir ? null : "shared";
  }
  return null;
}

// Resolve an import specifier from a file to a dashboard-relative module path
// without extension, or null when the specifier is external/unresolvable.
export function resolveSpecifier(root, importerRel, specifier) {
  let base;
  if (specifier.startsWith("@/")) {
    base = specifier.slice(2);
  } else if (specifier.startsWith(".")) {
    base = path.posix.normalize(path.posix.join(path.posix.dirname(importerRel), specifier));
  } else {
    return null;
  }
  const candidates = [
    base,
    `${base}.ts`,
    `${base}.tsx`,
    path.posix.join(base, "index.ts"),
    path.posix.join(base, "index.tsx"),
  ];
  for (const candidate of candidates) {
    if (existsSync(path.posix.join(root, candidate)) && statSync(path.posix.join(root, candidate)).isFile()) {
      return candidate.replace(/\.tsx?$/, "");
    }
  }
  return null;
}

const IMPORT_PATTERNS = [
  /(?:^|\n)\s*(?:import|export)\s+(?:type\s+)?[^'"();]*?\bfrom\s*['"]([^'"]+)['"]/g,
  /\bimport\(\s*['"]([^'"]+)['"]\s*\)/g,
  /(?:^|\n)\s*import\s*['"]([^'"]+)['"]/g,
];

function importsOfFile(source) {
  const specifiers = new Set();
  for (const pattern of IMPORT_PATTERNS) {
    for (const match of source.matchAll(pattern)) specifiers.add(match[1]);
  }
  return specifiers;
}

function readBaseline(file) {
  if (!existsSync(file)) return { violations: [] };
  const parsed = JSON.parse(readFileSync(file, "utf8"));
  if (!Array.isArray(parsed.violations)) throw new Error(`Malformed baseline: ${file}`);
  return parsed;
}

export function scan(root, baseline) {
  const features = featureNames(root);
  const current = [];
  const featuresDir = path.posix.join(root, "features");
  const files = features.size > 0 && existsSync(featuresDir) ? listFiles(featuresDir, []) : [];
  for (const file of files) {
    const relFile = path.posix.relative(root, file);
    const owner = relFile.split("/")[1];
    for (const specifier of importsOfFile(readFileSync(file, "utf8"))) {
      const target = resolveSpecifier(root, relFile, specifier);
      if (target === null) continue;
      const targetOwner = ownerOf(target, features, root);
      if (targetOwner === owner || targetOwner === "shared" || targetOwner === "app") continue;
      const isPublicInterface = new RegExp(`^features/${targetOwner}/public$`).test(target);
      if (targetOwner !== null && isPublicInterface) continue;
      current.push(`${relFile} -> ${target}`);
    }
  }
  current.sort();
  const recorded = [...baseline.violations].sort();
  const fresh = current.filter((entry) => !recorded.includes(entry));
  const stale = recorded.filter((entry) => !current.includes(entry));
  return { current, fresh, stale, features: [...features].sort() };
}

function main() {
  const options = parseArgs(process.argv.slice(2));
  const root = options.root ?? path.resolve(import.meta.dirname, "..");
  const baselineFile = options.baseline ?? path.posix.join(root, "feature_boundary_baseline.json");
  const baseline = readBaseline(baselineFile);
  const { current, fresh, stale, features } = scan(root, baseline);

  if (options.update) {
    writeFileSync(
      baselineFile,
      `${JSON.stringify({ violations: current }, null, 2)}\n`,
    );
    process.stdout.write(
      `Wrote ${current.length} ${current.length === 1 ? "violation" : "violations"} to ${path.relative(process.cwd(), baselineFile)}\n`,
    );
    return;
  }

  if (features.length === 0 && !options.quiet) {
    process.stdout.write("No features yet; the boundary guard had nothing to scan.\n");
  }
  let failed = false;
  for (const entry of fresh) {
    failed = true;
    process.stderr.write(`New feature-boundary violation: ${entry}\n`);
  }
  for (const entry of stale) {
    failed = true;
    process.stderr.write(`Stale baseline entry (run --update): ${entry}\n`);
  }
  if (failed) {
    process.stderr.write(
      `Feature-boundary check failed: ${fresh.length} new, ${stale.length} stale (see FEATURE_LAYOUT.md).\n`,
    );
    process.exitCode = 1;
  } else if (!options.quiet) {
    process.stdout.write(
      `Feature-boundary check passed: ${current.length} recorded ${current.length === 1 ? "violation" : "violations"} across ${features.length} ${features.length === 1 ? "feature" : "features"}.\n`,
    );
  }
}

if (import.meta.filename === process.argv[1] || process.argv[1]?.endsWith("check_feature_boundaries.mjs")) {
  main();
}
