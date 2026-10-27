import { test } from "node:test";
import assert from "node:assert/strict";
import {
  existsSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const dashboard = fileURLToPath(new URL("../", import.meta.url));
const repo = path.resolve(dashboard, "../..");
const playwright = path.join(dashboard, "node_modules/@playwright/test");

test("a failed journey's attachments survive the subsequent failed contract run in the CI upload paths", () => {
  const workflow = readFileSync(
    path.join(repo, ".github/workflows/dashboard-journeys.yml"),
    "utf8",
  );
  const journey = workflow.match(
    /e2e --shard=.*? --output=\$\{\{ runner.temp \}\}\/([^\s]+)/,
  )?.[1];
  const contract = workflow.match(
    /e2e:contracts --output=\$\{\{ runner.temp \}\}\/([^\s]+)/,
  )?.[1];
  assert.ok(journey, "Journeys must declare their artifact directory");
  assert.ok(contract, "Contracts must declare their artifact directory");
  assert.notEqual(
    journey,
    contract,
    "The second Playwright invocation must not erase the first output",
  );
  for (const output of [journey, contract]) {
    const step = workflow
      .split(/\n      - name:/)
      .find((step) => step.includes("path: ${{ runner.temp }}/" + output));
    assert.ok(step, `Missing upload for ${output}`);
    assert.match(step, /if: always\(\)/);
    assert.match(step, /uses: actions\/upload-artifact@/);
  }
  for (const config of [
    "playwright.config.ts",
    "playwright.contracts.config.ts",
  ])
    assert.ok(
      readFileSync(path.join(dashboard, config), "utf8").includes(
        "./e2e/support/artifact-reporter.ts",
      ),
    );
  const root = mkdtempSync(path.join(tmpdir(), "dashboard-failure-artifacts-"));
  const attached = [];
  try {
    for (const [layer, output] of [
      ["journey", journey],
      ["contract", contract],
    ]) {
      const config = path.join(root, "playwright.config.cjs");
      const report = path.join(root, `${layer}.json`);
      writeFileSync(
        config,
        `module.exports = ${JSON.stringify({
          testDir: root,
          outputDir: path.join(root, output),
          workers: 1,
          retries: 0,
          reporter: [
            [path.join(dashboard, "e2e/support/artifact-reporter.ts")],
            ["json", { outputFile: report }],
          ],
        })};`,
      );
      writeFileSync(
        path.join(root, "failure.spec.cjs"),
        `
const {test,expect} = require(${JSON.stringify(playwright)});
test('synthetic failure evidence',async({},info)=>{
  await info.attach('worker.log',{body:Buffer.from(${JSON.stringify(`${layer}: synthetic worker failure`)}),contentType:'text/plain'});
  await info.attach('screenshot.png',{body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==','base64'),contentType:'image/png'});
  expect('deliberate protocol failure').toBe('success');
});
`,
      );
      const result = spawnSync(
        process.execPath,
        [path.join(playwright, "cli.js"), "test", "--config", config],
        { encoding: "utf8" },
      );
      assert.equal(result.status, 1, result.stdout + result.stderr);
      const data = JSON.parse(readFileSync(report, "utf8"));
      const attachments =
        data.suites[0].specs[0].tests[0].results[0].attachments;
      for (const name of ["worker.log", "screenshot.png"])
        assert.ok(
          attachments.some((item) => item.name === name),
          `Missing ${name}`,
        );
      for (const attachment of attachments) {
        assert.ok(
          attachment.path.startsWith(path.join(root, output) + path.sep),
        );
        assert.ok(existsSync(attachment.path));
        attached.push(attachment.path);
      }
      assert.equal(
        readFileSync(
          attachments.find((item) => item.name === "worker.log").path,
          "utf8",
        ),
        `${layer}: synthetic worker failure`,
      );
    }
    // A second invocation has now cleaned its own output directory. Both sets
    // of normal Playwright attachments must still be available to the uploads.
    assert.ok(attached.every((file) => existsSync(file)));
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
