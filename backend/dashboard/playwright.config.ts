/**
 * Playwright e2e for the submission pipeline (plan W7.5).
 *
 * Boots a fully hermetic stack: the FastAPI backend on a disposable sqlite
 * file (demo seeding enabled — the e2e fixture path, never production) and
 * the Next.js dev server wired to it. Global setup bootstraps tenant rows,
 * seeds the sample bank through the API, runs a liquidity baseline, and
 * mints per-role session cookies — no real credentials anywhere.
 *
 * Run: pnpm e2e   (first run: npx playwright install chromium)
 */

import { defineConfig } from '@playwright/test';
import path from 'path';

export const E2E_BACKEND_PORT = 8021;
export const E2E_DASHBOARD_PORT = 3021;
export const E2E_BASE_URL = `http://localhost:${E2E_DASHBOARD_PORT}`;
export const E2E_API_ORIGIN = `http://127.0.0.1:${E2E_BACKEND_PORT}`;
export const E2E_TMP = path.join(__dirname, 'e2e', '.tmp');

const BACKEND_DIR = path.join(__dirname, '..');
const E2E_DB = path.join(E2E_TMP, 'e2e.db');

export default defineConfig({
  testDir: './e2e',
  globalSetup: './e2e/global-setup.ts',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  retries: 0,
  workers: 1, // journeys share one seeded bank; keep them ordered + isolated
  reporter: [['list']],
  use: {
    baseURL: E2E_BASE_URL,
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command:
        // Start from a FRESH disposable database every run: seed-demo only
        // wipes the seeded bank's ingestion/run dependents, so terminal
        // regulatory packages (acknowledged/rejected) minted by lifecycle
        // journeys would otherwise leak across runs and block regeneration.
        `sh -c 'mkdir -p "${E2E_TMP}" && rm -f "${E2E_DB}" "${E2E_DB}-wal" "${E2E_DB}-shm" && ` +
        `PYTHONPATH=. DATABASE_URL="sqlite+pysqlite:///${E2E_DB}" uv run python scripts/e2e_bootstrap.py && ` +
        `exec uv run uvicorn app.main:app --port ${E2E_BACKEND_PORT} --log-level warning'`,
      cwd: BACKEND_DIR,
      url: `${E2E_API_ORIGIN}/api/health/live`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        DATABASE_URL: `sqlite+pysqlite:///${E2E_DB}`,
        WORKER_DATABASE_URL: '',
        DEMO_SEED_ENABLED: '1',
        RUN_INPROCESS_WORKER: '0',
        AUTH_JWT_SECRET: 'e2e-backend-jwt-secret-not-production-000',
        SSO_INTERNAL_KEY: '',
        CREDENTIAL_VAULT_MASTER_KEY: '',
        CORS_ORIGINS: E2E_BASE_URL,
        APP_ENV: 'test',
      },
    },
    {
      command: `pnpm next dev -p ${E2E_DASHBOARD_PORT}`,
      cwd: __dirname,
      url: `${E2E_BASE_URL}/login`,
      reuseExistingServer: false,
      timeout: 180_000,
      env: {
        NEXT_PUBLIC_RISK_API_BASE_URL: `${E2E_API_ORIGIN}/api/v1`,
        // Separate build cache so an e2e run never poisons a developer's live
        // `.next` (NEXT_PUBLIC_* is compile-time-inlined; next dev shares one
        // cache per directory). Paired with distDir in next.config.js.
        NEXT_DIST_DIR: '.next-e2e',
        AUTH_SECRET: 'e2e-nextauth-secret-not-production-000',
        AUTH_URL: E2E_BASE_URL,
        AUTH_TRUST_HOST: 'true',
        SSO_INTERNAL_KEY: '',
      },
    },
  ],
});
