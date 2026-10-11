import { defineConfig } from "@playwright/test";
import journeys from "./playwright.config";

// Screenshot collection has no behavioral assertions and is deliberately
// separate from the required journey suite and its executed-count floor.
export default defineConfig({
  ...journeys,
  testDir: "./visual",
  reporter: "list",
});
