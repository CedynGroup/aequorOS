/**
 * Downloadable ingestion templates for the Excel & CSV tab.
 *
 * Two formats (the user asked for both):
 *  - `canonical`: headers ARE the canonical field names (self-documenting,
 *    provider-neutral). Ingests against the "Canonical passthrough" mapping.
 *  - `source`:    headers match the Sample Bank demo files exactly. Ingests
 *    against the "Sample Bank complete" mapping with zero setup and round-trips
 *    with the demo dataset.
 *
 * Canonical columns are derived from ENTITY_SPECS (the same data dictionary the
 * API reference renders) so the three stay in lockstep; scalar fields only —
 * object fields (`attributes`, `external_identifiers`) are not CSV columns.
 */

import { ENTITY_SPECS, type FieldSpec } from '@/components/data-engine/api-reference';

export type TemplateFormat = 'canonical' | 'source';

export type Template = {
  key: string;
  label: string;
  filename: string;
  /** The starter-mapping name that must be active for this file to ingest. */
  requiresMapping: string;
  columns: string[];
  /** Optional per-column metadata (canonical templates surface required/enum). */
  fields?: FieldSpec[];
  sampleRows: string[][];
};

const CANONICAL_MAPPING = 'Canonical passthrough (API field names)';
const SOURCE_MAPPING = 'Sample Bank complete (CSV + Excel)';

// --- canonical: columns from ENTITY_SPECS (scalars only) --------------------

function canonicalFields(entityKey: string): FieldSpec[] {
  const spec = ENTITY_SPECS.find((entity) => entity.key === entityKey);
  return (spec?.fields ?? []).filter((field) => field.type !== 'object');
}

function canonicalRow(entityKey: string, values: Record<string, string>): string[] {
  return canonicalFields(entityKey).map((field) => values[field.name] ?? '');
}

function canonicalTemplate(
  entityKey: string,
  label: string,
  samples: Record<string, string>[],
): Template {
  const fields = canonicalFields(entityKey);
  return {
    key: entityKey,
    label,
    filename: `${entityKey === 'position' ? 'positions' : `${entityKey}s`}.csv`,
    requiresMapping: CANONICAL_MAPPING,
    columns: fields.map((field) => field.name),
    fields,
    sampleRows: samples.map((sample) => canonicalRow(entityKey, sample)),
  };
}

const CANONICAL_TEMPLATES: Template[] = [
  canonicalTemplate('gl_account', 'GL accounts', [
    { source_reference: '1000', account_code: '1000', name: 'Cash and balances', account_class: 'ASSET', currency: 'GHS', balance: '24000000.00' },
    { source_reference: '2000', account_code: '2000', name: 'Customer deposits', account_class: 'LIABILITY', currency: 'GHS', balance: '180000000.00' },
  ]),
  canonicalTemplate('counterparty', 'Counterparties', [
    { source_reference: 'CUST-000001', name: 'Adjoa Marfo', counterparty_type: 'RETAIL_INDIVIDUAL', country_code: 'GH' },
    { source_reference: 'CUST-000002', name: 'Volta Agro Ltd', counterparty_type: 'CORPORATE', country_code: 'GH', rating: 'BB', rating_source: 'GCR' },
  ]),
  canonicalTemplate('product', 'Products', [
    { source_reference: 'LN.RET.PERS', product_code: 'LN.RET.PERS', name: 'Retail Personal Loan', regulatory_category: 'RETAIL_UNSECURED', risk_weight_code: '0.75' },
  ]),
  canonicalTemplate('position', 'Positions (loans, deposits, …)', [
    { source_reference: 'LN-000001', position_type: 'LOAN', currency: 'GHS', balance: '16376.30', counterparty_reference: 'CUST-000001', product_code: 'LN.RET.PERS', gl_account_code: '1301', origination_date: '2025-04-08', contractual_maturity: '2027-10-25', next_repricing_date: '2027-10-25', interest_rate: '0.2964', rate_type: 'FIXED', ifrs9_stage: '1' },
    { source_reference: 'DEP-000001', position_type: 'DEPOSIT', currency: 'GHS', balance: '319.28', counterparty_reference: 'CUST-001558', product_code: 'DEP.RET.CUR', gl_account_code: '2001', origination_date: '2025-04-30', interest_rate: '0.0' },
  ]),
];

// --- source: exact Sample Bank demo headers (round-trip with the demo) -------

function sourceTemplate(
  label: string,
  filename: string,
  columns: string[],
  rows: string[][],
): Template {
  return { key: filename, label, filename, requiresMapping: SOURCE_MAPPING, columns, sampleRows: rows };
}

const SOURCE_TEMPLATES: Template[] = [
  sourceTemplate(
    'GL accounts',
    '03_gl_accounts.csv',
    ['institution_id', 'as_of_date', 'gl_code', 'gl_name', 'gl_side', 'balance_ghs', 'currency', 'source_system'],
    [['SBL-GH-001', '2026-04-30', '1001', 'Cash on Hand', 'ASSET', '24000000.0', 'GHS', 'GL']],
  ),
  sourceTemplate(
    'Products',
    '04_products.csv',
    ['institution_id', 'product_code', 'product_name', 'product_type', 'currency', 'rate_type', 'min_tenor_months', 'max_tenor_months', 'typical_rate_low', 'typical_rate_high', 'regulatory_category', 'risk_weight'],
    [['SBL-GH-001', 'LN.RET.PERS', 'Retail Personal Loan (GHS, unsecured, 1-5yr)', 'LOAN', 'GHS', 'FIXED', '12', '60', '0.28', '0.35', 'RETAIL_UNSECURED', '0.75']],
  ),
  sourceTemplate(
    'Counterparties',
    '05_counterparties.csv',
    ['institution_id', 'counterparty_id', 'counterparty_name', 'counterparty_type', 'country', 'credit_rating', 'onboarded_date', 'kyc_status'],
    [['SBL-GH-001', 'SBL-CUST-000001', 'Adjoa Marfo', 'RETAIL_INDIVIDUAL', 'GH', '', '2019-02-08', 'COMPLETE']],
  ),
  sourceTemplate(
    'Loans',
    '06_loans.csv',
    ['institution_id', 'position_id', 'as_of_date', 'position_type', 'gl_code', 'product_code', 'counterparty_id', 'currency', 'notional_ccy', 'balance_ccy', 'balance_ghs', 'origination_date', 'contractual_maturity', 'next_repricing_date', 'interest_rate', 'rate_type', 'rate_index', 'rate_spread', 'ifrs9_stage', 'ecl_provision_ghs', 'branch_id', 'source_system', 'source_reference'],
    [['SBL-GH-001', 'SBL-LOAN-000001', '2026-04-30', 'LOAN', '1301', 'LN.RET.PERS', 'SBL-CUST-008178', 'GHS', '16376.3', '16376.3', '16376.3', '2025-04-08', '2027-10-25', '2027-10-25', '0.2964', 'FIXED', '', '', '1', '81.88', 'BR-010', 'T24', 'AA.ARRANGEMENT/1000001']],
  ),
  sourceTemplate(
    'Deposits',
    '07_deposits.csv',
    ['institution_id', 'position_id', 'as_of_date', 'position_type', 'gl_code', 'product_code', 'counterparty_id', 'currency', 'notional_ccy', 'balance_ccy', 'balance_ghs', 'origination_date', 'contractual_maturity', 'next_repricing_date', 'interest_rate', 'rate_type', 'rate_index', 'rate_spread', 'branch_id', 'source_system', 'source_reference'],
    [['SBL-GH-001', 'SBL-DEP-000001', '2026-04-30', 'DEPOSIT', '2001', 'DEP.RET.CUR', 'SBL-CUST-001558', 'GHS', '319.28', '319.28', '319.28', '2025-04-30', '', '', '0.0', 'NONE', '', '', 'BR-005', 'T24', 'ACCOUNT/2000001']],
  ),
];

export const TEMPLATES: Record<TemplateFormat, Template[]> = {
  canonical: CANONICAL_TEMPLATES,
  source: SOURCE_TEMPLATES,
};

export const REQUIRED_MAPPING: Record<TemplateFormat, string> = {
  canonical: CANONICAL_MAPPING,
  source: SOURCE_MAPPING,
};

/** RFC-4180-ish CSV: quote fields containing comma, quote, or newline. */
function csvCell(value: string): string {
  return /[",\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

export function templateCsv(template: Template): string {
  const lines = [template.columns, ...template.sampleRows].map((row) =>
    row.map(csvCell).join(','),
  );
  return `${lines.join('\n')}\n`;
}
