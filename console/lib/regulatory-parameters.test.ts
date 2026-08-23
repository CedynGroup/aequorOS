/**
 * Node-runnable tests for the regulatory-parameter control plane's pure logic
 * (lib/regulatory-parameters.ts). Run by `pnpm --filter @aequoros/console test`,
 * which compiles this with tsconfig.test.json and executes it under node.
 *
 * These cover the four rules that are dangerous to get wrong on a filing input:
 * decimal strings are never coerced, absent values fail closed, four-eyes is
 * legible before the server enforces it, and the supersession chain orders
 * newest-first. Component rendering is NOT covered — the console has no DOM
 * test harness.
 */

import assert from 'node:assert/strict';
import test from 'node:test';

import type { RegulatoryParameter } from './api';
import {
  approvalEligibility,
  buildChains,
  chainKey,
  confirmationLabel,
  displayValue,
  isDecimalValue,
  isProposalValid,
  lifecycleOf,
  scopeTypeLabel,
  validateProposal,
  type ProposeForm,
} from './regulatory-parameters';

const TODAY = '2026-08-21';

function param(overrides: Partial<RegulatoryParameter> = {}): RegulatoryParameter {
  return {
    id: 'id-1',
    scope_type: 'institution_class',
    scope_key: 'sdi',
    param_code: 'car_min',
    jurisdiction_code: 'GH',
    value_numeric: '13.000000',
    value_json: null,
    unit: 'pct',
    source_citation: 'Cited authority',
    confirmation_status: 'confirmed',
    effective_from: '2026-01-01',
    effective_to: null,
    status: 'approved',
    proposed_by: 'maker@example.com',
    approved_by: 'checker@example.com',
    approved_at: '2026-01-02T00:00:00Z',
    change_rationale: 'Initial entry.',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-02T00:00:00Z',
    ...overrides,
  };
}

function form(overrides: Partial<ProposeForm> = {}): ProposeForm {
  return {
    scope_type: 'institution_class',
    scope_key: 'sdi',
    param_code: 'car_min',
    jurisdiction_code: 'GH',
    value_numeric: '13',
    unit: 'pct',
    source_citation: 'Cited authority',
    confirmation_status: 'pending',
    effective_from: TODAY,
    change_rationale: 'Because the regulation says so.',
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Decimal strings are validated by shape, never by coercion
// ---------------------------------------------------------------------------

test('isDecimalValue accepts plain non-negative decimals', () => {
  for (const ok of ['0', '13', '13.5', '0.000001', '12345678901.123456', ' 7 ']) {
    assert.equal(isDecimalValue(ok), true, `expected ${ok} to be accepted`);
  }
});

test('isDecimalValue rejects what Number() would silently accept', () => {
  // Every one of these is finite under Number(), and every one is either a
  // server 422 or a precision hazard on a regulatory figure.
  for (const bad of ['1e3', '0x1f', '-1', '+1', '.5', '13.', '', '  ', 'Infinity', '1,5']) {
    assert.equal(isDecimalValue(bad), false, `expected ${bad} to be rejected`);
  }
});

test('a high-precision value survives display unchanged (no float rounding)', () => {
  const raw = '0.123456789012345678';
  const shown = displayValue(param({ value_numeric: raw }));
  assert.equal(shown.text, raw);
  // The guard that matters: the naive path would have lost digits.
  assert.notEqual(String(Number(raw)), raw);
});

// ---------------------------------------------------------------------------
// Fail closed: absence is stated, never rendered as zero
// ---------------------------------------------------------------------------

test('an absent value renders as "Not set", never as 0 or blank', () => {
  const shown = displayValue(param({ value_numeric: null, value_json: null }));
  assert.equal(shown.isSet, false);
  assert.equal(shown.text, 'Not set');
  assert.notEqual(shown.text, '0');
  assert.notEqual(shown.text.trim(), '');
});

test('a structured value is labelled, not dropped', () => {
  const shown = displayValue(param({ value_numeric: null, value_json: { a: 1 } }));
  assert.equal(shown.isSet, true);
  assert.equal(shown.text, 'Structured value');
});

test('a zero value is still a real value', () => {
  const shown = displayValue(param({ value_numeric: '0.000000' }));
  assert.equal(shown.isSet, true);
  assert.equal(shown.text, '0.000000');
});

test('an unconfirmed value is never presented as confirmed or green', () => {
  const pending = confirmationLabel('pending');
  assert.equal(pending.tone, 'warn');
  assert.equal(pending.label, 'Not confirmed');
  assert.equal(confirmationLabel('confirmed').tone, 'ok');
});

// ---------------------------------------------------------------------------
// Copy: no raw enum tokens, no hardcoded jurisdiction identity
// ---------------------------------------------------------------------------

test('user-facing labels contain no raw enum tokens', () => {
  const copy = [
    scopeTypeLabel('institution_class'),
    scopeTypeLabel('institution_type'),
    confirmationLabel('pending').label,
    confirmationLabel('pending').detail,
    confirmationLabel('confirmed').label,
    lifecycleOf(param({ status: 'draft' }), TODAY).label,
    lifecycleOf(param(), TODAY).label,
  ].join(' ');
  for (const token of ['institution_class', 'institution_type', 'scope_type', '_']) {
    assert.ok(!copy.includes(token), `label copy leaked the raw token ${token}`);
  }
});

test('no label hardcodes a country, currency, or regulator', () => {
  const copy = [
    scopeTypeLabel('institution_class'),
    scopeTypeLabel('institution_type'),
    confirmationLabel('pending').detail,
    confirmationLabel('confirmed').detail,
    lifecycleOf(param({ status: 'draft' }), TODAY).detail,
    validateProposal(form({ jurisdiction_code: '' }), TODAY).jurisdiction_code ?? '',
  ].join(' ');
  for (const token of ['GHS', 'BoG', 'Bank of Ghana', 'Ghana', 'Ghanaian', 'GH ', 'cedi']) {
    assert.ok(!copy.includes(token), `label copy hardcoded ${token}`);
  }
});

// ---------------------------------------------------------------------------
// Lifecycle / supersession ordering
// ---------------------------------------------------------------------------

test('a draft is never in force', () => {
  const life = lifecycleOf(param({ status: 'draft', approved_by: null }), TODAY);
  assert.equal(life.key, 'awaiting_approval');
  assert.notEqual(life.tone, 'ok');
});

test('an approved, open-ended, already-started generation is in force', () => {
  assert.equal(lifecycleOf(param({ effective_from: '2026-01-01' }), TODAY).key, 'in_force');
});

test('a future effective date is scheduled, not in force', () => {
  assert.equal(lifecycleOf(param({ effective_from: '2026-12-01' }), TODAY).key, 'scheduled');
});

test('effective_to is half-open: the day it arrives, the row is superseded', () => {
  // approve() closes the predecessor with effective_to = successor.effective_from,
  // so the successor governs from that date and the predecessor does not.
  assert.equal(lifecycleOf(param({ effective_to: TODAY }), TODAY).key, 'superseded');
  assert.equal(lifecycleOf(param({ effective_to: '2026-12-01' }), TODAY).key, 'in_force');
  assert.equal(lifecycleOf(param({ effective_to: '2026-01-05' }), TODAY).key, 'superseded');
});

test('buildChains groups by the resolution key and orders newest generation first', () => {
  const older = param({
    id: 'older',
    effective_from: '2025-01-01',
    effective_to: '2026-01-01',
    value_numeric: '10.000000',
  });
  const current = param({ id: 'current', effective_from: '2026-01-01' });
  const scheduled = param({
    id: 'scheduled',
    effective_from: '2026-12-01',
    value_numeric: '15.000000',
  });
  const otherScope = param({ id: 'other', scope_key: 'bank' });

  const chains = buildChains([current, older, scheduled, otherScope], TODAY);
  assert.equal(chains.length, 2);

  const sdi = chains.find((c) => c.scope_key === 'sdi');
  assert.ok(sdi);
  assert.deepEqual(
    sdi.generations.map((g) => g.id),
    ['scheduled', 'current', 'older'],
  );
  assert.equal(sdi.inForce?.id, 'current');
  assert.equal(sdi.awaitingApproval, 0);
  assert.equal(lifecycleOf(sdi.generations[2], TODAY).key, 'superseded');
});

test('a chain with only a draft has nothing in force', () => {
  const draft = param({ id: 'd', status: 'draft', approved_by: null, approved_at: null });
  const [chain] = buildChains([draft], TODAY);
  assert.equal(chain.inForce, null);
  assert.equal(chain.awaitingApproval, 1);
});

test('same effective date is tie-broken by creation time, latest first', () => {
  const first = param({ id: 'first', created_at: '2026-01-01T00:00:00Z' });
  const second = param({ id: 'second', created_at: '2026-01-01T09:00:00Z' });
  const [chain] = buildChains([first, second], TODAY);
  assert.deepEqual(
    chain.generations.map((g) => g.id),
    ['second', 'first'],
  );
});

test('chainKey separates jurisdictions with the same code and scope', () => {
  assert.notEqual(
    chainKey(param({ jurisdiction_code: 'GH' })),
    chainKey(param({ jurisdiction_code: 'NG' })),
  );
  const chains = buildChains(
    [param({ id: 'gh' }), param({ id: 'ng', jurisdiction_code: 'NG' })],
    TODAY,
  );
  assert.equal(chains.length, 2);
});

// ---------------------------------------------------------------------------
// Four-eyes
// ---------------------------------------------------------------------------

test('the proposer cannot approve their own row, and is told why', () => {
  const e = approvalEligibility(param({ status: 'draft' }), 'maker@example.com');
  assert.equal(e.state, 'blocked_own_proposal');
  assert.equal(e.canAttempt, false);
  assert.match(e.message, /second operator/i);
});

test('proposer matching is case- and whitespace-insensitive, like the server', () => {
  const row = param({ status: 'draft', proposed_by: ' Maker@Example.com ' });
  assert.equal(approvalEligibility(row, 'maker@example.com').canAttempt, false);
  assert.equal(approvalEligibility(row, 'MAKER@EXAMPLE.COM ').canAttempt, false);
});

test('a different operator is eligible', () => {
  const e = approvalEligibility(param({ status: 'draft' }), 'checker@example.com');
  assert.equal(e.state, 'eligible');
  assert.equal(e.canAttempt, true);
});

test('an unknown viewer is not claimed eligible, but the server still decides', () => {
  for (const unknown of [null, '', '   ']) {
    const e = approvalEligibility(param({ status: 'draft' }), unknown);
    assert.equal(e.state, 'viewer_unknown');
    assert.equal(e.canAttempt, true, 'the server is the authority, not the client');
    assert.match(e.message, /refused/i);
  }
});

test('an approved row is not approvable again', () => {
  const e = approvalEligibility(param({ status: 'approved' }), 'checker@example.com');
  assert.equal(e.state, 'not_a_draft');
  assert.equal(e.canAttempt, false);
});

// ---------------------------------------------------------------------------
// Propose-form validation
// ---------------------------------------------------------------------------

test('a complete proposal validates', () => {
  assert.equal(isProposalValid(validateProposal(form(), TODAY)), true);
});

test('every schema-required field is required in the form', () => {
  const required: (keyof ProposeForm)[] = [
    'scope_key',
    'param_code',
    'jurisdiction_code',
    'value_numeric',
    'unit',
    'source_citation',
    'effective_from',
    'change_rationale',
  ];
  for (const field of required) {
    const errs = validateProposal(form({ [field]: '' } as Partial<ProposeForm>), TODAY);
    assert.ok(errs[field], `${field} should be required`);
  }
});

test('a back-dated effective date is refused, today is allowed', () => {
  assert.ok(validateProposal(form({ effective_from: '2026-08-20' }), TODAY).effective_from);
  assert.equal(validateProposal(form({ effective_from: TODAY }), TODAY).effective_from, undefined);
  assert.equal(
    validateProposal(form({ effective_from: '2027-01-01' }), TODAY).effective_from,
    undefined,
  );
});

test('a negative value is refused before it reaches the server', () => {
  assert.ok(validateProposal(form({ value_numeric: '-5' }), TODAY).value_numeric);
});

test('field lengths match the backend column limits', () => {
  assert.ok(validateProposal(form({ scope_key: 'x'.repeat(41) }), TODAY).scope_key);
  assert.ok(validateProposal(form({ param_code: 'x'.repeat(65) }), TODAY).param_code);
  assert.ok(validateProposal(form({ jurisdiction_code: 'x'.repeat(9) }), TODAY).jurisdiction_code);
  assert.ok(validateProposal(form({ unit: 'x'.repeat(25) }), TODAY).unit);
  assert.ok(validateProposal(form({ source_citation: 'x'.repeat(241) }), TODAY).source_citation);
  assert.ok(validateProposal(form({ change_rationale: 'x'.repeat(501) }), TODAY).change_rationale);
});
