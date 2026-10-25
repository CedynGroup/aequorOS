import { defineConfig } from "@playwright/test";
import journeys from "./playwright.config";

// Contracts use a fresh disposable product stack after the journey run. They
// remain required in CI, but presentation and HTTP checks do not count as journeys.
export default defineConfig({
  ...journeys,
  testDir: "./contracts",
  reporter: [
    ["./e2e/support/contract-reporter.ts"],
    ["./e2e/support/critical-outcome-reporter.ts", { layer: "contract" }],
    ["list"],
  ],
});
