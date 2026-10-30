import base, { E2E_TMP } from "./playwright.config";
import { defineConfig } from "@playwright/test";
import path from "node:path";

export default defineConfig({
  ...base,
  reporter: process.env.CI
    ? [
        [
          "./e2e/support/quarantine-reporter.ts",
          { requireCompleteExecution: true },
        ],
        ["list"],
      ]
    : base.reporter,
  testMatch: "regulatory-rule-status.spec.ts",
  testIgnore: [],
  webServer: (Array.isArray(base.webServer) ? base.webServer : []).map(
    (server, index) =>
      index === 1
        ? {
            ...server,
            command: server.command.replace(
              "uvicorn app.main:app",
              "uvicorn dashboard.e2e.support.regulatory_server:app",
            ),
            env: {
              ...server.env,
              E2E_REGULATORY_CLOCK: path.join(E2E_TMP, "regulatory-clock.json"),
              LOG_LEVEL: "ERROR",
              ICAAP_FRAMEWORKS_ENABLED:
                "bog_icaap,test_icaap,cbk_icaap_gn,cbn_srp_icaap",
            },
          }
        : server,
  ),
});
