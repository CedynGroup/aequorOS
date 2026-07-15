CREATE TABLE IF NOT EXISTS risk.param_lcr_runoff_rate (
    id UUID PRIMARY KEY,
    parameter_set_id UUID NOT NULL REFERENCES risk.parameter_sets(id) ON DELETE CASCADE,
    runoff_category_code TEXT NOT NULL,
    runoff_category_name TEXT NOT NULL,
    base_rate_pct NUMERIC(8,4) NOT NULL,
    stressed_rate_pct NUMERIC(8,4) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (parameter_set_id, runoff_category_code)
);

CREATE TABLE IF NOT EXISTS risk.param_lcr_inflow_cap (
    id UUID PRIMARY KEY,
    parameter_set_id UUID NOT NULL REFERENCES risk.parameter_sets(id) ON DELETE CASCADE,
    cap_pct NUMERIC(8,4) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (parameter_set_id)
);

CREATE TABLE IF NOT EXISTS risk.param_nsfr_weight (
    id UUID PRIMARY KEY,
    parameter_set_id UUID NOT NULL REFERENCES risk.parameter_sets(id) ON DELETE CASCADE,
    weight_family TEXT NOT NULL,
    category_code TEXT NOT NULL,
    category_name TEXT NOT NULL,
    weight_pct NUMERIC(8,4) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (parameter_set_id, weight_family, category_code)
);
