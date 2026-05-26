CREATE TABLE IF NOT EXISTS core.scenarios (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    bank_id UUID NOT NULL REFERENCES core.banks(id) ON DELETE CASCADE,
    scenario_code TEXT NOT NULL,
    scenario_name TEXT NOT NULL,
    scenario_type TEXT NOT NULL,
    description TEXT,
    is_system BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (bank_id, scenario_code)
);

CREATE TABLE IF NOT EXISTS core.as_of_calendar (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    bank_id UUID NOT NULL REFERENCES core.banks(id) ON DELETE CASCADE,
    as_of_date DATE NOT NULL,
    is_month_end BOOLEAN NOT NULL DEFAULT FALSE,
    is_quarter_end BOOLEAN NOT NULL DEFAULT FALSE,
    is_year_end BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (bank_id, as_of_date)
);
