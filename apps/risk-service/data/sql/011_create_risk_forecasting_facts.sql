CREATE TABLE IF NOT EXISTS risk.fact_historical_daily_cashflows (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    bank_id UUID NOT NULL REFERENCES core.banks(id) ON DELETE CASCADE,
    flow_date DATE NOT NULL,
    inflow_ghs_m NUMERIC(18,4) NOT NULL,
    outflow_ghs_m NUMERIC(18,4) NOT NULL,
    ending_balance_ghs_m NUMERIC(18,4) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (bank_id, flow_date)
);

CREATE TABLE IF NOT EXISTS risk.fact_macro_assumption (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    bank_id UUID NOT NULL REFERENCES core.banks(id) ON DELETE CASCADE,
    scenario_id UUID REFERENCES core.scenarios(id) ON DELETE CASCADE,
    as_of_date DATE NOT NULL,
    variable_code TEXT NOT NULL,
    variable_name TEXT NOT NULL,
    variable_value NUMERIC(18,6) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (bank_id, scenario_id, as_of_date, variable_code)
);

CREATE TABLE IF NOT EXISTS risk.fact_projection_input (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    bank_id UUID NOT NULL REFERENCES core.banks(id) ON DELETE CASCADE,
    scenario_id UUID REFERENCES core.scenarios(id) ON DELETE CASCADE,
    as_of_date DATE NOT NULL,
    input_code TEXT NOT NULL,
    input_name TEXT NOT NULL,
    input_value NUMERIC(18,6) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (bank_id, scenario_id, as_of_date, input_code)
);
