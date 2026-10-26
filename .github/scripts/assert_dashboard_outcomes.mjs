import { readFileSync, appendFileSync } from "node:fs";

// The catalogue owns stable outcome identities; titles remain diagnostic only.
const catalogueUrl = new URL(
  "./dashboard_critical_outcomes.json",
  import.meta.url,
);
const problems = [];
const seen = new Set();
let required = [];
const nonempty = (value) =>
  typeof value === "string" && value.trim().length > 0;
try {
  const catalogue = JSON.parse(readFileSync(catalogueUrl, "utf8"));
  if (
    catalogue.schemaVersion !== 1 ||
    !Array.isArray(catalogue.outcomes) ||
    !catalogue.outcomes.length
  )
    throw new Error("Invalid critical-outcome catalogue.");
  required = catalogue.outcomes;
  const byId = new Map();
  for (const outcome of required) {
    if (
      !outcome ||
      !nonempty(outcome.id) ||
      !nonempty(outcome.description) ||
      !["journey", "contract"].includes(outcome.layer) ||
      byId.has(outcome.id)
    )
      throw new Error("Invalid or duplicate catalogue outcome.");
    byId.set(outcome.id, outcome);
  }

  const files = process.argv.slice(2);
  if (files.length !== 5)
    throw new Error(
      `Expected 4 journey and 1 contract outcome reports; received ${files.length}.`,
    );
  const shards = new Set();
  let contracts = 0;
  for (const file of files) {
    const report = JSON.parse(readFileSync(file, "utf8"));
    if (
      report.schemaVersion !== 1 ||
      !["journey", "contract"].includes(report.layer) ||
      !Array.isArray(report.outcomes)
    )
      throw new Error(`Invalid critical-outcome report: ${file}`);
    if (report.status !== "passed")
      problems.push(`Unsuccessful ${report.layer} suite: ${file}`);
    if (report.layer === "journey") {
      if (
        !report.shard ||
        report.shard.total !== 4 ||
        ![1, 2, 3, 4].includes(report.shard.current) ||
        shards.has(report.shard.current)
      )
        throw new Error(`Missing or duplicate journey shard: ${file}`);
      shards.add(report.shard.current);
    } else {
      contracts++;
      if (contracts !== 1 || report.shard !== null)
        throw new Error(`Expected one unsharded contract report: ${file}`);
    }
    for (const outcome of report.outcomes) {
      if (
        !outcome ||
        !nonempty(outcome.id) ||
        !nonempty(outcome.testId) ||
        !nonempty(outcome.name)
      )
        throw new Error(`Invalid outcome identity: ${file}`);
      const expected = byId.get(outcome.id);
      if (!expected) throw new Error(`Unknown critical outcome: ${outcome.id}`);
      if (seen.has(outcome.id))
        throw new Error(`Duplicate critical outcome: ${outcome.id}`);
      seen.add(outcome.id);
      if (expected.layer !== report.layer)
        problems.push(
          `Wrong layer for ${outcome.id}: expected ${expected.layer}, received ${report.layer}.`,
        );
      if (outcome.status !== "passed")
        problems.push(
          `Critical outcome did not pass: ${outcome.id} (${outcome.status}) — ${outcome.name}`,
        );
    }
  }
  if (shards.size !== 4 || contracts !== 1)
    problems.push("Incomplete journey or contract reports.");
  for (const outcome of required) {
    if (!seen.has(outcome.id))
      problems.push(
        `Missing critical outcome: ${outcome.id} — ${outcome.description}`,
      );
  }
} catch (error) {
  problems.push(error.message);
}
const summary = [
  `Dashboard critical outcomes: ${seen.size} reported, ${required.length} required.`,
  ...problems.map((problem) => `ERROR: ${problem}`),
].join("\n");
console.log(summary);
if (process.env.GITHUB_STEP_SUMMARY)
  appendFileSync(process.env.GITHUB_STEP_SUMMARY, `${summary}\n`);
if (problems.length) process.exitCode = 1;
