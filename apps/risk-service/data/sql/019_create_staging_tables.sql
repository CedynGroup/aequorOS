CREATE TABLE IF NOT EXISTS staging.raw_balance_sheet_position (
    id BIGSERIAL PRIMARY KEY,
    batch_id TEXT NOT NULL,
    tenant_code TEXT NOT NULL,
    bank_code TEXT NOT NULL,
    as_of_date DATE NOT NULL,
    category_group TEXT NOT NULL,
    category_code TEXT NOT NULL,
    category_name TEXT NOT NULL,
    amount_ghs_m NUMERIC(18,4) NOT NULL,
    currency_code CHAR(3) NOT NULL,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS staging.raw_loan_exposure (
    id BIGSERIAL PRIMARY KEY,
    batch_id TEXT NOT NULL,
    tenant_code TEXT NOT NULL,
    bank_code TEXT NOT NULL,
    as_of_date DATE NOT NULL,
    segment_code TEXT NOT NULL,
    ead_ghs_m NUMERIC(18,4) NOT NULL,
    pd NUMERIC(8,6) NOT NULL,
    lgd NUMERIC(8,6) NOT NULL,
    risk_weight_pct NUMERIC(8,4) NOT NULL,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS staging.load_issue (
    id BIGSERIAL PRIMARY KEY,
    batch_id TEXT NOT NULL,
    issue_type TEXT NOT NULL,
    issue_message TEXT NOT NULL,
    row_payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
