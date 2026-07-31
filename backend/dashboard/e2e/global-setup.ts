/**
 * Global setup: seed the hermetic backend through its own API, then mint
 * per-role storage states. Servers are already up (Playwright webServer).
 */

import type { FullConfig } from '@playwright/test';
import { readFileSync } from 'fs';
import path from 'path';
import { E2E_API_ORIGIN, E2E_BASE_URL, E2E_TMP } from '../playwright.config';
import { mintBackendToken, writeStorageState } from './support/mint';

const SAMPLE_BANK_ID = 'BK-SAMP0001';
// e2e/ -> dashboard/ -> backend/, the directory whose .env the FastAPI process
// loads.
const BACKEND_DIR = path.join(__dirname, '..', '..');

async function api(
  token: string,
  method: string,
  pathName: string,
  body?: unknown
): Promise<any> {
  const response = await fetch(`${E2E_API_ORIGIN}/api/v1${pathName}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    throw new Error(
      `${method} ${pathName} -> ${response.status}: ${await response.text()}`
    );
  }
  return response.json();
}

/**
 * The stack is hermetic in every respect EXCEPT object storage.
 *
 * `StorageBackend` is `Literal["s3"]` — there is no filesystem mode — so the
 * backend needs a reachable S3/MinIO endpoint to persist the artifacts a
 * package produces when it is validated. On a developer machine that arrives
 * silently from the untracked `backend/.env`, which is why the suite passes
 * there and nowhere else.
 *
 * Without it the failure is deeply unhelpful: the package simply never leaves
 * `generated`, so seven tests sit waiting for a Validated badge, an artifact
 * download, or a signing workspace that will never appear, and each burns its
 * full 60-second timeout. That is ~7.7 minutes to be told nothing. Measured
 * 2026-07-31: cold worktree without storage 7 failed / 7.7 min; the same
 * commit with only the S3_* keys added, 20 passed / 1.6 min.
 *
 * So say it in one second instead.
 */
async function requireObjectStorage(): Promise<void> {
  // Deliberately NOT /health/ready. `storage_configured` returns true with
  // nothing configured at all — backend defaults to "s3", bucket and region
  // carry defaults, and a null endpoint_url short-circuits to true before the
  // credentials are ever considered. Readiness therefore reports storage "ok"
  // on a machine that cannot store a byte, which is its own bug and the
  // reason this check first failed to fire.
  //
  // Check the same source the backend reads instead: process.env (CI exports
  // them) falling back to backend/.env (how a developer machine supplies
  // them). That models the real question — will the FastAPI process get
  // usable storage config — rather than trusting a predicate that says yes
  // regardless.
  const required = ['S3_ENDPOINT', 'S3_ACCESS_KEY', 'S3_SECRET_KEY'];
  const fromEnvFile = new Set<string>();
  try {
    const dotenv = readFileSync(path.join(BACKEND_DIR, '.env'), 'utf8');
    for (const line of dotenv.split('\n')) {
      const key = /^\s*([A-Z0-9_]+)\s*=\s*\S/.exec(line)?.[1];
      if (key) fromEnvFile.add(key);
    }
  } catch {
    // No .env at all — every key counts as missing, which is the CI case.
  }
  const missing = required.filter((key) => !process.env[key] && !fromEnvFile.has(key));
  if (missing.length === 0) return;
  throw new Error(
    [
      `e2e needs object storage; ${missing.join(', ')} ${missing.length === 1 ? 'is' : 'are'} unset.`,
      '',
      'Artifacts are persisted to S3/MinIO when a package is validated, and the',
      'backend has no filesystem storage mode. Without it packages stay at',
      '"generated", so the attestation and lifecycle journeys each wait out a',
      '60s timeout for a Validated badge that never arrives — seven failures',
      'and no usable message.',
      '',
      'Locally: backend/.env supplies S3_ENDPOINT / S3_ACCESS_KEY /',
      'S3_SECRET_KEY / S3_BUCKET. Run e2e from a checkout that HAS it — a git',
      'worktree does not inherit untracked files, which is exactly how this',
      'went undiagnosed.',
      'In CI: run MinIO as a service container and pass the same four.',
    ].join('\n')
  );
}

export default async function globalSetup(_config: FullConfig): Promise<void> {
  await requireObjectStorage();
  const admin = await mintBackendToken('admin');

  // Seed the sample bank (e2e fixture path — DEMO_SEED_ENABLED=1 on this
  // hermetic backend only) and compute a liquidity baseline so BSD3 packs
  // can generate.
  await api(admin, 'POST', '/banks/seed-demo');
  const periods = await api(
    admin,
    'GET',
    `/banks/${SAMPLE_BANK_ID}/reporting-periods`
  );
  const latest = periods.periods[0];
  await api(admin, 'POST', `/banks/${SAMPLE_BANK_ID}/regulatory-runs`, {
    module: 'liquidity',
    reporting_period_id: latest.id,
    scenario_code: 'baseline',
  });

  // Institution profile so LRT packs generate (corporate journey).
  await api(admin, 'PUT', `/banks/${SAMPLE_BANK_ID}/institution-profile`, {
    reason: 'e2e bootstrap',
    institution_type: 'Universal Bank',
    legal_entity_structure: 'Private Limited Company',
    orass_institution_code: 'GH-UB-9001',
    traded_on_exchange: false,
    ownership_local_pct: '60',
    ownership_foreign_pct: '40',
  });

  for (const role of ['admin', 'approver', 'analyst', 'viewer'] as const) {
    await writeStorageState(role, E2E_BASE_URL, E2E_TMP);
  }
}
