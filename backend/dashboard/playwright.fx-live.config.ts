import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

// These mutation journeys own a fresh disposable stack, separate from the
// shared CI journeys whose fixture users and filing history must stay stable.
export default defineConfig({
  ...base,
  testDir: "./live-verification",
  reporter: [["list"]],
});
