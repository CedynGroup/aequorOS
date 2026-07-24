/**
 * Global setup: seed the hermetic backend through its own API, then mint
 * per-role storage states. Servers are already up (Playwright webServer).
 */

import type { FullConfig } from '@playwright/test';
import { E2E_API_ORIGIN, E2E_BASE_URL, E2E_TMP } from '../playwright.config';
import { mintBackendToken, writeStorageState } from './support/mint';

const SAMPLE_BANK_ID = 'BK-SAMP0001';

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

export default async function globalSetup(_config: FullConfig): Promise<void> {
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
