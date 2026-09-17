import { expect, test } from '@playwright/test';
import { writeFileSync } from 'node:fs';
import path from 'node:path';
import { E2E_API_ORIGIN } from '../playwright.config';
import { E2E_USERS, mintBackendToken, mintSessionCookie } from './support/mint';

const evidence = process.env.E2E_EVIDENCE_DIR;
const bank = '/banks/BK-SAMP0001';

test('system contracts, governed clones, and real denied authority', async ({ request, page }) => {
  test.setTimeout(120_000);
  const transcript: unknown[] = [];
  async function api(method: string, route: string, data?: unknown, role = 'analyst', version?: number) {
    const response = await request.fetch(`${E2E_API_ORIGIN}/api/v1${route}`, {
      method, data, headers: { Authorization: `Bearer ${await mintBackendToken(role, version)}` },
    });
    const body = await response.json();
    transcript.push({ method, route, role, status: response.status(), body });
    if (evidence) writeFileSync(path.join(evidence, 'macro-live-api.json'), JSON.stringify(transcript, null, 2));
    return { status: response.status(), body };
  }
  const list = await api('GET', '/macro-scenarios');
  expect(list.status).toBe(200);
  expect(list.body.scenarios).toHaveLength(10);
  expect(list.body.scenarios.every((s: any) => s.owner === 'system' && s.status === 'approved' && s.is_immutable)).toBe(true);
  const scenarios = Object.fromEntries(list.body.scenarios.map((s: any) => [s.code, s]));
  const period = (await api('GET', `${bank}/reporting-periods`)).body.periods[0].id;
  const run = (id: string, role = 'analyst', version?: number) => api('POST', `${bank}/enterprise-stress/runs`, {
    scenario_id: id, reporting_period_id: period, reason: 'Live default scenario verification',
  }, role, version);
  const calibrations: Record<string, Record<string, number>> = {
    parallel_up_200: {parallel_bp: 200, short_bp: 200, long_bp: 200}, parallel_down_200: {parallel_bp: -200, short_bp: -200, long_bp: -200},
    short_up_250: {short_bp: 250, decay_years: 3}, short_down_250: {short_bp: -250, decay_years: 3},
    steepener: {short_bp: -65, long_bp: 90}, flattener: {short_bp: 80, long_bp: -60},
  };
  for (const scenario of Object.values(scenarios) as any[]) {
    const detail = await api('GET', `/macro-scenarios/${scenario.id}`);
    expect(detail.status).toBe(200);
    if (!scenario.is_runnable) {
      expect(detail.body.paths).toHaveLength(0);
      expect((await run(scenario.id)).status).toBe(409);
      continue;
    }
    expect(detail.body.paths).toHaveLength(156);
    expect(new Set(detail.body.paths.map((p: any) => p.quarter_index)).size).toBe(12);
    const suffix = scenario.code.replace('system_irr_', '');
    if (calibrations[suffix]) {
      const translation = await api('GET', `/macro-scenarios/${scenario.id}/translation/irr`);
      expect(Object.fromEntries(Object.entries(translation.body.shocks).map(([k,v]) => [k, Number(v)]))).toEqual(calibrations[suffix]);
    }
    expect((await run(scenario.id)).status).toBe(201);
  }
  const system = scenarios.system_adverse_bog_style;
  for (const action of ['submit', 'approve', 'archive']) {
    expect((await api('POST', `/macro-scenarios/${system.id}/${action}`, {reason: 'Attempt system mutation'}, action === 'approve' ? 'approver' : 'analyst')).status).toBe(409);
  }
  expect((await api('PATCH', `/macro-scenarios/${system.id}`, {name: 'Changed', reason: 'Attempt system edit'})).status).toBe(409);
  const systemRun = await run(system.id);
  const clone = await api('POST', `/macro-scenarios/${system.id}/clone`, {reason: 'Customize system default'});
  expect(clone.status).toBe(201);
  expect(clone.body.owner).toBe('organization');
  expect(clone.body.status).toBe('draft');
  let id = clone.body.id;
  expect((await run(id)).status).toBe(409);
  expect((await api('PATCH', `/macro-scenarios/${id}`, {name: 'Customized organization scenario', reason: 'Exercise editable clone'})).status).toBe(200);
  const collision = await api('POST', '/macro-scenarios', {code: system.code, name: 'Same-code organization scenario', scenario_type: clone.body.scenario_type, severity: clone.body.severity, horizon_years: 3, paths: clone.body.paths.map(({variable, year_index, base_value, stress_value}: any) => ({variable, year_index, base_value, stress_value})), reason: 'Exercise immutable identity'});
  expect(collision.status).toBe(201);
  id = collision.body.id;
  expect((await api('POST', `/macro-scenarios/${id}/submit`, {reason: 'Ready for independent review'})).status).toBe(200);
  expect((await api('POST', `/macro-scenarios/${id}/approve`, {reason: 'Independent review'}, 'approver')).status).toBe(200);
  const clonedRun = await run(id);
  expect(clonedRun.status).toBe(201);
  for (const [scenarioId, expected] of [[system.id, systemRun.body.run_id], [id, clonedRun.body.run_id]]) {
    const latest = await api('GET', `${bank}/enterprise-stress/latest?reporting_period_id=${period}&scenario_id=${scenarioId}`);
    expect(latest.body.run_id).toBe(expected);
    expect((await api('GET', `${bank}/enterprise-stress/runs/${expected}`)).body.scenario_id).toBe(scenarioId);
  }
  for (const rotation of ['steepener', 'flattener']) {
    const draft = await api('POST', `/macro-scenarios/${scenarios[`system_irr_${rotation}`].id}/clone`, {reason: 'Customize long end alone'});
    expect(draft.status).toBe(201);
    const draftId = draft.body.id;
    const paths = draft.body.paths.map(({variable, year_index, base_value, stress_value}: any) => ({variable, year_index, base_value, stress_value: variable === 'policy_rate' ? base_value : stress_value}));
    expect((await api('PATCH', `/macro-scenarios/${draftId}`, {paths, reason: 'Hold policy rates flat'})).status).toBe(200);
    expect((await api('POST', `/macro-scenarios/${draftId}/submit`, {reason: 'Ready for review'})).status).toBe(200);
    expect((await api('POST', `/macro-scenarios/${draftId}/approve`, {reason: 'Independent review'}, 'approver')).status).toBe(200);
    const result = await run(draftId);
    expect(result.status).toBe(201);
    expect(Number(result.body.outcome.irr.delta_eve)).not.toBe(0);
  }
  const grant = {principal_user_id: E2E_USERS.viewer.id, role_bundle: 'viewer', institution_scope: 'organization', module_scope: 'all', sensitivity_scope: 'all', reason: 'Read-only live verification'};
  const preview = await api('POST', '/authorization/bindings/preview', grant, 'admin');
  expect(preview.status).toBe(200);
  expect((await api('POST', '/authorization/bindings', {...grant, expected_authority_sentence: preview.body.authority_sentence}, 'admin')).status).toBe(201);
  expect((await run(system.id, 'viewer', 2)).status).toBe(403);
  await page.context().addCookies([{name: 'authjs.session-token', value: await mintSessionCookie('viewer', 2), domain: '127.0.0.1', path: '/'}]);
  await page.goto('/irr/scenarios');
  const control = page.getByRole('button', {name: 'Run enterprise stress', exact: true});
  await expect(control).toBeVisible();
  await expect(control).toBeDisabled();
  await control.hover({force: true});
  await expect(page.getByRole('tooltip')).toHaveText('Requires IRRBB · Confidential · Run. Ask your organization owner or admin to grant it.');
  if (evidence) await page.screenshot({path: path.join(evidence, 'real-denied-authority.png'), fullPage: true});
});
