import base from "./playwright.config";
import { defineConfig } from "@playwright/test";

export default defineConfig({
  ...base,
  testMatch: "regulatory-rule-status.spec.ts",
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
              LOG_LEVEL: "ERROR",
              ICAAP_FRAMEWORKS_ENABLED:
                "bog_icaap,test_icaap,cbk_icaap_gn,cbn_srp_icaap",
            },
          }
        : server,
  ),
});
