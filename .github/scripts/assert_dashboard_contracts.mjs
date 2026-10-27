import { appendFileSync, readFileSync } from "node:fs";

// Collection changes require a reviewed manifest edit, just as reducing the
// executed-journey floor requires a reviewed change to that gate.
const manifest = JSON.parse(
  readFileSync(new URL("./dashboard_contracts.json", import.meta.url), "utf8"),
);
const problems = [];
let contracts = [];
let executed = 0;
let skippedNames = [];
try {
  if (
    manifest.schemaVersion !== 1 ||
    !Array.isArray(manifest.contracts) ||
    !manifest.contracts.length ||
    manifest.contracts.some((name) => typeof name !== "string" || !name) ||
    new Set(manifest.contracts).size !== manifest.contracts.length
  )
    throw new Error("Invalid browser contract collection manifest");
  const files = process.argv.slice(2);
  if (files.length !== 1)
    throw new Error(
      `Expected 1 complete contract report; received ${files.length}.`,
    );
  const report = JSON.parse(readFileSync(files[0], "utf8"));
  if (
    report.schemaVersion !== 1 ||
    report.layer !== "contract" ||
    report.shard !== null ||
    !Array.isArray(report.contracts) ||
    !Array.isArray(report.integrityErrors) ||
    !Array.isArray(report.skippedNames)
  )
    throw new Error("Invalid browser contract report");
  contracts = report.contracts;
  const names = new Set();
  const ids = new Set();
  for (const contract of contracts) {
    if (
      typeof contract.id !== "string" ||
      !contract.id ||
      ids.has(contract.id) ||
      typeof contract.name !== "string" ||
      !contract.name ||
      names.has(contract.name)
    )
      throw new Error("Missing or duplicate browser contract identity");
    ids.add(contract.id);
    names.add(contract.name);
    if (!manifest.contracts.includes(contract.name))
      problems.push(`Unreviewed contract collection entry: ${contract.name}`);
    if (contract.status === "skipped") skippedNames.push(contract.name);
    if (
      ["passed", "failed", "timedOut", "interrupted"].includes(contract.status)
    )
      executed++;
    if (contract.status !== "passed" || contract.expectedStatus !== "passed")
      problems.push(
        `Contract did not execute and pass: ${contract.name} (${contract.status}, expected ${contract.expectedStatus})`,
      );
  }
  for (const name of manifest.contracts)
    if (!names.has(name)) problems.push(`Missing required contract: ${name}`);
  if (
    report.discovered !== contracts.length ||
    report.executed !== executed ||
    report.skipped !== skippedNames.length ||
    JSON.stringify(report.skippedNames) !== JSON.stringify(skippedNames)
  )
    problems.push("Contract counts disagree with named results");
  if (report.status !== "passed" || report.integrityErrors.length)
    problems.push(
      "Unsuccessful browser contract suite or reporter integrity error",
    );
  if (skippedNames.length)
    problems.push("The required browser contract suite permits no skips");
} catch (error) {
  problems.push(error.message);
}
const summary = [
  `Dashboard browser contracts: ${manifest.contracts.length} required, ${contracts.length} reported, ${executed} executed, ${skippedNames.length} skipped.`,
  "",
  "Skipped contracts:",
  "",
  ...(skippedNames.length
    ? skippedNames.map((name) => `- ${name}`)
    : ["- None"]),
  ...problems.map((problem) => `ERROR: ${problem}`),
].join("\n");
console.log(summary);
if (process.env.GITHUB_STEP_SUMMARY)
  appendFileSync(process.env.GITHUB_STEP_SUMMARY, `${summary}\n`);
if (problems.length) process.exitCode = 1;
