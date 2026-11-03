import {
  readFileSync,
  readdirSync,
  mkdirSync,
  writeFileSync,
  existsSync,
} from "node:fs";
import path from "node:path";
import coverage from "istanbul-lib-coverage";

const args = process.argv.slice(2);
const rootIndex = args.indexOf("--root");
const root = path.resolve(rootIndex < 0 ? process.cwd() : args[rootIndex + 1]);
const update = args.includes("--update");
const baselinePath = path.join(root, "unit-coverage-baseline.json");
const read = (file) => JSON.parse(readFileSync(path.join(root, file), "utf8"));
const roots = ["app", "components", "features", "lib"];
function inventory(directory) {
  if (!existsSync(path.join(root, directory))) return [];
  return readdirSync(path.join(root, directory), {
    withFileTypes: true,
  }).flatMap((entry) => {
    const file = `${directory}/${entry.name}`;
    if (entry.isDirectory()) return inventory(file);
    return /\.(ts|tsx)$/.test(file) && !/\.(test|spec|d)\.(ts|tsx)$/.test(file)
      ? [file]
      : [];
  });
}
function lineMaps(report) {
  const result = new Map();
  for (const [key, value] of Object.entries(report)) {
    const file = path.relative(root, key).split(path.sep).join("/");
    if (path.resolve(root, value.path) !== path.resolve(key))
      throw new Error(`Coverage path mismatch: ${key}`);
    if (result.has(file)) throw new Error(`Duplicate coverage file: ${file}`);
    if (
      !value.statementMap ||
      !value.s ||
      Object.keys(value.s).length !== Object.keys(value.statementMap).length
    ) {
      throw new Error(`Invalid statements: ${file}`);
    }
    for (const [id, count] of Object.entries(value.s)) {
      if (
        !Number.isSafeInteger(count) ||
        count < 0 ||
        !Number.isSafeInteger(value.statementMap[id]?.start?.line) ||
        value.statementMap[id].start.line < 1
      ) {
        throw new Error(`Invalid coverage counter: ${file}`);
      }
    }
    result.set(file, coverage.createFileCoverage(value).getLineCoverage());
  }
  return result;
}
function collection() {
  const legacy = read("coverage/legacy-tests.json");
  if (
    !Array.isArray(legacy.discovered) ||
    legacy.discovered.length < 55 ||
    new Set(legacy.discovered).size !== legacy.discovered.length ||
    JSON.stringify(legacy.discovered) !== JSON.stringify(legacy.executed) ||
    !Array.isArray(legacy.failed) ||
    legacy.failed.length
  )
    throw new Error("Legacy unit collection did not complete all 55+ files");
  const native = read("coverage/vitest-tests.json");
  if (
    !Number.isSafeInteger(native.numTotalTests) ||
    native.numTotalTests < 8 ||
    native.numPassedTests !== native.numTotalTests ||
    native.numFailedTests !== 0 ||
    native.numPendingTests !== 0 ||
    native.numTodoTests !== 0 ||
    native.success !== true
  ) {
    throw new Error(
      "Vitest collection failed, skipped tests or fell below 8 tests",
    );
  }
  return {
    legacyFiles: legacy.executed.length,
    vitestTests: native.numPassedTests,
  };
}

try {
  const tests = collection();
  const files = roots.flatMap(inventory).sort();
  const native = lineMaps(read("coverage/vitest/coverage-final.json"));
  const legacy = lineMaps(read("coverage/legacy/coverage-final.json"));
  if (
    !files.length ||
    native.size !== files.length ||
    files.some((file) => !native.has(file))
  ) {
    throw new Error(
      "Vitest coverage must include every production TS/TSX file in app/, components/, features/ and lib/",
    );
  }
  if (!legacy.size || [...legacy.keys()].some((file) => !native.has(file)))
    throw new Error(
      "Legacy coverage is empty or contains files outside the production inventory",
    );
  const summary = {
    ...tests,
    files: {},
    total: { covered: 0, uncovered: 0, lines: 0 },
  };
  const lcov = [];
  for (const file of files) {
    const canonical = native.get(file);
    const old = legacy.get(file) ?? {};
    const entries = Object.entries(canonical).map(([line, hits]) => [
      Number(line),
      hits > 0 || old[line] > 0 ? 1 : 0,
    ]);
    const covered = entries.filter(([, hit]) => hit > 0).length;
    const lines = entries.length;
    summary.files[file] = { covered, uncovered: lines - covered, lines };
    summary.total.covered += covered;
    summary.total.lines += lines;
    summary.total.uncovered += lines - covered;
    lcov.push(
      `TN:\nSF:${file}\n${entries.map(([line, hit]) => `DA:${line},${hit}`).join("\n")}\nLF:${lines}\nLH:${covered}\nend_of_record`,
    );
  }
  if (!summary.total.lines || !summary.total.covered)
    throw new Error("Coverage report contains no exercised production lines");
  summary.total.percent = Number(
    ((100 * summary.total.covered) / summary.total.lines).toFixed(2),
  );
  mkdirSync(path.join(root, "coverage/combined"), { recursive: true });
  writeFileSync(
    path.join(root, "coverage/combined/summary.json"),
    `${JSON.stringify(summary, null, 2)}\n`,
  );
  writeFileSync(
    path.join(root, "coverage/combined/lcov.info"),
    `${lcov.join("\n")}\n`,
  );
  const text = `${files.length} production files: ${summary.total.covered}/${summary.total.lines} lines covered (${summary.total.percent}%), ${summary.total.uncovered} uncovered; ${tests.legacyFiles} legacy files + ${tests.vitestTests} Vitest tests.\n`;
  console.log(text.trim());
  writeFileSync(path.join(root, "coverage/combined/summary.txt"), text);
  const baseline = existsSync(baselinePath)
    ? read("unit-coverage-baseline.json")
    : null;
  if (baseline) {
    if (
      baseline.version !== 1 ||
      !Number.isSafeInteger(baseline.maxUncovered) ||
      baseline.maxUncovered < 0 ||
      !baseline.files ||
      Object.values(baseline.files).some(
        (value) => !Number.isSafeInteger(value) || value < 0,
      ) ||
      Object.values(baseline.files).reduce((sum, value) => sum + value, 0) !==
        baseline.maxUncovered
    ) {
      throw new Error("Invalid coverage baseline");
    }
    const regressions = files.filter(
      (file) => summary.files[file].uncovered > (baseline.files[file] ?? 0),
    );
    if (summary.total.uncovered > baseline.maxUncovered || regressions.length) {
      throw new Error(
        `Uncovered lines may only shrink (total ceiling ${baseline.maxUncovered}): ${regressions.map((file) => `${file}: ${summary.files[file].uncovered} > ${baseline.files[file] ?? 0}`).join(", ")}`,
      );
    }
  } else if (!update)
    throw new Error(
      "Missing coverage baseline; establish it with coverage:ratchet and review it",
    );
  if (update) {
    const next = {
      version: 1,
      maxUncovered: summary.total.uncovered,
      files: Object.fromEntries(
        files.map((file) => [file, summary.files[file].uncovered]),
      ),
    };
    writeFileSync(baselinePath, `${JSON.stringify(next, null, 2)}\n`);
    console.log("Coverage baseline tightened; review and commit it.");
  }
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
