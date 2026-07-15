CREATE TABLE IF NOT EXISTS core.regulatory_jurisdictions (
    id UUID PRIMARY KEY,
    jurisdiction_code TEXT NOT NULL UNIQUE,
    jurisdiction_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS core.currencies (
    code CHAR(3) PRIMARY KEY,
    currency_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS core.branches (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    bank_id UUID NOT NULL REFERENCES core.banks(id) ON DELETE CASCADE,
    branch_code TEXT NOT NULL,
    branch_name TEXT NOT NULL,
    region_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (bank_id, branch_code)
);

CREATE TABLE IF NOT EXISTS core.products (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    bank_id UUID NOT NULL REFERENCES core.banks(id) ON DELETE CASCADE,
    product_code TEXT NOT NULL,
    product_name TEXT NOT NULL,
    product_type TEXT NOT NULL,
    currency_code CHAR(3) NOT NULL REFERENCES core.currencies(code),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (bank_id, product_code)
);

CREATE TABLE IF NOT EXISTS core.counterparty_types (
    id UUID PRIMARY KEY,
    counterparty_code TEXT NOT NULL UNIQUE,
    counterparty_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
