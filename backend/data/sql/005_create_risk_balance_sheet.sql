CREATE TABLE IF NOT EXISTS risk.fact_balance_sheet_position (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    bank_id UUID NOT NULL REFERENCES core.banks(id) ON DELETE CASCADE,
    as_of_date DATE NOT NULL,
    category_group TEXT NOT NULL,
    category_code TEXT NOT NULL,
    category_name TEXT NOT NULL,
    amount_ghs_m NUMERIC(18,4) NOT NULL,
    currency_code CHAR(3) NOT NULL REFERENCES core.currencies(code),
    source_system TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (bank_id, as_of_date, category_code)
);

ALTER TABLE risk.fact_balance_sheet_position
    ADD CONSTRAINT chk_balance_sheet_category_group
    CHECK (category_group IN ('asset', 'liability', 'capital'));
