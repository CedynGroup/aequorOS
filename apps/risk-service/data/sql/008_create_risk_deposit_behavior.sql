CREATE TABLE IF NOT EXISTS risk.fact_deposit_behavior (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    bank_id UUID NOT NULL REFERENCES core.banks(id) ON DELETE CASCADE,
    as_of_date DATE NOT NULL,
    segment_code TEXT NOT NULL,
    segment_name TEXT NOT NULL,
    balance_ghs_m NUMERIC(18,4) NOT NULL,
    is_core BOOLEAN NOT NULL,
    stability_band TEXT NOT NULL,
    asf_weight_pct NUMERIC(8,4),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (bank_id, as_of_date, segment_code)
);
