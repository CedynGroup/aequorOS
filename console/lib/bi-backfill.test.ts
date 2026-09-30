/**
 * Node tests for the console's BI mart backfill surface (audit A360-2 M4).
 *
 * What is being protected: `bi_mart_backfill` has exactly ONE enqueue site,
 * `POST /operator/v1/tenants/{org}/bi/backfill`, and until this surface existed
 * nothing in the product called it — a tenant that switched BI on got only the
 * live date built, so every twelve-month widget, trend pack and on-new-data
 * history answered needs-data, and after three terminal failures a hand-crafted
 * POST was the only way back.
 *
 * Two halves. The pure half pins the request builder and the window mirror. The
 * surface half reads the panel's and the client's SOURCE and asserts the action
 * is actually wired: the remediation panel renders it, gated and audited like
 * the other four, and the client posts to the backend's own path. A route that
 * exists and a surface that never calls it is the whole finding, so the second
 * half is not optional.
 *
 * Run by `pnpm --filter @aequoros/console test`.
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

import { backfillWindowProblem, biBackfillRequest, oldestAsOfDate } from './bi-backfill';

// Compiled to console/.test-out/lib, so the console root is two up.
const CONSOLE_ROOT = join(__dirname, '..', '..');

// ---------------------------------------------------------------------------
// The request
// ---------------------------------------------------------------------------

test('a blank from-date is omitted so the server default (latest snapshot) applies', () => {
  const body = biBackfillRequest({
    bankId: 'BK-SAMP0001',
    fromDate: '   ',
    untilDate: '2026-01-31',
    note: 'build the history for the pilot dashboards',
  });
  assert.deepEqual(body, {
    bank_id: 'BK-SAMP0001',
    until_date: '2026-01-31',
    note: 'build the history for the pilot dashboards',
  });
  assert.equal('from_date' in body, false, 'an empty from_date must not be sent as ""');
});

test('a given from-date travels, and every field is trimmed', () => {
  const body = biBackfillRequest({
    bankId: 'BK-SAMP0001',
    fromDate: ' 2026-06-30 ',
    untilDate: ' 2026-01-31 ',
    note: '  why  ',
  });
  assert.deepEqual(body, {
    bank_id: 'BK-SAMP0001',
    until_date: '2026-01-31',
    from_date: '2026-06-30',
    note: 'why',
  });
});

// ---------------------------------------------------------------------------
// The window mirror — the backend's newest-first rule, said before the 409
// ---------------------------------------------------------------------------

test('the until date is required and must be a calendar date', () => {
  assert.match(backfillWindowProblem({ fromDate: '', untilDate: '' }) ?? '', /until date/);
  assert.match(
    backfillWindowProblem({ fromDate: '', untilDate: '31/01/2026' }) ?? '',
    /calendar date/,
  );
});

test('a from date, when given, must be a calendar date', () => {
  assert.match(
    backfillWindowProblem({ fromDate: 'June', untilDate: '2026-01-31' }) ?? '',
    /from date must be a calendar date/,
  );
});

test('the window runs newest-first: until on or before from', () => {
  assert.equal(backfillWindowProblem({ fromDate: '2026-06-30', untilDate: '2026-01-31' }), null);
  assert.equal(backfillWindowProblem({ fromDate: '2026-06-30', untilDate: '2026-06-30' }), null);
  assert.match(
    backfillWindowProblem({ fromDate: '2026-01-31', untilDate: '2026-06-30' }) ?? '',
    /newest-first/,
  );
});

test('with no from date the window cannot be judged here and is left to the server', () => {
  assert.equal(backfillWindowProblem({ fromDate: '', untilDate: '2026-01-31' }), null);
});

test('the oldest ingested as-of date is offered as the natural until date', () => {
  assert.equal(
    oldestAsOfDate([
      { as_of_date: '2026-03-31' },
      { as_of_date: '2025-12-31' },
      { as_of_date: 'not a date' },
      { as_of_date: '2026-01-31' },
    ]),
    '2025-12-31',
  );
  assert.equal(oldestAsOfDate([]), null);
});

// ---------------------------------------------------------------------------
// The surface is wired — the finding was a route nobody called
// ---------------------------------------------------------------------------

test('the operator client posts to the backend path, not a guessed /fix/ one', () => {
  const api = readFileSync(join(CONSOLE_ROOT, 'lib', 'api.ts'), 'utf8');
  const start = api.indexOf('export function fixBiBackfill');
  assert.ok(start >= 0, 'lib/api.ts no longer exports fixBiBackfill');
  const fn = api.slice(start, api.indexOf('\n}\n', start));
  assert.match(fn, /\/bi\/backfill`/, 'fixBiBackfill must post to …/tenants/{org}/bi/backfill');
  assert.doesNotMatch(fn, /tenantFix\(/, 'the backfill route is its own router, not under /fix/');
  assert.match(fn, /method: "POST"/);
});

test('the remediation panel renders the backfill action, gated and audited like the other four', () => {
  const panel = readFileSync(
    join(CONSOLE_ROOT, 'components', 'tenants', 'RemediationPanel.tsx'),
    'utf8',
  );
  assert.ok(panel.includes('fixBiBackfill'), 'RemediationPanel no longer calls fixBiBackfill');
  const start = panel.indexOf('function BiBackfillAction');
  assert.ok(start >= 0, 'RemediationPanel no longer defines BiBackfillAction');
  const action = panel.slice(start, panel.indexOf('\n// ----', start));
  assert.ok(action.includes('<NoteField'), 'the backfill action must carry the audited note field');
  assert.ok(action.includes('backfillWindowProblem('), 'the modal must mirror the window rule');
  assert.ok(action.includes('biBackfillRequest('), 'the modal must post through the builder');
  assert.ok(
    action.includes('Audited to the inspection session'),
    'the confirmation copy must say the act is audited to the inspection session',
  );
  // Rendered inside the session-gated panel body, after the fourth action.
  const body = panel.slice(panel.indexOf('export function RemediationPanel'));
  assert.ok(body.includes('<BiBackfillAction'), 'the panel body must render BiBackfillAction');
  assert.ok(
    body.indexOf('<FixConfigAction') < body.indexOf('<BiBackfillAction'),
    'the backfill action follows the existing four',
  );
  assert.ok(
    body.includes("if (!active || active.organization_id !== orgId) return null;"),
    'the panel is still self-gated on an active session for THIS org',
  );
});

test('the tenant page hands the panel the bank it is inspecting', () => {
  const page = readFileSync(
    join(CONSOLE_ROOT, 'app', '(shell)', '(developer)', 'tenants', '[orgId]', 'page.tsx'),
    'utf8',
  );
  const start = page.indexOf('<RemediationPanel');
  assert.ok(start >= 0);
  const usage = page.slice(start, page.indexOf('/>', start));
  assert.match(usage, /bankId=\{/, 'the panel needs the bank id to name in the request');
  assert.match(usage, /onBackfill=\{/, 'the panel needs a reload to chain after a queued backfill');
});
