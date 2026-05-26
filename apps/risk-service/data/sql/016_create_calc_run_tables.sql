CREATE TABLE IF NOT EXISTS calc.calc_run (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    bank_id UUID NOT NULL REFERENCES core.banks(id) ON DELETE CASCADE,
    module_code TEXT NOT NULL,
    scenario_id UUID REFERENCES core.scenarios(id) ON DELETE SET NULL,
    parameter_set_id UUID REFERENCES risk.parameter_sets(id) ON DELETE SET NULL,
    as_of_date DATE NOT NULL,
    run_status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    context_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS calc.calc_metric_result (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES calc.calc_run(id) ON DELETE CASCADE,
    metric_code TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value NUMERIC(18,6) NOT NULL,
    unit_code TEXT NOT NULL,
    threshold_min NUMERIC(18,6),
    threshold_warn NUMERIC(18,6),
    metric_status TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, metric_code)
);

CREATE TABLE IF NOT EXISTS calc.calc_line_item (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES calc.calc_run(id) ON DELETE CASCADE,
    metric_code TEXT NOT NULL,
    line_code TEXT NOT NULL,
    line_description TEXT NOT NULL,
    amount NUMERIC(18,6) NOT NULL,
    formula_ref TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, metric_code, line_code)
);

CREATE TABLE IF NOT EXISTS calc.calc_validation_result (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES calc.calc_run(id) ON DELETE CASCADE,
    rule_code TEXT NOT NULL,
    result_status TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, rule_code)
);

CREATE TABLE IF NOT EXISTS calc.expected_metric_assertion (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    bank_id UUID NOT NULL REFERENCES core.banks(id) ON DELETE CASCADE,
    scenario_id UUID REFERENCES core.scenarios(id) ON DELETE SET NULL,
    as_of_date DATE NOT NULL,
    metric_code TEXT NOT NULL,
    expected_value NUMERIC(18,6) NOT NULL,
    tolerance_pct NUMERIC(8,6) NOT NULL,
    assertion_note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (bank_id, scenario_id, as_of_date, metric_code)
);
