import {
  existsSync,
  copyFileSync,
  mkdirSync,
  rmSync,
  readdirSync,
} from "node:fs";
import { spawnSync } from "node:child_process";

// Keep child-process coverage separate: legacy suites replace Node's module
// loader, while native Vitest suites use Vite's transform and V8 AST remapping.
const externalReport = process.env.DASHBOARD_UNIT_REPORT;
const legacyReport = "coverage/legacy-tests.json";
rmSync("coverage", { recursive: true, force: true });
rmSync(".test-out", { recursive: true, force: true });
mkdirSync("coverage", { recursive: true });
function run(command, args, env = process.env) {
  const result = spawnSync(command, args, { stdio: "inherit", env });
  if (result.status !== 0)
    throw new Error(`${command} failed: ${result.error ?? result.status}`);
}
try {
  run("tsc", ["-p", "tsconfig.test.json"]);
  run(process.execPath, [
    "--test",
    ...readdirSync("scripts")
      .filter((file) => file.endsWith(".test.mjs"))
      .sort()
      .map((file) => `scripts/${file}`),
  ]);
  run(
    "c8",
    ["--config", "c8.config.json", "node", "scripts/run-unit-tests.mjs"],
    {
      ...process.env,
      DASHBOARD_UNIT_REPORT: legacyReport,
    },
  );
  run("vitest", [
    "run",
    "--coverage",
    "--reporter=default",
    "--reporter=json",
    "--outputFile=coverage/vitest-tests.json",
  ]);
  run(process.execPath, [
    "scripts/check-unit-coverage.mjs",
    ...process.argv.slice(2),
  ]);
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
} finally {
  if (externalReport && existsSync(legacyReport))
    copyFileSync(legacyReport, externalReport);
  rmSync(".test-out", { recursive: true, force: true });
}
