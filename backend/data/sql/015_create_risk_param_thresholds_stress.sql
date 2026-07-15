CREATE TABLE IF NOT EXISTS risk.param_capital_threshold (
    id UUID PRIMARY KEY,
    parameter_set_id UUID NOT NULL REFERENCES risk.parameter_sets(id) ON DELETE CASCADE,
    metric_code TEXT NOT NULL,
    warning_threshold NUMERIC(12,6),
    breach_threshold NUMERIC(12,6),
    critical_threshold NUMERIC(12,6),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (parameter_set_id, metric_code)
);

CREATE TABLE IF NOT EXISTS risk.param_stress_shock (
    id UUID PRIMARY KEY,
    parameter_set_id UUID NOT NULL REFERENCES risk.parameter_sets(id) ON DELETE CASCADE,
    scenario_code TEXT NOT NULL,
    shock_code TEXT NOT NULL,
    shock_name TEXT NOT NULL,
    shock_value NUMERIC(18,6) NOT NULL,
    shock_unit TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (parameter_set_id, scenario_code, shock_code)
);
