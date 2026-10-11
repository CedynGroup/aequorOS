import { readdirSync, existsSync, writeFileSync } from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

const args = process.argv.slice(2);
const option = (name, fallback) => {
  const index = args.indexOf(name);
  return index < 0 ? fallback : args[index + 1];
};
const root = path.resolve(option("--root", process.cwd()));
const minimum = Number(option("--min-files", "55"));
const reportPath = process.env.DASHBOARD_UNIT_REPORT;
const report = { discovered: [], executed: [], failed: [] };

function discover(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    if (
      entry.isDirectory() &&
      (entry.name === "node_modules" ||
        entry.name === ".test-out" ||
        entry.name === ".git" ||
        entry.name.startsWith(".next"))
    )
      return [];
    const location = path.join(directory, entry.name);
    if (entry.isDirectory()) return discover(location);
    return /\.test\.tsx?$/.test(entry.name)
      ? [path.relative(root, location)]
      : [];
  });
}

try {
  report.discovered = discover(root).sort();
  if (
    !Number.isInteger(minimum) ||
    minimum < 1 ||
    report.discovered.length < minimum
  ) {
    throw new Error(
      `Collected ${report.discovered.length} unit-test files; expected at least ${minimum}.`,
    );
  }
  const compiled = report.discovered.map((file) =>
    path.join(root, ".test-out", file.replace(/\.tsx?$/, ".js")),
  );
  const missing = report.discovered.filter(
    (_, index) => !existsSync(compiled[index]),
  );
  if (missing.length)
    throw new Error(
      `Unit tests excluded from compilation: ${missing.join(", ")}`,
    );
  console.log(
    `Collected all ${report.discovered.length} dashboard *.test.ts(x) files.`,
  );
  for (const [index, file] of report.discovered.entries()) {
    console.log(`Running ${file}`);
    report.executed.push(file);
    const result = spawnSync(process.execPath, [compiled[index]], {
      cwd: root,
      stdio: "inherit",
    });
    if (result.status !== 0) {
      report.failed.push(file);
      throw new Error(
        `Unit test failed: ${file}${result.error ? ` (${result.error.message})` : ""}`,
      );
    }
  }
  console.log(
    `Executed all ${report.executed.length} collected unit-test files.`,
  );
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
} finally {
  if (reportPath)
    writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`);
}
