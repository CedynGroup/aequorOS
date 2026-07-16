/**
 * Push API reference content, hand-structured from docs/API_INTEGRATION.md
 * (the public contract). Keep the two in sync when the contract changes —
 * this module renders the readable in-console version; the markdown document
 * remains the authoritative full contract.
 */

export type PushFlowStep = {
  step: number;
  title: string;
  method: 'POST' | 'GET';
  path: string;
  summary: string;
  curl: string;
};

const H = `-H 'Content-Type: application/json' -H 'X-Org-Id: $ORG_ID' -H 'X-User-Id: $USER_ID'`;

export const PUSH_FLOW_STEPS: PushFlowStep[] = [
  {
    step: 1,
    title: 'Open a push batch',
    method: 'POST',
    path: '/banks/{bank_id}/push-batches',
    summary:
      'Declares the business date and an idempotency key. Reopening with the same key returns the same push batch; reusing a key with a different as-of date is a 409.',
    curl: `curl -s -X POST "$BASE_URL/api/v1/banks/$BANK_ID/push-batches" \\
  ${H} \\
  -d '{
    "as_of_date": "2026-04-30",
    "idempotency_key": "nightly-2026-04-30",
    "reason": "Nightly close push from middleware"
  }'`,
  },
  {
    step: 2,
    title: 'Stage record pages',
    method: 'POST',
    path: '/banks/{bank_id}/push-batches/{push_id}/records',
    summary:
      'One page per call, at most 5,000 records per page (413 beyond — split). Every key is optional; pages accumulate in order. Push only what you have.',
    curl: `curl -s -X POST "$BASE_URL/api/v1/banks/$BANK_ID/push-batches/$PUSH_ID/records" \\
  ${H} \\
  -d '{
    "entities": {
      "gl_account":   [{"source_reference": "1000", "account_code": "1000",
                        "name": "Cash and balances", "account_class": "ASSET"}],
      "position":     [{"source_reference": "LN-0001", "position_type": "LOAN",
                        "currency": "GHS", "balance": 1500000.50}]
    },
    "reference": {
      "yield_curve": [{"curve_name": "GHS_SOVEREIGN", "tenor_months": 3,
                       "rate": 0.158, "quote_date": "2026-06-01"}]
    }
  }'`,
  },
  {
    step: 3,
    title: 'Commit',
    method: 'POST',
    path: '/banks/{bank_id}/push-batches/{push_id}/commit',
    summary:
      'No body. Runs the standard ingestion pipeline with source_system = API_PUSH and returns the same batch + validation report a file upload returns. Recommitting returns the same batch (reused = true).',
    curl: `curl -s -X POST "$BASE_URL/api/v1/banks/$BANK_ID/push-batches/$PUSH_ID/commit" \\
  ${H}`,
  },
  {
    step: 4,
    title: 'Check staging status (any time)',
    method: 'GET',
    path: '/banks/{bank_id}/push-batches/{push_id}',
    summary: 'Running totals of pages and records staged, and the committed batch id once committed.',
    curl: `curl -s "$BASE_URL/api/v1/banks/$BANK_ID/push-batches/$PUSH_ID" \\
  ${H}`,
  },
];

export type FieldSpec = {
  name: string;
  type: string;
  required: boolean;
  description: string;
};

export type EntitySpec = {
  key: string;
  title: string;
  note?: string;
  fields: FieldSpec[];
};

export const ENTITY_SPECS: EntitySpec[] = [
  {
    key: 'gl_account',
    title: 'gl_account',
    fields: [
      { name: 'source_reference', type: 'string', required: true, description: 'Your stable identifier for the record (usually the account code).' },
      { name: 'account_code', type: 'string', required: true, description: 'GL account code.' },
      { name: 'name', type: 'string', required: true, description: 'Account name.' },
      { name: 'account_class', type: 'enum', required: true, description: 'ASSET, LIABILITY, EQUITY, INCOME, EXPENSE, OFF_BALANCE.' },
      { name: 'parent_account_code', type: 'string', required: false, description: 'Parent GL code (hierarchy is wired when the parent is known).' },
      { name: 'currency', type: 'string', required: false, description: 'ISO 4217 code.' },
      { name: 'balance', type: 'number', required: false, description: 'Balance as of the as-of date. Enables GL vs sub-ledger reconciliation when positions carry gl_account_code.' },
      { name: 'attributes', type: 'object', required: false, description: 'Free-form extras preserved verbatim.' },
    ],
  },
  {
    key: 'counterparty',
    title: 'counterparty',
    fields: [
      { name: 'source_reference', type: 'string', required: true, description: 'Your counterparty identifier.' },
      { name: 'name', type: 'string', required: true, description: 'Legal / display name.' },
      { name: 'counterparty_type', type: 'enum', required: true, description: 'RETAIL_INDIVIDUAL, SME, CORPORATE, BANK_OECD, BANK_NON_OECD, CENTRAL_BANK, SOVEREIGN, GOVERNMENT_ENTITY, MULTILATERAL_DEV_BANK, NBFI, OTHER.' },
      { name: 'country_code', type: 'string', required: false, description: 'ISO country code.' },
      { name: 'rating', type: 'string', required: false, description: 'External rating.' },
      { name: 'rating_source', type: 'string', required: false, description: 'Rating agency.' },
      { name: 'group_reference', type: 'string', required: false, description: 'Group / parent counterparty reference.' },
      { name: 'external_identifiers', type: 'object', required: false, description: 'e.g. {"tin": "…", "lei": "…"} — preserved verbatim.' },
      { name: 'attributes', type: 'object', required: false, description: 'Free-form extras.' },
    ],
  },
  {
    key: 'product',
    title: 'product',
    fields: [
      { name: 'source_reference', type: 'string', required: true, description: 'Your product identifier (usually the product code).' },
      { name: 'product_code', type: 'string', required: true, description: 'Product code positions reference.' },
      { name: 'name', type: 'string', required: true, description: 'Product name.' },
      { name: 'regulatory_category', type: 'string', required: false, description: "Canonical regulatory category; when omitted, the mapping config's product_mappings may supply it." },
      { name: 'risk_weight_code', type: 'string', required: false, description: 'Risk-weight bucket code.' },
      { name: 'attributes', type: 'object', required: false, description: 'Free-form extras.' },
    ],
  },
  {
    key: 'position',
    title: 'position',
    fields: [
      { name: 'source_reference', type: 'string', required: true, description: 'Your position identifier (arrangement id, deal ref, …).' },
      { name: 'position_type', type: 'enum', required: true, description: 'LOAN, DEPOSIT, SECURITY_HOLDING, DERIVATIVE, FX_HEDGE, INTEREST_RATE_SWAP, CASH, INTERBANK_PLACEMENT, INTERBANK_BORROWING, LC_GUARANTEE, COMMITMENT_UNDRAWN, OTHER_ASSET, OTHER_LIABILITY.' },
      { name: 'currency', type: 'string', required: true, description: 'ISO 4217 (validated).' },
      { name: 'balance', type: 'number', required: true, description: 'Outstanding / carrying amount as of the as-of date.' },
      { name: 'notional', type: 'number', required: false, description: 'Notional where distinct from balance (OBS, hedges, swaps).' },
      { name: 'counterparty_reference', type: 'string', required: false, description: 'Must match a counterparty.source_reference in this push or previously ingested (gap = warning, not rejection).' },
      { name: 'product_code', type: 'string', required: false, description: 'Must match a known product.product_code (dangling = error on the record).' },
      { name: 'gl_account_code', type: 'string', required: false, description: 'Must match a known gl_account.account_code; drives reconciliation.' },
      { name: 'origination_date', type: 'date', required: false, description: 'ISO date.' },
      { name: 'contractual_maturity', type: 'date', required: false, description: 'Before the as-of date ⇒ warning.' },
      { name: 'next_repricing_date', type: 'date', required: false, description: 'ISO date.' },
      { name: 'interest_rate', type: 'number', required: false, description: 'Decimal fraction (0.245 = 24.5%); outside [0, 1] ⇒ error on the record.' },
      { name: 'rate_type', type: 'enum', required: false, description: 'FIXED or FLOATING.' },
      { name: 'rate_index', type: 'string', required: false, description: 'e.g. GHREF.' },
      { name: 'rate_spread', type: 'number', required: false, description: 'Decimal fraction.' },
      { name: 'ifrs9_stage', type: 'integer', required: false, description: '1, 2, or 3.' },
      { name: 'attributes', type: 'object', required: false, description: 'Instrument specifics (hedge pair, contract rate, MtM, swap legs, ECL, branch, …) — preserved verbatim and used by module fact derivation.' },
    ],
  },
];

export type ReferenceKindSpec = { key: string; typicalFields: string };

export const REFERENCE_KINDS: ReferenceKindSpec[] = [
  { key: 'capital_structure', typicalFields: 'item, amount_ghs, tier, …' },
  { key: 'behavioral_assumptions', typicalFields: 'product_code, assumption, value, …' },
  { key: 'yield_curve', typicalFields: 'curve_name, currency, tenor_months, rate, quote_date' },
  { key: 'fx_rates_current', typicalFields: 'pair, rate, quote_date' },
  { key: 'fx_rates_historical', typicalFields: 'pair, rate, quote_date' },
  { key: 'historical_cashflows', typicalFields: 'date, inflow, outflow, …' },
  { key: 'historical_financials', typicalFields: 'month, total_assets, net_income, …' },
  { key: 'business_units', typicalFields: 'unit_id, name, …' },
  { key: 'institution', typicalFields: 'institution_id, name, …' },
];

export const VALUE_CONVENTIONS: { rule: string; detail: string }[] = [
  { rule: 'Amounts', detail: 'JSON number or plain numeric string (1500000.5 or "1500000.50"). No currency symbols or thousands separators.' },
  { rule: 'Rates', detail: 'Decimal fractions — 0.245 means 24.5%. Never "24.5%" and never bare percent numbers.' },
  { rule: 'Dates', detail: 'ISO "YYYY-MM-DD" strings.' },
  { rule: 'Nulls', detail: 'JSON null (or omit the field). Empty strings are treated as null.' },
  { rule: 'Unknown fields', detail: "Ignored unless captured via attributes or a mapping config's attribute_columns." },
  { rule: 'Bad records', detail: 'Do NOT fail the request: they land in the batch\'s translation failures (raw record preserved) and the rest proceeds — same semantics as file ingestion.' },
];

/** Self-contained, runnable end-to-end example (bash + curl + jq). */
export const EXAMPLE_SCRIPT = `#!/usr/bin/env bash
# AequorOS push API — end-to-end example. Requires: bash, curl, jq.
set -euo pipefail

BASE_URL="http://127.0.0.1:8003/api/v1"          # your endpoint
ORG_ID="11111111-1111-4111-8111-111111111111"    # X-Org-Id
USER_ID="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"   # X-User-Id (service account)
AS_OF="2026-04-30"

hdr=(-H "Content-Type: application/json" -H "X-Org-Id: $ORG_ID" -H "X-User-Id: $USER_ID")

# The bank you are pushing for (first bank in this org shown here):
BANK_ID=$(curl -s "$BASE_URL/banks" "\${hdr[@]}" | jq -r '.banks[0].id')

# 1. Open a push batch. The idempotency key makes retries safe.
PUSH_ID=$(curl -s -X POST "$BASE_URL/banks/$BANK_ID/push-batches" "\${hdr[@]}" \\
  -d "{\\"as_of_date\\":\\"$AS_OF\\",\\"idempotency_key\\":\\"example-$AS_OF\\",\\"reason\\":\\"Example push\\"}" \\
  | jq -r '.push_batch_id')

# 2. Stage a page of records (<= 5,000 per page; every key optional).
curl -s -X POST "$BASE_URL/banks/$BANK_ID/push-batches/$PUSH_ID/records" "\${hdr[@]}" \\
  -d '{
    "entities": {
      "gl_account": [{"source_reference":"1000","account_code":"1000","name":"Cash and balances","account_class":"ASSET"}],
      "position":   [{"source_reference":"LN-0001","position_type":"LOAN","currency":"GHS","balance":1500000.50}]
    }
  }' > /dev/null

# 3. Commit — runs the full ingestion pipeline, returns the batch + report.
curl -s -X POST "$BASE_URL/banks/$BANK_ID/push-batches/$PUSH_ID/commit" "\${hdr[@]}" \\
  | jq '{status: .batch.status, accepted: .batch.records_accepted, warnings: .batch.records_warning, errors: .batch.records_error}'
`;
