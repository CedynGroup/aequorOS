/**
 * Playwright e2e for the submission pipeline (plan W7.5).
 *
 * Boots the disposable API, local OIDC issuer, and dashboard. Stack setup and
 * prerequisites are documented in README.md §End-to-end (Playwright).
 *
 * Object storage is a real S3/MinIO dependency: the backend has no filesystem
 * mode. `pnpm e2e` provisions worktree-local MinIO through local_services.py
 * when no endpoint is exported (native binaries when Docker is unavailable).
 * CI supplies its own endpoint. Direct Playwright invocations must supply S3_*
 * or use backend/.env; package-capable journeys refuse immediately without it.
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
export const E2E_IDP_PORT = runtimePorts.idp;
export const E2E_BASE_URL = `http://127.0.0.1:${E2E_DASHBOARD_PORT}`;
export const E2E_API_ORIGIN = `http://127.0.0.1:${E2E_BACKEND_PORT}`;
// Plain http on loopback: the only issuer shape the backend and the dashboard
// egress guards accept without TLS, and only on an undeployed APP_ENV.
export const E2E_IDP_ORIGIN = `http://127.0.0.1:${E2E_IDP_PORT}`;
// Authenticates the dashboard's server-to-server read of the SSO client config.
// Any value enables the path; this one says what it is.
const E2E_SSO_INTERNAL_KEY = "e2e-sso-internal-key-not-production-000";
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
      // Started first: the bootstrap below registers its issuer URL, and the
      // dashboard's NextAuth discovers it on the first SSO sign-in.
      command: "uv run python scripts/e2e_idp.py",
      cwd: BACKEND_DIR,
      url: `${E2E_IDP_ORIGIN}/.well-known/openid-configuration`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: { E2E_IDP_PORT: String(E2E_IDP_PORT) },
    },
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
      // The PR #230 fixture now materializes the ICAAP live plane and filing
      // chain before uvicorn starts. On a cold or loaded host that bootstrap
      // can exceed two minutes even though the server is healthy afterward.
      timeout: 300_000,
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
        // ICAAP has no on/off flag (D-046): the gates are institution class
        // and Capital/confidential authority, which `icaap-sdi.spec.ts` and
        // `icaap-workspace.spec.ts` cover from both sides.
        //
        // These two ARE needed, and only here. 15 of the 17 Ghana sections are
        // still awaiting the regulator's published text (D-006), so a real
        // filing cannot be frozen against that instrument and the filing
        // journey would have nothing to exercise. The test instrument is a
        // fully-sourced framework that exists for exactly this, and the
        // settings validator REFUSES the extra directory outside `local`/
        // `test`, so it cannot reach a deployment.
        ICAAP_EXTRA_FRAMEWORKS_DIR: "tests/fixtures/icaap/frameworks",
        ICAAP_FRAMEWORKS_ENABLED: "bog_icaap,test_icaap",
        // Pinned to the product default for the same reason as
        // ATTESTATION_ESIGN_REQUIRED above: a developer who has switched the
        // ICAAP ceremony on locally must not change what these journeys see.
        // A journey that exercises the ICAAP ceremony sets it to 1 itself.
        ICAAP_SIGNING_ENABLED: "0",
        // Business Intelligence. `BI_ENABLED` defaults off and is set in no
        // deployment yet, and with it off every `/banks/{id}/bi/*` route answers
        // 404 and the shell HIDES Insights, Dashboards and Explore — so without
        // this the four `bi-*` journeys would navigate to walled-up doors and
        // pass on an empty state. It is set HERE and not in `backend/.env` so a
        // developer's own flag state cannot change what the journeys see, in
        // either direction: `bi-authorization.spec.ts` asserts both the flag-on
        // and the flag-off shell, the second by intercepting `/feature-flags`.
        //
        // The other four BI flags stay OFF, which is the production-shaped
        // configuration and deliberate. `BI_MART_ENQUEUE_ENABLED` would enqueue
        // `bi_mart_refresh` into the `bi` worker lane, and this stack runs no
        // worker at all — the marts are materialised synchronously by
        // `tests/fixtures/bi_plane.py` during bootstrap, exactly as the live
        // plane is, so an enqueue flag here would only orphan jobs in `queued`.
        // `BI_SCHEDULER_ENABLED` / `BI_SUBSCRIPTIONS_ENABLED` add tick branches
        // nothing here runs, and `BI_ALERTS_ENABLED` is evaluated by a succeeded
        // mart build that no worker will perform.
        BI_ENABLED: "1",
        // Natural-language questions. A THIRD switch, independent of BI_ENABLED,
        // and off in every deployment — so without this the ask routes answer 409
        // and the shell hides the Explore "Ask a question" tab, and `bi-ask.spec.ts`
        // would navigate to a walled-up door and pass on an empty state. Setting it
        // sends nothing to any vendor: the AI gates are separate and all shut here
        // (no approved configuration, no credential, no `ai`-lane worker), which is
        // precisely the refusal the wire half of that spec asserts. It is set HERE
        // rather than in `backend/.env` so a developer's own flag state cannot change
        // what the journeys see, and the flag-OFF shell is asserted by intercepting
        // `/feature-flags`, as the BI flag's own journey does.
        BI_NLQ_ENABLED: "1",
        // The journeys ARE the script the BI budget exists to bound. They drive
        // roughly thirty BI journeys as ONE identity inside the 60-second window,
        // so the suite trips the product's own limit of 120 reads and the failure
        // lands on whichever spec happens to run last — a red suite that says
        // nothing about the code, and a different spec each time the order shifts.
        // Raising the CEILING here cannot switch the budget off: the window is not
        // configurable, every read is still metered and still recorded, and
        // `test_bi_routes.py` / `test_bi_query_log.py` prove the refusal itself
        // against the product figure rather than against this one.
        BI_RATE_LIMIT_MAX_QUERIES: "5000",
        AUTH_JWT_SECRET: "e2e-backend-jwt-secret-not-production-000",
        IMPERSONATION_JWT_SECRET: "e2e-impersonation-secret-not-production-000",
        SSO_INTERNAL_KEY: E2E_SSO_INTERNAL_KEY,
        E2E_IDP_ISSUER: E2E_IDP_ORIGIN,
        // Computed, not written as a literal: the vault wants base64, and a
        // base64 literal in source is indistinguishable from a real key to a
        // secret scanner (gitleaks flagged exactly that). Keeping the readable
        // string here means a genuine key pasted into this spot would still be
        // caught, instead of hiding behind an allowlist entry.
        CREDENTIAL_VAULT_MASTER_KEY: E2E_VAULT_KEY,
        CORS_ORIGINS: E2E_BASE_URL,
        APP_ENV: "test",
        // The hermetic stack has no TLS to verify: the database is a disposable
        // SQLite FILE (no transport at all), and MinIO, the IdP and the
        // dashboard all answer plain http on loopback. Since database TLS
        // became mandatory (#470) `plaintext_allowed()` requires BOTH an
        // undeployed APP_ENV and this flag, so without it `create_app` refuses
        // at startup — `TransportSecurityError: Database transport requires
        // PostgreSQL with a hostname` — before a single journey runs (#492).
        //
        // It cannot reach a deployment: `validate_service_transports` raises
        // outright when APP_ENV is staging or production and this flag is set,
        // which `test_production_refuses_local_escape_hatch` pins. Setting it
        // here rather than in `backend/.env` also keeps a developer's own TLS
        // posture from changing what the journeys see.
        TLS_ALLOW_PLAINTEXT: "1",
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
        AUTH_TRUST_HOST: "true",
        SSO_INTERNAL_KEY: E2E_SSO_INTERNAL_KEY,
        // The dashboard's egress guard mirrors the backend's loopback
        // carve-out and reads the same variable to decide it applies.
        APP_ENV: "test",
      },
    },
  ],
});
