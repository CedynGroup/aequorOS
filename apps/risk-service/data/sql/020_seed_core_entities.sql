INSERT INTO core.tenants (id, tenant_code, tenant_name)
VALUES
    ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'sample-tenant', 'Sample Tenant')
ON CONFLICT (tenant_code) DO NOTHING;

INSERT INTO core.banks (id, tenant_id, bank_code, bank_name, regulator_code, base_currency, founded_year, country_code)
VALUES
    (
        'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001',
        'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001',
        'SBL-GH',
        'Sample Bank Limited',
        'BOG',
        'GHS',
        2005,
        'GH'
    )
ON CONFLICT (tenant_id, bank_code) DO NOTHING;

INSERT INTO core.regulatory_jurisdictions (id, jurisdiction_code, jurisdiction_name)
VALUES
    ('cccccccc-cccc-cccc-cccc-cccccccc0001', 'BOG', 'Bank of Ghana')
ON CONFLICT (jurisdiction_code) DO NOTHING;

INSERT INTO core.currencies (code, currency_name)
VALUES
    ('GHS', 'Ghana Cedi'),
    ('USD', 'US Dollar'),
    ('EUR', 'Euro'),
    ('GBP', 'Pound Sterling')
ON CONFLICT (code) DO NOTHING;

INSERT INTO core.counterparty_types (id, counterparty_code, counterparty_name)
VALUES
    ('dddddddd-dddd-dddd-dddd-dddddddd0001', 'retail', 'Retail'),
    ('dddddddd-dddd-dddd-dddd-dddddddd0002', 'sme', 'Small and Medium Enterprise'),
    ('dddddddd-dddd-dddd-dddd-dddddddd0003', 'corporate', 'Corporate'),
    ('dddddddd-dddd-dddd-dddd-dddddddd0004', 'sovereign', 'Sovereign')
ON CONFLICT (counterparty_code) DO NOTHING;

INSERT INTO core.scenarios (id, tenant_id, bank_id, scenario_code, scenario_name, scenario_type, description, is_system)
VALUES
    ('eeeeeeee-eeee-eeee-eeee-eeeeeeee0001', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'base', 'Base Case', 'baseline', 'Management expected case', TRUE),
    ('eeeeeeee-eeee-eeee-eeee-eeeeeeee0002', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'adverse', 'Adverse', 'stress', 'Moderate stress scenario', TRUE),
    ('eeeeeeee-eeee-eeee-eeee-eeeeeeee0003', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'severe', 'Severely Adverse', 'stress', 'Severe stress scenario', TRUE),
    ('eeeeeeee-eeee-eeee-eeee-eeeeeeee0004', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'idio', 'Idiosyncratic Stress', 'stress', 'Bank-specific stress', TRUE),
    ('eeeeeeee-eeee-eeee-eeee-eeeeeeee0005', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'market', 'Market-wide Stress', 'stress', 'Market-wide stress', TRUE),
    ('eeeeeeee-eeee-eeee-eeee-eeeeeeee0006', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'combined', 'Combined Stress', 'stress', 'Combined idiosyncratic and market stress', TRUE)
ON CONFLICT (bank_id, scenario_code) DO NOTHING;

INSERT INTO core.as_of_calendar (id, tenant_id, bank_id, as_of_date, is_month_end, is_quarter_end, is_year_end)
VALUES
    ('ffffffff-ffff-ffff-ffff-ffffffff0001', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', DATE '2026-03-31', TRUE, TRUE, FALSE)
ON CONFLICT (bank_id, as_of_date) DO NOTHING;

INSERT INTO core.branches (id, tenant_id, bank_id, branch_code, branch_name, region_name)
VALUES
    ('00000000-0000-0000-0000-000000000101', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'BR001', 'Accra Central', 'Greater Accra'),
    ('00000000-0000-0000-0000-000000000102', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'BR002', 'Tema', 'Greater Accra'),
    ('00000000-0000-0000-0000-000000000103', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'BR003', 'Kasoa', 'Central'),
    ('00000000-0000-0000-0000-000000000104', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'BR004', 'Cape Coast', 'Central'),
    ('00000000-0000-0000-0000-000000000105', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'BR005', 'Takoradi', 'Western'),
    ('00000000-0000-0000-0000-000000000106', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'BR006', 'Kumasi Main', 'Ashanti'),
    ('00000000-0000-0000-0000-000000000107', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'BR007', 'Suame', 'Ashanti'),
    ('00000000-0000-0000-0000-000000000108', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'BR008', 'Sunyani', 'Bono'),
    ('00000000-0000-0000-0000-000000000109', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'BR009', 'Koforidua', 'Eastern'),
    ('00000000-0000-0000-0000-000000000110', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'BR010', 'Ho', 'Volta'),
    ('00000000-0000-0000-0000-000000000111', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'BR011', 'Tamale', 'Northern'),
    ('00000000-0000-0000-0000-000000000112', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'BR012', 'Bolgatanga', 'Upper East'),
    ('00000000-0000-0000-0000-000000000113', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'BR013', 'Wa', 'Upper West'),
    ('00000000-0000-0000-0000-000000000114', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'BR014', 'Techiman', 'Bono East'),
    ('00000000-0000-0000-0000-000000000115', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'BR015', 'Nkawkaw', 'Eastern'),
    ('00000000-0000-0000-0000-000000000116', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'BR016', 'Winneba', 'Central'),
    ('00000000-0000-0000-0000-000000000117', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'BR017', 'Madina', 'Greater Accra'),
    ('00000000-0000-0000-0000-000000000118', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'BR018', 'Akim Oda', 'Eastern')
ON CONFLICT (bank_id, branch_code) DO NOTHING;

INSERT INTO core.products (id, tenant_id, bank_id, product_code, product_name, product_type, currency_code)
VALUES
    ('11111111-1111-1111-1111-111111110001', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'LOAN-RET', 'Retail Loans', 'asset', 'GHS'),
    ('11111111-1111-1111-1111-111111110002', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'LOAN-SME', 'SME Loans', 'asset', 'GHS'),
    ('11111111-1111-1111-1111-111111110003', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'LOAN-CORP', 'Corporate Loans', 'asset', 'GHS'),
    ('11111111-1111-1111-1111-111111110004', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'LOAN-MORT', 'Residential Mortgages', 'asset', 'GHS'),
    ('11111111-1111-1111-1111-111111110005', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'DEP-RET', 'Retail Deposits', 'liability', 'GHS'),
    ('11111111-1111-1111-1111-111111110006', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0001', 'DEP-WHS', 'Wholesale Deposits', 'liability', 'GHS')
ON CONFLICT (bank_id, product_code) DO NOTHING;
