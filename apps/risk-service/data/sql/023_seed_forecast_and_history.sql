INSERT INTO risk.parameter_sets (
    id,
    tenant_id,
    bank_id,
    parameter_set_code,
    parameter_set_name,
    jurisdiction_code,
    effective_from,
    approved_by,
    approval_timestamp,
    is_active
)
VALUES
    (
        '99999999-9999-9999-9999-999999990001',
        'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001',
        'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001',
        'bog_2026_mvp',
        'BoG 2026 MVP Baseline',
        'BOG',
        DATE '2026-01-01',
        'calc-owner',
        NOW(),
        TRUE
    )
ON CONFLICT (bank_id, parameter_set_code, effective_from) DO NOTHING;

INSERT INTO risk.param_lcr_runoff_rate (id, parameter_set_id, runoff_category_code, runoff_category_name, base_rate_pct, stressed_rate_pct)
VALUES
    ('31000000-0000-0000-0000-000000000001', '99999999-9999-9999-9999-999999990001', 'retail_stable', 'Retail stable deposits', 5, 15),
    ('31000000-0000-0000-0000-000000000002', '99999999-9999-9999-9999-999999990001', 'retail_less_stable', 'Retail less stable deposits', 10, 20),
    ('31000000-0000-0000-0000-000000000003', '99999999-9999-9999-9999-999999990001', 'wholesale_operational', 'Unsecured wholesale operational', 25, 40),
    ('31000000-0000-0000-0000-000000000004', '99999999-9999-9999-9999-999999990001', 'wholesale_non_operational_sme', 'Unsecured wholesale non-operational SME', 40, 60),
    ('31000000-0000-0000-0000-000000000005', '99999999-9999-9999-9999-999999990001', 'wholesale_non_operational_corp', 'Unsecured wholesale non-operational corporate', 100, 100)
ON CONFLICT (parameter_set_id, runoff_category_code) DO NOTHING;

INSERT INTO risk.param_lcr_inflow_cap (id, parameter_set_id, cap_pct)
VALUES
    ('32000000-0000-0000-0000-000000000001', '99999999-9999-9999-9999-999999990001', 75)
ON CONFLICT (parameter_set_id) DO NOTHING;

INSERT INTO risk.param_nsfr_weight (id, parameter_set_id, weight_family, category_code, category_name, weight_pct)
VALUES
    ('33000000-0000-0000-0000-000000000001', '99999999-9999-9999-9999-999999990001', 'asf', 'reg_capital', 'Regulatory capital', 100),
    ('33000000-0000-0000-0000-000000000002', '99999999-9999-9999-9999-999999990001', 'asf', 'retail_stable', 'Retail stable deposits', 95),
    ('33000000-0000-0000-0000-000000000003', '99999999-9999-9999-9999-999999990001', 'asf', 'retail_less_stable', 'Retail less stable deposits', 90),
    ('33000000-0000-0000-0000-000000000004', '99999999-9999-9999-9999-999999990001', 'asf', 'wholesale', 'Wholesale funding', 50),
    ('33000000-0000-0000-0000-000000000005', '99999999-9999-9999-9999-999999990001', 'rsf', 'cash', 'Cash', 0),
    ('33000000-0000-0000-0000-000000000006', '99999999-9999-9999-9999-999999990001', 'rsf', 'hqla_l1', 'Level 1 HQLA', 5),
    ('33000000-0000-0000-0000-000000000007', '99999999-9999-9999-9999-999999990001', 'rsf', 'corporate_loans', 'Corporate loans', 85),
    ('33000000-0000-0000-0000-000000000008', '99999999-9999-9999-9999-999999990001', 'rsf', 'residential_mortgages', 'Residential mortgages', 65)
ON CONFLICT (parameter_set_id, weight_family, category_code) DO NOTHING;

INSERT INTO risk.param_risk_weight (id, parameter_set_id, exposure_class_code, exposure_class_name, risk_weight_pct)
VALUES
    ('34000000-0000-0000-0000-000000000001', '99999999-9999-9999-9999-999999990001', 'retail', 'Retail exposures', 75),
    ('34000000-0000-0000-0000-000000000002', '99999999-9999-9999-9999-999999990001', 'sme_corp', 'SME and unrated corporate', 100),
    ('34000000-0000-0000-0000-000000000003', '99999999-9999-9999-9999-999999990001', 'mortgage', 'Residential mortgage', 35),
    ('34000000-0000-0000-0000-000000000004', '99999999-9999-9999-9999-999999990001', 'sovereign', 'Sovereign cash and securities', 0)
ON CONFLICT (parameter_set_id, exposure_class_code) DO NOTHING;

INSERT INTO risk.param_capital_threshold (id, parameter_set_id, metric_code, warning_threshold, breach_threshold, critical_threshold)
VALUES
    ('35000000-0000-0000-0000-000000000001', '99999999-9999-9999-9999-999999990001', 'car', 10.5, 10.0, 9.0),
    ('35000000-0000-0000-0000-000000000002', '99999999-9999-9999-9999-999999990001', 'cet1_ratio', 7.0, 6.5, 5.5),
    ('35000000-0000-0000-0000-000000000003', '99999999-9999-9999-9999-999999990001', 'tier1_ratio', 8.5, 8.0, 7.0),
    ('35000000-0000-0000-0000-000000000004', '99999999-9999-9999-9999-999999990001', 'lcr', 100.0, 90.0, 80.0),
    ('35000000-0000-0000-0000-000000000005', '99999999-9999-9999-9999-999999990001', 'nsfr', 100.0, 90.0, 80.0)
ON CONFLICT (parameter_set_id, metric_code) DO NOTHING;

INSERT INTO risk.param_stress_shock (id, parameter_set_id, scenario_code, shock_code, shock_name, shock_value, shock_unit)
VALUES
    ('36000000-0000-0000-0000-000000000001', '99999999-9999-9999-9999-999999990001', 'adverse', 'gdp_delta', 'GDP change vs base', -2, 'pct_points'),
    ('36000000-0000-0000-0000-000000000002', '99999999-9999-9999-9999-999999990001', 'adverse', 'inflation_delta', 'Inflation change vs base', 3, 'pct_points'),
    ('36000000-0000-0000-0000-000000000003', '99999999-9999-9999-9999-999999990001', 'adverse', 'fx_depreciation', 'GHS depreciation', 15, 'pct'),
    ('36000000-0000-0000-0000-000000000004', '99999999-9999-9999-9999-999999990001', 'severe', 'gdp_delta', 'GDP contraction', -4, 'pct_points'),
    ('36000000-0000-0000-0000-000000000005', '99999999-9999-9999-9999-999999990001', 'severe', 'inflation_level', 'Inflation level', 21, 'pct'),
    ('36000000-0000-0000-0000-000000000006', '99999999-9999-9999-9999-999999990001', 'severe', 'fx_depreciation', 'GHS depreciation', 40, 'pct'),
    ('36000000-0000-0000-0000-000000000007', '99999999-9999-9999-9999-999999990001', 'severe', 'deposit_outflow', 'Deposit outflow', 20, 'pct'),
    ('36000000-0000-0000-0000-000000000008', '99999999-9999-9999-9999-999999990001', 'severe', 'npl_multiplier', 'NPL ratio multiplier', 2, 'x')
ON CONFLICT (parameter_set_id, scenario_code, shock_code) DO NOTHING;

INSERT INTO risk.fact_macro_assumption (id, tenant_id, bank_id, scenario_id, as_of_date, variable_code, variable_name, variable_value)
VALUES
    ('37000000-0000-0000-0000-000000000001', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0001', DATE '2026-03-31', 'gdp_growth', 'GDP growth', 3.8),
    ('37000000-0000-0000-0000-000000000002', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0001', DATE '2026-03-31', 'inflation', 'Inflation', 12.0),
    ('37000000-0000-0000-0000-000000000003', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0001', DATE '2026-03-31', 'fx_ghs_usd', 'GHS per USD', 12.5),
    ('37000000-0000-0000-0000-000000000004', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0002', DATE '2026-03-31', 'gdp_growth', 'GDP growth', 1.8),
    ('37000000-0000-0000-0000-000000000005', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0002', DATE '2026-03-31', 'inflation', 'Inflation', 15.0),
    ('37000000-0000-0000-0000-000000000006', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0002', DATE '2026-03-31', 'fx_ghs_usd', 'GHS per USD', 14.4),
    ('37000000-0000-0000-0000-000000000007', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0003', DATE '2026-03-31', 'gdp_growth', 'GDP growth', -1.5),
    ('37000000-0000-0000-0000-000000000008', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0003', DATE '2026-03-31', 'inflation', 'Inflation', 21.0),
    ('37000000-0000-0000-0000-000000000009', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0003', DATE '2026-03-31', 'fx_ghs_usd', 'GHS per USD', 17.5)
ON CONFLICT (bank_id, scenario_id, as_of_date, variable_code) DO NOTHING;

INSERT INTO risk.fact_projection_input (id, tenant_id, bank_id, scenario_id, as_of_date, input_code, input_name, input_value)
VALUES
    ('38000000-0000-0000-0000-000000000001', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0001', DATE '2026-03-31', 'loan_growth_rate', 'Loan growth rate', 0.120000),
    ('38000000-0000-0000-0000-000000000002', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0001', DATE '2026-03-31', 'deposit_growth_rate', 'Deposit growth rate', 0.100000),
    ('38000000-0000-0000-0000-000000000003', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0001', DATE '2026-03-31', 'nim', 'Net interest margin', 0.067000),
    ('38000000-0000-0000-0000-000000000004', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0001', DATE '2026-03-31', 'cost_to_income', 'Cost to income ratio', 0.530000),
    ('38000000-0000-0000-0000-000000000005', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0001', DATE '2026-03-31', 'credit_loss_rate', 'Credit loss rate', 0.030000),
    ('38000000-0000-0000-0000-000000000006', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0002', DATE '2026-03-31', 'loan_growth_rate', 'Loan growth rate', 0.060000),
    ('38000000-0000-0000-0000-000000000007', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0002', DATE '2026-03-31', 'deposit_growth_rate', 'Deposit growth rate', 0.040000),
    ('38000000-0000-0000-0000-000000000008', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0002', DATE '2026-03-31', 'nim', 'Net interest margin', 0.058000),
    ('38000000-0000-0000-0000-000000000009', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0002', DATE '2026-03-31', 'cost_to_income', 'Cost to income ratio', 0.570000),
    ('38000000-0000-0000-0000-000000000010', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0002', DATE '2026-03-31', 'credit_loss_rate', 'Credit loss rate', 0.045000),
    ('38000000-0000-0000-0000-000000000011', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0003', DATE '2026-03-31', 'loan_growth_rate', 'Loan growth rate', 0.000000),
    ('38000000-0000-0000-0000-000000000012', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0003', DATE '2026-03-31', 'deposit_growth_rate', 'Deposit growth rate', -0.120000),
    ('38000000-0000-0000-0000-000000000013', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0003', DATE '2026-03-31', 'nim', 'Net interest margin', 0.046000),
    ('38000000-0000-0000-0000-000000000014', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0003', DATE '2026-03-31', 'cost_to_income', 'Cost to income ratio', 0.640000),
    ('38000000-0000-0000-0000-000000000015', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'eeeeeeee-eeee-eeee-eeee-eeeeeeee0003', DATE '2026-03-31', 'credit_loss_rate', 'Credit loss rate', 0.070000)
ON CONFLICT (bank_id, scenario_id, as_of_date, input_code) DO NOTHING;

INSERT INTO risk.fact_historical_daily_cashflows (
    id,
    tenant_id,
    bank_id,
    flow_date,
    inflow_ghs_m,
    outflow_ghs_m,
    ending_balance_ghs_m
)
SELECT
    (
        SUBSTRING(MD5('hist-' || d::TEXT), 1, 8) || '-' ||
        SUBSTRING(MD5('hist-' || d::TEXT), 9, 4) || '-' ||
        SUBSTRING(MD5('hist-' || d::TEXT), 13, 4) || '-' ||
        SUBSTRING(MD5('hist-' || d::TEXT), 17, 4) || '-' ||
        SUBSTRING(MD5('hist-' || d::TEXT), 21, 12)
    )::UUID,
    'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001',
    'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001',
    d::DATE,
    ROUND((45
      + 2 * SIN(EXTRACT(DOY FROM d) / 365.0 * 2 * PI())
      + CASE WHEN EXTRACT(DAY FROM d) IN (28,29,30,31) THEN 4 ELSE 0 END
      + CASE WHEN EXTRACT(DOW FROM d) IN (5,6) THEN -1.2 ELSE 0 END)::NUMERIC, 4),
    ROUND((43
      + 1.6 * COS(EXTRACT(DOY FROM d) / 365.0 * 2 * PI())
      + CASE WHEN EXTRACT(DAY FROM d) IN (1,2,3) THEN 2.5 ELSE 0 END
      + CASE WHEN EXTRACT(DOW FROM d) IN (1,2) THEN 0.8 ELSE 0 END)::NUMERIC, 4),
    ROUND((280
      + 0.015 * ROW_NUMBER() OVER ()
      + 6 * SIN(EXTRACT(DOY FROM d) / 365.0 * 2 * PI()))::NUMERIC, 4)
FROM generate_series(DATE '2024-04-01', DATE '2026-03-31', INTERVAL '1 day') AS d
ON CONFLICT (bank_id, flow_date) DO NOTHING;
