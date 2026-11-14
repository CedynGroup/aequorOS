/**
 * Shared harness for the plugin tests: every case writes a fixture file and a
 * throwaway .oxlintrc.json into a fresh temp directory and runs the REAL
 * pinned oxlint binary against it, exactly the way a consuming package would.
 */
import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

const PACKAGE_ROOT = resolve(import.meta.dirname, "..");
const PLUGIN_PATH = join(PACKAGE_ROOT, "index.js");
const OXLINT_BIN = join(PACKAGE_ROOT, "node_modules", ".bin", "oxlint");

/** The three rules, keyed for the harness. */
export const RULES = {
  frozenTime: {
    id: "acts/require-frozen-time",
    name: "require-frozen-time",
    code: "ACTS001",
  },
  weakAwaits: {
    id: "acts/no-weak-test-awaits",
    name: "no-weak-test-awaits",
    code: "ACTS002",
  },
  e2eStability: {
    id: "acts/prefer-e2e-stability",
    name: "prefer-e2e-stability",
    code: "ACTS003",
  },
};

/**
 * Run oxlint with the given acts rule keys enabled over `code`.
 * Returns `{ exitCode, output }`; a flagged fixture exits non-zero.
 */
export async function runOxlint(ruleKeys, code) {
  const tempDir = await mkdtemp(join(tmpdir(), "oxlint-plugins-test-"));
  try {
    const fixtureFile = join(tempDir, `fixture-${randomUUID()}.test.ts`);
    const configFile = join(tempDir, ".oxlintrc.json");

    const rules = {};
    for (const key of ruleKeys) {
      rules[RULES[key].id] = "error";
    }

    const config = {
      $schema: "https://oxc.rs/oxlint/schema.json",
      plugins: [],
      jsPlugins: [PLUGIN_PATH],
      categories: {},
      rules,
    };

    await writeFile(configFile, JSON.stringify(config));
    await writeFile(fixtureFile, code);

    try {
      const { stdout, stderr } = await execFileAsync(OXLINT_BIN, ["-c", configFile, fixtureFile], {
        timeout: 30_000,
        maxBuffer: 1024 * 1024,
      });
      return { exitCode: 0, output: stdout + stderr };
    } catch (error) {
      const execError = /** @type {{ code?: number; stdout?: string; stderr?: string }} */ (error);
      return {
        exitCode: execError.code ?? 1,
        output: (execError.stdout ?? "") + (execError.stderr ?? ""),
      };
    }
  } finally {
    await rm(tempDir, { recursive: true, force: true });
  }
}

/** Assert the fixture is flagged with the given ACTS code and rule. */
export function assertFlagged(result, key) {
  const { code, name } = RULES[key];
  if (result.exitCode === 0 || !result.output.includes(`(${name})`)) {
    throw new Error(
      `expected ${name} to fire (exit ${result.exitCode}), got: ${result.output || "(no output)"}`
    );
  }
  if (!result.output.includes(code)) {
    throw new Error(`expected message prefix ${code} in oxlint output, got: ${result.output}`);
  }
}

/** Assert the fixture passes oxlint cleanly (no finding for the rule). */
export function assertClean(result, key) {
  const { name } = RULES[key];
  if (result.exitCode !== 0) {
    throw new Error(`expected a clean run, exit ${result.exitCode}: ${result.output}`);
  }
  if (result.output.includes(`(${name})`)) {
    throw new Error(`expected no ${name} finding, got: ${result.output}`);
  }
}
