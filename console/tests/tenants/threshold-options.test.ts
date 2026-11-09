import assert from 'node:assert/strict';
import { test } from 'node:test';
import { liquidityThresholdOption } from '../../lib/threshold-options';

test('copied bank-local thresholds have distinct institution labels and unchanged target IDs', () => {
  const common = {
    institution_class: 'bank',
    threshold_code: 'narrow_to_volatile',
    threshold_pct: 85,
    effective_from: '2026-01-01',
    effective_to: null,
    approved_by: 'Synthetic Board',
  };
  const first = liquidityThresholdOption({
    ...common, id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', bank_id: 'BK-SYNTH001',
  });
  const second = liquidityThresholdOption({
    ...common, id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', bank_id: 'BK-SYNTH002',
  });
  assert.match(first.label, /BK-SYNTH001/);
  assert.match(second.label, /BK-SYNTH002/);
  assert.notEqual(first.label, second.label);
  assert.equal(first.id, 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');
  assert.equal(second.id, 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb');
  assert.equal(first.current, '85%');
});
