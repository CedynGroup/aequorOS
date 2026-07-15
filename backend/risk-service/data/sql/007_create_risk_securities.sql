CREATE TABLE IF NOT EXISTS risk.fact_securities_holding (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    bank_id UUID NOT NULL REFERENCES core.banks(id) ON DELETE CASCADE,
    as_of_date DATE NOT NULL,
    security_code TEXT NOT NULL,
    security_type TEXT NOT NULL,
    issuer_name TEXT NOT NULL,
    carrying_amount_ghs_m NUMERIC(18,4) NOT NULL,
    hqla_level TEXT NOT NULL,
    risk_weight_pct NUMERIC(8,4) NOT NULL,
    maturity_date DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (bank_id, as_of_date, security_code)
);
