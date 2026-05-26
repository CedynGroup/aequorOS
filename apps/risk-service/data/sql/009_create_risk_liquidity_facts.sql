CREATE TABLE IF NOT EXISTS risk.fact_lcr_outflow_base (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    bank_id UUID NOT NULL REFERENCES core.banks(id) ON DELETE CASCADE,
    as_of_date DATE NOT NULL,
    outflow_category_code TEXT NOT NULL,
    outflow_category_name TEXT NOT NULL,
    exposure_amount_ghs_m NUMERIC(18,4) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (bank_id, as_of_date, outflow_category_code)
);

CREATE TABLE IF NOT EXISTS risk.fact_lcr_inflow_base (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    bank_id UUID NOT NULL REFERENCES core.banks(id) ON DELETE CASCADE,
    as_of_date DATE NOT NULL,
    inflow_category_code TEXT NOT NULL,
    inflow_category_name TEXT NOT NULL,
    exposure_amount_ghs_m NUMERIC(18,4) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (bank_id, as_of_date, inflow_category_code)
);

CREATE TABLE IF NOT EXISTS risk.fact_nsfr_asf_base (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    bank_id UUID NOT NULL REFERENCES core.banks(id) ON DELETE CASCADE,
    as_of_date DATE NOT NULL,
    asf_category_code TEXT NOT NULL,
    asf_category_name TEXT NOT NULL,
    amount_ghs_m NUMERIC(18,4) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (bank_id, as_of_date, asf_category_code)
);

CREATE TABLE IF NOT EXISTS risk.fact_nsfr_rsf_base (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    bank_id UUID NOT NULL REFERENCES core.banks(id) ON DELETE CASCADE,
    as_of_date DATE NOT NULL,
    rsf_category_code TEXT NOT NULL,
    rsf_category_name TEXT NOT NULL,
    amount_ghs_m NUMERIC(18,4) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (bank_id, as_of_date, rsf_category_code)
);
