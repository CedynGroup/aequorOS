INSERT INTO risk.fact_lcr_outflow_base (
    id,
    tenant_id,
    bank_id,
    as_of_date,
    outflow_category_code,
    outflow_category_name,
    exposure_amount_ghs_m
)
VALUES
    ('25000000-0000-0000-0000-000000000001', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'retail_stable', 'Retail deposits stable', 684),
    ('25000000-0000-0000-0000-000000000002', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'retail_less_stable', 'Retail deposits less stable', 456),
    ('25000000-0000-0000-0000-000000000003', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'wholesale_operational', 'Unsecured wholesale operational', 304),
    ('25000000-0000-0000-0000-000000000004', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'wholesale_non_operational_sme', 'Unsecured wholesale non-operational SME', 220),
    ('25000000-0000-0000-0000-000000000005', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'wholesale_non_operational_corp', 'Unsecured wholesale non-operational corporate', 236)
ON CONFLICT (bank_id, as_of_date, outflow_category_code) DO NOTHING;

INSERT INTO risk.fact_lcr_inflow_base (
    id,
    tenant_id,
    bank_id,
    as_of_date,
    inflow_category_code,
    inflow_category_name,
    exposure_amount_ghs_m
)
VALUES
    ('26000000-0000-0000-0000-000000000001', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'performing_loans', 'Expected inflow from performing loans', 140),
    ('26000000-0000-0000-0000-000000000002', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'securities_coupon', 'Coupon and maturity inflows', 35)
ON CONFLICT (bank_id, as_of_date, inflow_category_code) DO NOTHING;

INSERT INTO risk.fact_nsfr_asf_base (
    id,
    tenant_id,
    bank_id,
    as_of_date,
    asf_category_code,
    asf_category_name,
    amount_ghs_m
)
VALUES
    ('26100000-0000-0000-0000-000000000001', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'reg_capital', 'Regulatory capital', 340),
    ('26100000-0000-0000-0000-000000000002', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'retail_stable', 'Retail stable deposits', 684),
    ('26100000-0000-0000-0000-000000000003', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'retail_less_stable', 'Retail less stable deposits', 456),
    ('26100000-0000-0000-0000-000000000004', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'wholesale_funding', 'Wholesale funding', 760)
ON CONFLICT (bank_id, as_of_date, asf_category_code) DO NOTHING;

INSERT INTO risk.fact_nsfr_rsf_base (
    id,
    tenant_id,
    bank_id,
    as_of_date,
    rsf_category_code,
    rsf_category_name,
    amount_ghs_m
)
VALUES
    ('26200000-0000-0000-0000-000000000001', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'cash', 'Cash and balances with BoG', 290),
    ('26200000-0000-0000-0000-000000000002', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'hqla_l1', 'Level 1 securities', 620),
    ('26200000-0000-0000-0000-000000000003', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'corporate_loans', 'Corporate and SME loans', 770),
    ('26200000-0000-0000-0000-000000000004', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'residential_mortgages', 'Residential mortgages', 210),
    ('26200000-0000-0000-0000-000000000005', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'other_assets', 'Other assets', 90)
ON CONFLICT (bank_id, as_of_date, rsf_category_code) DO NOTHING;

INSERT INTO risk.fact_capital_components (
    id,
    tenant_id,
    bank_id,
    as_of_date,
    component_type,
    component_code,
    component_name,
    amount_ghs_m
)
VALUES
    ('27000000-0000-0000-0000-000000000001', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'cet1', 'paid_up_capital', 'Paid-up capital', 180),
    ('27000000-0000-0000-0000-000000000002', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'cet1', 'retained_earnings', 'Retained earnings', 110),
    ('27000000-0000-0000-0000-000000000003', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'at1', 'at1_instruments', 'AT1 instruments', 20),
    ('27000000-0000-0000-0000-000000000004', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'tier2', 'subordinated_debt', 'Tier 2 subordinated debt', 35),
    ('27000000-0000-0000-0000-000000000005', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 'deduction', 'regulatory_deduction', 'Regulatory deductions', -5)
ON CONFLICT (bank_id, as_of_date, component_code) DO NOTHING;

INSERT INTO risk.fact_market_risk_position (
    id,
    tenant_id,
    bank_id,
    as_of_date,
    net_long_fx_ghs_m,
    net_short_fx_ghs_m
)
VALUES
    ('28000000-0000-0000-0000-000000000001', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', 120, 84)
ON CONFLICT (bank_id, as_of_date) DO NOTHING;

INSERT INTO risk.fact_operational_risk_income (
    id,
    tenant_id,
    bank_id,
    fiscal_year,
    gross_income_ghs_m
)
VALUES
    ('29000000-0000-0000-0000-000000000001', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 2023, 118),
    ('29000000-0000-0000-0000-000000000002', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 2024, 126),
    ('29000000-0000-0000-0000-000000000003', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 2025, 134)
ON CONFLICT (bank_id, fiscal_year) DO NOTHING;
