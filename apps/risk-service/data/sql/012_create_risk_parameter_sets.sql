CREATE TABLE IF NOT EXISTS risk.parameter_sets (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    bank_id UUID NOT NULL REFERENCES core.banks(id) ON DELETE CASCADE,
    parameter_set_code TEXT NOT NULL,
    parameter_set_name TEXT NOT NULL,
    jurisdiction_code TEXT NOT NULL,
    effective_from DATE NOT NULL,
    effective_to DATE,
    approved_by TEXT,
    approval_timestamp TIMESTAMPTZ,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (bank_id, parameter_set_code, effective_from)
);
