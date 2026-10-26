import { readFileSync, appendFileSync } from "node:fs";

// The required suite permits no skips. Ratchet this executed-journey floor up
// when adding coverage; reducing it requires a reviewed suite change.
// Critical scenario results are checked separately by assert_dashboard_outcomes.mjs.
const floor = 213;
const files = process.argv.slice(2);
const problems = [];
const ids = new Set();
const shards = new Set();
const skippedNames = [];
let discovered = 0;
let executed = 0;
try {
  if (files.length !== 4)
    throw new Error(`Expected 4 shard reports; received ${files.length}.`);
  for (const file of files) {
    const report = JSON.parse(readFileSync(file, "utf8"));
    if (
      !report.shard ||
      report.shard.total !== 4 ||
      ![1, 2, 3, 4].includes(report.shard.current) ||
      shards.has(report.shard.current)
    ) {
      throw new Error(`Missing or duplicate shard identity: ${file}`);
    }
    shards.add(report.shard.current);
    if (
      !Array.isArray(report.journeys) ||
      !Array.isArray(report.integrityErrors)
    )
      throw new Error(`Invalid report: ${file}`);
    let ran = 0;
    const skipped = [];
    for (const journey of report.journeys) {
      if (
        typeof journey.id !== "string" ||
        !journey.id ||
        ids.has(journey.id) ||
        typeof journey.name !== "string" ||
        !journey.name
      )
        throw new Error(`Missing or duplicate journey identity: ${file}`);
      ids.add(journey.id);
      if (journey.status === "skipped") skipped.push(journey.name);
      else {
        ran += 1;
        if (journey.status !== "passed")
          problems.push(
            `Journey did not pass: ${journey.name} (${journey.status})`,
          );
      }
    }
    skippedNames.push(...skipped);
    if (
      report.discovered !== report.journeys.length ||
      report.executed !== ran ||
      report.skipped !== skipped.length ||
      JSON.stringify(report.skippedNames) !== JSON.stringify(skipped)
    )
      problems.push(`Counts disagree with named results: ${file}`);
    if (
      report.status !== "passed" ||
      report.quarantinedExecuted !== 0 ||
      report.quarantineSize !== 0 ||
      report.integrityErrors.length
    )
      problems.push(`Unsuccessful or quarantined shard: ${file}`);
    discovered += report.journeys.length;
    executed += ran;
  }
  if (executed + skippedNames.length < floor)
    problems.push(
      `Only ${executed + skippedNames.length} completed journeys; floor is ${floor}.`,
    );
  if (executed < floor)
    problems.push(`Only ${executed} journeys executed; floor is ${floor}.`);
  if (skippedNames.length)
    problems.push(
      `The required suite permits no skips (${skippedNames.length} skipped).`,
    );
} catch (error) {
  problems.push(error.message);
}
const summary = [
  `Dashboard journeys: ${discovered} discovered, ${executed} executed, ${skippedNames.length} skipped.`,
  "",
  "Skipped journeys:",
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
