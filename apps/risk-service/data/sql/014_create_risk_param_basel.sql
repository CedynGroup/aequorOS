CREATE TABLE IF NOT EXISTS risk.param_risk_weight (
    id UUID PRIMARY KEY,
    parameter_set_id UUID NOT NULL REFERENCES risk.parameter_sets(id) ON DELETE CASCADE,
    exposure_class_code TEXT NOT NULL,
    exposure_class_name TEXT NOT NULL,
    risk_weight_pct NUMERIC(8,4) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (parameter_set_id, exposure_class_code)
);
