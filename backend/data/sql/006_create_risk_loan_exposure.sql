CREATE TABLE IF NOT EXISTS risk.fact_loan_exposure (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    bank_id UUID NOT NULL REFERENCES core.banks(id) ON DELETE CASCADE,
    as_of_date DATE NOT NULL,
    segment_code TEXT NOT NULL,
    product_id UUID REFERENCES core.products(id),
    ead_ghs_m NUMERIC(18,4) NOT NULL,
    pd NUMERIC(8,6) NOT NULL,
    lgd NUMERIC(8,6) NOT NULL,
    risk_weight_pct NUMERIC(8,4) NOT NULL,
    npl_ratio NUMERIC(8,6),
    source_system TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (bank_id, as_of_date, segment_code)
);
