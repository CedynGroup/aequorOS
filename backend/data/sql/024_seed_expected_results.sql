INSERT INTO calc.expected_metric_assertion (
    id,
    tenant_id,
    bank_id,
    scenario_id,
    as_of_date,
    metric_code,
    expected_value,
    tolerance_pct,
    assertion_note
)
VALUES
    ('41000000-0000-0000-0000-000000000001', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0001', DATE '2026-03-31', 'lcr', 142.000000, 0.050000, 'Target synthetic baseline LCR'),
    ('41000000-0000-0000-0000-000000000002', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0001', DATE '2026-03-31', 'nsfr', 118.000000, 0.050000, 'Target synthetic baseline NSFR'),
    ('41000000-0000-0000-0000-000000000003', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0001', DATE '2026-03-31', 'car', 14.200000, 0.050000, 'Target synthetic baseline CAR'),
    ('41000000-0000-0000-0000-000000000004', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0002', DATE '2026-03-31', 'lcr', 121.000000, 0.070000, 'Target synthetic adverse LCR'),
    ('41000000-0000-0000-0000-000000000005', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0002', DATE '2026-03-31', 'nsfr', 108.000000, 0.070000, 'Target synthetic adverse NSFR'),
    ('41000000-0000-0000-0000-000000000006', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0002', DATE '2026-03-31', 'car', 12.300000, 0.070000, 'Target synthetic adverse CAR'),
    ('41000000-0000-0000-0000-000000000007', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0003', DATE '2026-03-31', 'lcr', 94.000000, 0.080000, 'Target synthetic severe LCR'),
    ('41000000-0000-0000-0000-000000000008', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0003', DATE '2026-03-31', 'nsfr', 89.000000, 0.080000, 'Target synthetic severe NSFR'),
    ('41000000-0000-0000-0000-000000000009', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0003', DATE '2026-03-31', 'car', 9.200000, 0.080000, 'Target synthetic severe CAR')
ON CONFLICT (bank_id, scenario_id, as_of_date, metric_code) DO NOTHING;

INSERT INTO calc.calc_run (
    id,
    tenant_id,
    bank_id,
    module_code,
    scenario_id,
    parameter_set_id,
    as_of_date,
    run_status,
    started_at,
    completed_at,
    context_json
)
VALUES
    (
        '42000000-0000-0000-0000-000000000001',
        'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001',
        'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001',
        'liquidity',
        'eeeeeeee-eeee-eeee-eeee-eeeeeeee0001',
        '99999999-9999-9999-9999-999999990001',
        DATE '2026-03-31',
        'succeeded',
        NOW(),
        NOW(),
        '{"seeded": true, "note": "smoke run placeholder"}'::jsonb
    )
ON CONFLICT (id) DO NOTHING;

INSERT INTO calc.calc_metric_result (
    id,
    run_id,
    metric_code,
    metric_name,
    metric_value,
    unit_code,
    threshold_min,
    threshold_warn,
    metric_status
)
VALUES
    ('43000000-0000-0000-0000-000000000001', '42000000-0000-0000-0000-000000000001', 'lcr', 'Liquidity Coverage Ratio', 142.000000, 'pct', 100.0, 90.0, 'green'),
    ('43000000-0000-0000-0000-000000000002', '42000000-0000-0000-0000-000000000001', 'nsfr', 'Net Stable Funding Ratio', 118.000000, 'pct', 100.0, 90.0, 'green')
ON CONFLICT (run_id, metric_code) DO NOTHING;

INSERT INTO calc.calc_validation_result (
    id,
    run_id,
    rule_code,
    result_status,
    message
)
VALUES
    ('44000000-0000-0000-0000-000000000001', '42000000-0000-0000-0000-000000000001', 'lcr_minimum', 'pass', 'LCR above minimum threshold'),
    ('44000000-0000-0000-0000-000000000002', '42000000-0000-0000-0000-000000000001', 'nsfr_minimum', 'pass', 'NSFR above minimum threshold')
ON CONFLICT (run_id, rule_code) DO NOTHING;
