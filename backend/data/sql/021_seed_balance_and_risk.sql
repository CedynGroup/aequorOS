INSERT INTO risk.fact_balance_sheet_position (
    id,
    tenant_id,
    bank_id,
    as_of_date,
    category_group,
    category_code,
    category_name,
    amount_ghs_m,
    currency_code,
    source_system
)
VALUES
    ('21000000-0000-0000-0000-000000000001', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'asset', 'cash_bog', 'Cash and balances with BoG', 290, 'GHS', 'synthetic-pack-v1'),
    ('21000000-0000-0000-0000-000000000002', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'asset', 'investment_securities', 'Investment securities', 620, 'GHS', 'synthetic-pack-v1'),
    ('21000000-0000-0000-0000-000000000003', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'asset', 'gross_loans', 'Gross loans and advances', 1400, 'GHS', 'synthetic-pack-v1'),
    ('21000000-0000-0000-0000-000000000004', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'asset', 'other_assets', 'Other assets', 90, 'GHS', 'synthetic-pack-v1'),
    ('21000000-0000-0000-0000-000000000005', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'liability', 'retail_deposits', 'Retail deposits', 1140, 'GHS', 'synthetic-pack-v1'),
    ('21000000-0000-0000-0000-000000000006', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'liability', 'wholesale_deposits', 'Wholesale and corporate deposits', 760, 'GHS', 'synthetic-pack-v1'),
    ('21000000-0000-0000-0000-000000000007', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'liability', 'borrowings_other', 'Borrowings and other liabilities', 160, 'GHS', 'synthetic-pack-v1'),
    ('21000000-0000-0000-0000-000000000008', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'capital', 'regulatory_capital', 'Total regulatory capital', 340, 'GHS', 'synthetic-pack-v1')
ON CONFLICT (bank_id, as_of_date, category_code) DO NOTHING;

INSERT INTO risk.fact_loan_exposure (
    id,
    tenant_id,
    bank_id,
    as_of_date,
    segment_code,
    product_id,
    ead_ghs_m,
    pd,
    lgd,
    risk_weight_pct,
    npl_ratio,
    source_system
)
VALUES
    ('22000000-0000-0000-0000-000000000001', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'retail', '11111111-1111-1111-1111-111111110001', 420, 0.060000, 0.450000, 75, 0.050000, 'synthetic-pack-v1'),
    ('22000000-0000-0000-0000-000000000002', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'sme', '11111111-1111-1111-1111-111111110002', 350, 0.080000, 0.500000, 100, 0.070000, 'synthetic-pack-v1'),
    ('22000000-0000-0000-0000-000000000003', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'corporate', '11111111-1111-1111-1111-111111110003', 420, 0.050000, 0.400000, 100, 0.040000, 'synthetic-pack-v1'),
    ('22000000-0000-0000-0000-000000000004', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'mortgage', '11111111-1111-1111-1111-111111110004', 210, 0.030000, 0.250000, 35, 0.020000, 'synthetic-pack-v1')
ON CONFLICT (bank_id, as_of_date, segment_code) DO NOTHING;

INSERT INTO risk.fact_securities_holding (
    id,
    tenant_id,
    bank_id,
    as_of_date,
    security_code,
    security_type,
    issuer_name,
    carrying_amount_ghs_m,
    hqla_level,
    risk_weight_pct,
    maturity_date
)
VALUES
    ('23000000-0000-0000-0000-000000000001', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'BOG-91D', 'BoG Bill', 'Bank of Ghana', 260, 'L1', 0, DATE '2026-06-30'),
    ('23000000-0000-0000-0000-000000000002', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'GOG-2Y', 'GoG Security', 'Government of Ghana', 200, 'L1', 0, DATE '2028-03-31'),
    ('23000000-0000-0000-0000-000000000003', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'GOG-5Y', 'GoG Security', 'Government of Ghana', 160, 'L1', 0, DATE '2031-03-31')
ON CONFLICT (bank_id, as_of_date, security_code) DO NOTHING;

INSERT INTO risk.fact_deposit_behavior (
    id,
    tenant_id,
    bank_id,
    as_of_date,
    segment_code,
    segment_name,
    balance_ghs_m,
    is_core,
    stability_band,
    asf_weight_pct
)
VALUES
    ('24000000-0000-0000-0000-000000000001', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'retail_stable_core', 'Retail stable core', 684, TRUE, 'stable', 95),
    ('24000000-0000-0000-0000-000000000002', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'retail_less_stable', 'Retail less stable', 456, FALSE, 'less_stable', 90),
    ('24000000-0000-0000-0000-000000000003', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'wholesale_operational', 'Wholesale operational', 304, TRUE, 'operational', 50),
    ('24000000-0000-0000-0000-000000000004', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'wholesale_non_operational', 'Wholesale non operational', 456, FALSE, 'non_operational', 50)
ON CONFLICT (bank_id, as_of_date, segment_code) DO NOTHING;
