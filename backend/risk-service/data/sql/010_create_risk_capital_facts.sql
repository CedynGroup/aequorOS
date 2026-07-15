CREATE TABLE IF NOT EXISTS risk.fact_capital_components (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    bank_id UUID NOT NULL REFERENCES core.banks(id) ON DELETE CASCADE,
    as_of_date DATE NOT NULL,
    component_type TEXT NOT NULL,
    component_code TEXT NOT NULL,
    component_name TEXT NOT NULL,
    amount_ghs_m NUMERIC(18,4) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (bank_id, as_of_date, component_code)
);

CREATE TABLE IF NOT EXISTS risk.fact_market_risk_position (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    bank_id UUID NOT NULL REFERENCES core.banks(id) ON DELETE CASCADE,
    as_of_date DATE NOT NULL,
    net_long_fx_ghs_m NUMERIC(18,4) NOT NULL,
    net_short_fx_ghs_m NUMERIC(18,4) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (bank_id, as_of_date)
);

CREATE TABLE IF NOT EXISTS risk.fact_operational_risk_income (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    bank_id UUID NOT NULL REFERENCES core.banks(id) ON DELETE CASCADE,
    fiscal_year INT NOT NULL,
    gross_income_ghs_m NUMERIC(18,4) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (bank_id, fiscal_year)
);
