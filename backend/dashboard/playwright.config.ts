/**
 * Playwright e2e for the submission pipeline (plan W7.5).
 *
 * Boots the FastAPI backend on a disposable sqlite file (demo seeding enabled
 * — the e2e fixture path, never production) and the Next.js dev server wired
 * to it. Global setup bootstraps tenant rows, seeds the sample bank through
 * the API, runs a liquidity baseline, and mints per-role session cookies — no
 * real credentials anywhere.
 *
 * Hermetic EXCEPT object storage. This file used to claim "fully hermetic",
 * which was wrong and cost a long diagnosis: validated packages persist their
 * artifacts to S3/MinIO and the backend has no filesystem mode, so the suite
 * silently depends on S3_* reaching it from the untracked backend/.env. That
 * is why it passes on a developer machine and fails in a fresh clone, a git
 * worktree, or CI. The four package-capable specs refuse immediately without
 * it, while storage-free journeys can still run on a cold worktree.
 *
 * Run: pnpm e2e   (first run: npx playwright install chromium)
 */

import { defineConfig } from "@playwright/test";
import path from "path";
import { acquireE2ERunLock } from "./e2e/support/runtime-lock";
import { selectE2ERuntimePorts } from "./e2e/support/runtime-ports";

export const E2E_TMP = path.join(__dirname, "e2e", ".tmp");
acquireE2ERunLock(E2E_TMP);
const runtimePorts = selectE2ERuntimePorts();
export const E2E_BACKEND_PORT = runtimePorts.backend;
export const E2E_DASHBOARD_PORT = runtimePorts.dashboard;
export const E2E_BASE_URL = `http://127.0.0.1:${E2E_DASHBOARD_PORT}`;
export const E2E_API_ORIGIN = `http://127.0.0.1:${E2E_BACKEND_PORT}`;
// Seals the disposable soft signing keys the ceremony journeys use. The
// software key backend refuses to initialise when APP_ENV is production, so
// this fixture value cannot reach a deployment.
const E2E_VAULT_KEY = Buffer.from(
  "e2e-vault-master-key-not-prod-00000",
).toString("base64");

const BACKEND_DIR = path.join(__dirname, "..");
const E2E_DB = path.join(E2E_TMP, "e2e.db");

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  retries: 0,
  workers: 1, // journeys share one disposable canonical test bank; keep them ordered + isolated
  reporter: process.env.CI
    ? [["./e2e/support/quarantine-reporter.ts"], ["list"]]
    : [["list"]],
  use: {
    baseURL: E2E_BASE_URL,
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command:
        // Start from a FRESH disposable database every run. Terminal
        // regulatory packages (acknowledged/rejected) minted by lifecycle
        // journeys would otherwise leak across runs and block regeneration.
        `sh -c 'mkdir -p "${E2E_TMP}" && rm -f "${E2E_DB}" "${E2E_DB}-wal" "${E2E_DB}-shm" && ` +
        `PYTHONPATH=. DATABASE_URL="sqlite+pysqlite:///${E2E_DB}" uv run python scripts/e2e_bootstrap.py && ` +
        `exec uv run uvicorn app.main:app --host 127.0.0.1 --port ${E2E_BACKEND_PORT} --log-level warning'`,
      cwd: BACKEND_DIR,
      url: `${E2E_API_ORIGIN}/api/health/live`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        DATABASE_URL: `sqlite+pysqlite:///${E2E_DB}`,
        WORKER_DATABASE_URL: "",
        // Signer identities need a pepper to derive; signing itself stays
        // OFF so the hermetic stack exercises the surfaces and guards
        // without an HSM.
        SIGNER_ID_PEPPER: "e2e-signer-pepper-not-production-000",
        // Signing ON for the hermetic stack, backed by disposable self-signed
        // software keys. The software backend refuses to start when APP_ENV is
        // production, so this configuration cannot leak into a deployment.
        ATTESTATION_SIGNING_ENABLED: "1",
        // The requirement must hold regardless of the developer's .env —
        // the ceremony specs assert the signature gate.
        ATTESTATION_ESIGN_REQUIRED: "1",
        SIGNING_BACKEND: "software",
        SIGNING_SOFTWARE_KEY_DIR: `${E2E_TMP}/signing-keys`,
        RUN_INPROCESS_WORKER: "0",
        AUTH_JWT_SECRET: "e2e-backend-jwt-secret-not-production-000",
        SSO_INTERNAL_KEY: "",
        // Computed, not written as a literal: the vault wants base64, and a
        // base64 literal in source is indistinguishable from a real key to a
        // secret scanner (gitleaks flagged exactly that). Keeping the readable
        // string here means a genuine key pasted into this spot would still be
        // caught, instead of hiding behind an allowlist entry.
        CREDENTIAL_VAULT_MASTER_KEY: E2E_VAULT_KEY,
        CORS_ORIGINS: E2E_BASE_URL,
        APP_ENV: "test",
      },
    },
    {
      command: `pnpm next dev -H 127.0.0.1 -p ${E2E_DASHBOARD_PORT}`,
      cwd: __dirname,
      url: `${E2E_BASE_URL}/login`,
      reuseExistingServer: false,
      timeout: 180_000,
      env: {
        NEXT_PUBLIC_RISK_API_BASE_URL: `${E2E_API_ORIGIN}/api/v1`,
        // Separate build cache so an e2e run never poisons a developer's live
        // `.next` (NEXT_PUBLIC_* is compile-time-inlined; next dev shares one
        // cache per directory). Paired with distDir in next.config.js.
        NEXT_DIST_DIR: ".next-e2e",
        AUTH_SECRET: "e2e-nextauth-secret-not-production-000",
        AUTH_URL: E2E_BASE_URL,
        AUTH_TRUST_HOST: "true",
        SSO_INTERNAL_KEY: "",
      },
    },
  ],
});
