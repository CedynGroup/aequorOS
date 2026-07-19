'use client';

/**
 * Data Engine API layer: generated IngestionApi client, TanStack Query hooks,
 * and starter mapping templates authored against the Sample Bank demo files
 * (data/02_Customer_Positions.xlsx, 03_gl_accounts.csv, 04_products.csv).
 *
 * Kept separate from lib/api/client.ts and hooks.ts so Data Engine work does
 * not contend with the regulatory modules' files.
 */

import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import {
  IngestionApi,
  type DataActivationRead,
  type IngestionBatchCreate,
  type ListIngestionBatchesSourceSystemEnum,
  type MappingConfig,
} from '@aequoros/risk-service-api';
import { apiCall, configuration } from './client';

export type IngestionSourceSystem = ListIngestionBatchesSourceSystemEnum;

// Reuse the shared, token-bearing Configuration so Data Engine calls authenticate
// with the same backend JWT as every other module (no separate unauthenticated client).
export const ingestionApi = new IngestionApi(configuration);


// ---------------------------------------------------------------------------
// Starter mapping template for the Sample Bank demo dataset.
//
// Column names below are the REAL headers of the demo files. Table names are
// resolved exact -> case-insensitive -> normalized, and every mapping lists
// the CSV file stem plus the workbook sheet name as aliases, so ONE template
// serves both the /data CSV drops and the multi-sheet Excel workbooks.
//
// The mapping-config store enforces a single active config per
// (bank, source system) and the console ingests with the active config, so
// the templates are merged into one combined mapping: tables absent from a
// given upload are skipped with a table_not_found warning, and a file where
// NOTHING matches is rejected with a found-versus-expected diagnosis.
// ---------------------------------------------------------------------------

const glAccountMapping = {
  sourceTable: '03_gl_accounts',
  sourceTableAliases: ['General_Ledger'],
  fields: {
    source_reference: 'gl_code',
    account_code: 'gl_code',
    name: 'gl_name',
    account_class: 'gl_side',
    currency: 'currency',
    balance: 'balance_ghs',
  },
};

const productMapping = {
  sourceTable: '04_products',
  sourceTableAliases: ['Product_Catalog'],
  fields: {
    source_reference: 'product_code',
    product_code: 'product_code',
    name: 'product_name',
    regulatory_category: 'regulatory_category',
    risk_weight_code: 'risk_weight',
  },
  attributeColumns: [
    'product_type',
    'rate_type',
    'currency',
    'min_tenor_months',
    'max_tenor_months',
    'typical_rate_low',
    'typical_rate_high',
  ],
};

const counterpartyMapping = {
  sourceTable: 'Counterparties_Sample',
  sourceTableAliases: ['05_counterparties'],
  fields: {
    source_reference: 'counterparty_id',
    name: 'counterparty_name',
    counterparty_type: 'counterparty_type',
    country_code: 'country',
    rating: 'credit_rating',
  },
  attributeColumns: ['kyc_status', 'onboarded_date'],
};

// One position mapping serves every position sheet/file: loans, deposits,
// securities, interbank, the OBS register, the FX hedge book, and the
// interest-rate swap blotter. Fallback column lists bridge header differences
// (the LC/guarantee sheet has no balance_ccy and carries issue/expiry dates
// instead of origination/maturity; hedges and swaps carry trade/maturity
// dates); columns the canonical schema has no dedicated home for ride along
// in attributes (hedge pair/rate/MtM/effectiveness, swap legs, ...).
const positionMapping = {
  sourceTable: 'Loans',
  sourceTableAliases: [
    '06_loans',
    'Deposits',
    '07_deposits',
    'Government_Securities',
    '08_securities',
    'Interbank',
    '09_interbank',
    'LC_and_Guarantees',
    '10_off_balance_sheet',
    'FX_Hedges',
    '18_fx_hedges',
    'Interest_Rate_Swaps',
    '19_interest_rate_swaps',
  ],
  fields: {
    source_reference: 'position_id',
    position_type: 'position_type',
    currency: 'currency',
    balance: ['balance_ccy', 'notional_ccy'],
    notional: 'notional_ccy',
    counterparty_reference: 'counterparty_id',
    product_code: 'product_code',
    gl_account_code: 'gl_code',
    origination_date: ['origination_date', 'issue_date', 'trade_date'],
    contractual_maturity: ['contractual_maturity', 'expiry_date', 'maturity_date'],
    next_repricing_date: 'next_repricing_date',
    interest_rate: 'interest_rate',
    rate_type: 'rate_type',
    rate_index: 'rate_index',
    rate_spread: 'rate_spread',
    ifrs9_stage: 'ifrs9_stage',
  },
  attributeColumns: [
    'balance_ghs',
    'notional_ghs',
    'ecl_provision_ghs',
    'branch_id',
    'issuer',
    'isin',
    'credit_conversion_factor',
    'credit_equivalent_ghs',
    // FX hedge book (18_fx_hedges / FX_Hedges)
    'hedge_id',
    'instrument',
    'currency_pair',
    'buy_currency',
    'sell_currency',
    'notional_currency',
    'contract_rate',
    'mtm_ghs',
    'prospective_r2',
    'dollar_offset_ratio',
    // Interest-rate swaps (19_interest_rate_swaps / Interest_Rate_Swaps)
    'swap_id',
    'direction',
    'pay_rate_pct',
    'receive_index',
    'tenor_years',
    'counterparty_ref',
    'source_system',
    'source_reference',
  ],
};

const referenceMappings = {
  institution: {
    sourceTable: '01_institution',
    sourceTableAliases: ['Institution'],
    datasetKind: 'institution',
  },
  business_units: {
    sourceTable: '02_business_units',
    sourceTableAliases: ['Branches'],
    datasetKind: 'business_units',
  },
  capital_structure: {
    sourceTable: '11_capital_structure',
    sourceTableAliases: ['Capital_Structure'],
    datasetKind: 'capital_structure',
  },
  behavioral_assumptions: {
    sourceTable: '12_behavioral_assumptions',
    sourceTableAliases: ['Behavioral_Assumptions'],
    datasetKind: 'behavioral_assumptions',
  },
  yield_curves: {
    sourceTable: '13_yield_curves',
    sourceTableAliases: ['Yield_Curves'],
    datasetKind: 'yield_curve',
  },
  fx_rates_current: {
    sourceTable: '14_fx_rates_current',
    sourceTableAliases: ['FX_Rates_Current'],
    datasetKind: 'fx_rates_current',
  },
  fx_rates_historical: {
    sourceTable: '15_fx_rates_historical',
    sourceTableAliases: ['FX_Rates_Historical'],
    datasetKind: 'fx_rates_historical',
  },
  historical_cashflows: {
    sourceTable: '16_historical_cashflows',
    sourceTableAliases: ['Daily_Cashflows'],
    datasetKind: 'historical_cashflows',
  },
  historical_financials: {
    sourceTable: '17_historical_financials',
    sourceTableAliases: ['Monthly_Financials'],
    datasetKind: 'historical_financials',
  },
} satisfies MappingConfig['referenceMappings'];

// Canonical passthrough: identity mapping for files whose headers already use
// the AequorOS canonical field names — the format of the downloadable canonical
// templates. Source table names match those template filenames.
const canonicalGlMapping = {
  sourceTable: 'gl_accounts',
  fields: {
    source_reference: 'source_reference',
    account_code: 'account_code',
    name: 'name',
    account_class: 'account_class',
    parent_account_code: 'parent_account_code',
    currency: 'currency',
    balance: 'balance',
  },
};

const canonicalCounterpartyMapping = {
  sourceTable: 'counterparties',
  fields: {
    source_reference: 'source_reference',
    name: 'name',
    counterparty_type: 'counterparty_type',
    country_code: 'country_code',
    rating: 'rating',
    rating_source: 'rating_source',
    group_reference: 'group_reference',
  },
};

const canonicalProductMapping = {
  sourceTable: 'products',
  fields: {
    source_reference: 'source_reference',
    product_code: 'product_code',
    name: 'name',
    regulatory_category: 'regulatory_category',
    risk_weight_code: 'risk_weight_code',
  },
};

const canonicalPositionMapping = {
  sourceTable: 'positions',
  fields: {
    source_reference: 'source_reference',
    position_type: 'position_type',
    currency: 'currency',
    balance: 'balance',
    notional: 'notional',
    counterparty_reference: 'counterparty_reference',
    product_code: 'product_code',
    gl_account_code: 'gl_account_code',
    origination_date: 'origination_date',
    contractual_maturity: 'contractual_maturity',
    next_repricing_date: 'next_repricing_date',
    interest_rate: 'interest_rate',
    rate_type: 'rate_type',
    rate_index: 'rate_index',
    rate_spread: 'rate_spread',
    ifrs9_stage: 'ifrs9_stage',
  },
};

export type StarterTemplate = {
  key: string;
  name: string;
  description: string;
  ingests: string[];
  config: MappingConfig;
};

export const STARTER_TEMPLATES: StarterTemplate[] = [
  {
    key: 'sample-bank-complete',
    name: 'Sample Bank complete (CSV + Excel)',
    description:
      'One mapping for the whole Sample Bank dataset: GL, products, counterparties, and every position book — including the treasury FX hedge book and interest-rate swap blotter — plus the reference datasets (capital, behavioral, market data, history). Aliases cover both the CSV files and the workbook sheets — upload the files in any order. Re-activating creates a new mapping-config version.',
    ingests: [
      '01_Balance_Sheet_Master.xlsx · 03_gl_accounts.csv · 04_products.csv',
      '02_Customer_Positions.xlsx · 06_loans.csv · 07_deposits.csv',
      '03_Securities_and_Interbank.xlsx',
      '04_Capital_and_Behavioral.xlsx · 11/12_*.csv',
      '05_Market_Data.xlsx · 13_yield_curves.csv',
      '06_Historical_Data.xlsx · 16/17_*.csv',
      '18_fx_hedges.csv · 19_interest_rate_swaps.csv',
    ],
    config: {
      fieldMappings: {
        gl_account: glAccountMapping,
        product: productMapping,
        counterparty: counterpartyMapping,
        position: positionMapping,
      },
      referenceMappings,
      // LC / GUARANTEE rows normalize onto the canonical LC_GUARANTEE type;
      // deposits' rate_type "NONE" is a recognized no-value placeholder and
      // needs no mapping.
      enumMappings: {
        position_type: { LC: 'LC_GUARANTEE', GUARANTEE: 'LC_GUARANTEE' },
      },
      productMappings: {},
      options: {},
    },
  },
  {
    key: 'canonical-passthrough',
    name: 'Canonical passthrough (API field names)',
    description:
      'Identity mapping for files whose headers already use AequorOS canonical field names — the format of the downloadable canonical templates. Upload gl_accounts / counterparties / products / positions with canonical headers and they ingest with no column translation. Provider-neutral: map your core system to our spec once.',
    ingests: ['gl_accounts.csv', 'counterparties.csv', 'products.csv', 'positions.csv'],
    config: {
      fieldMappings: {
        gl_account: canonicalGlMapping,
        product: canonicalProductMapping,
        counterparty: canonicalCounterpartyMapping,
        position: canonicalPositionMapping,
      },
      enumMappings: {},
      productMappings: {},
      options: {},
    },
  },
];

// ---------------------------------------------------------------------------
// Query hooks
// ---------------------------------------------------------------------------

export function useMappingConfigs(bankId: string | undefined) {
  return useQuery({
    queryKey: ['de-mapping-configs', bankId],
    queryFn: () =>
      apiCall(() => ingestionApi.listMappingConfigs({ bankId: bankId! })),
    enabled: Boolean(bankId),
  });
}

export function useIngestionBatches(
  bankId: string | undefined,
  sourceSystem?: IngestionSourceSystem,
) {
  return useQuery({
    queryKey: ['de-batches', bankId, sourceSystem ?? 'all'],
    queryFn: () =>
      apiCall(() =>
        ingestionApi.listIngestionBatches({ bankId: bankId!, sourceSystem }),
      ),
    enabled: Boolean(bankId),
  });
}

/** Per-source ingestion rollup + canonical model counts + activation history. */
export function useIngestionSummary(bankId: string | undefined) {
  return useQuery({
    queryKey: ['de-summary', bankId],
    queryFn: () =>
      apiCall(() => ingestionApi.getIngestionSummary({ bankId: bankId! })),
    enabled: Boolean(bankId),
  });
}

export function useIngestionBatch(
  bankId: string | undefined,
  batchId: string | undefined,
) {
  return useQuery({
    queryKey: ['de-batch', bankId, batchId],
    queryFn: () =>
      apiCall(() =>
        ingestionApi.getIngestionBatch({ bankId: bankId!, batchId: batchId! }),
      ),
    enabled: Boolean(bankId && batchId),
  });
}

export function useTranslationFailures(
  bankId: string | undefined,
  batchId: string | undefined,
) {
  return useQuery({
    queryKey: ['de-failures', bankId, batchId],
    queryFn: () =>
      apiCall(() =>
        ingestionApi.listTranslationFailures({
          bankId: bankId!,
          batchId: batchId!,
        }),
      ),
    enabled: Boolean(bankId && batchId),
  });
}

/**
 * One page of canonical positions (server pagination, 100 rows per window).
 * The response carries `total`/`limit`/`offset` so callers can page.
 */
export function useCanonicalPositions(bankId: string | undefined, offset = 0) {
  return useQuery({
    queryKey: ['de-positions', bankId, offset],
    queryFn: () =>
      apiCall(() =>
        ingestionApi.listCanonicalPositions({ bankId: bankId!, offset }),
      ),
    enabled: Boolean(bankId),
    placeholderData: keepPreviousData,
  });
}

export function useLineageWalk(lineageId: string | undefined) {
  return useQuery({
    queryKey: ['de-lineage', lineageId],
    queryFn: () =>
      apiCall(() => ingestionApi.walkLineage({ lineageId: lineageId! })),
    enabled: Boolean(lineageId),
  });
}

export function useActivateTemplate(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (template: StarterTemplate) =>
      apiCall(() =>
        ingestionApi.createMappingConfig({
          bankId: bankId!,
          mappingConfigCreate: {
            sourceSystem: 'EXCEL_CSV',
            name: template.name,
            config: template.config,
            activate: true,
            reason: `Activated starter template "${template.key}" from the Data Engine console.`,
          },
        }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['de-mapping-configs', bankId] });
    },
  });
}

/**
 * Activate the bank's uploaded data: derive the module fact set for one as-of
 * date and recompute all six modules. On success the reporting-period list is
 * invalidated so the header selector picks up the new period, and every module
 * dashboard query refetches against it.
 */
export function useActivateBankData(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation<
    DataActivationRead,
    unknown,
    { asOfDate: string; runCalculations: boolean }
  >({
    mutationFn: ({ asOfDate, runCalculations }) =>
      apiCall(() =>
        ingestionApi.activateBankData({
          bankId: bankId!,
          dataActivationCreate: {
            asOfDate: new Date(`${asOfDate}T00:00:00Z`),
            reason: 'Activated uploaded data from the Data Engine console.',
            runCalculations,
          },
        }),
      ),
    onSuccess: () => {
      // The new reporting period must appear in the header selector, and every
      // module dashboard now has fresh runs for it.
      void queryClient.invalidateQueries({ queryKey: ['periods', bankId] });
      void queryClient.invalidateQueries({ queryKey: ['facts', bankId] });
      for (const prefix of [
        'liq-dashboard',
        'cap-dashboard',
        'irr-dashboard',
        'fx-dashboard',
        'ftp-dashboard',
        'reg-runs',
        'forecast-runs',
        'cap-rwa',
        'cap-structure',
        'bsd3',
        'bsd2',
      ]) {
        void queryClient.invalidateQueries({ queryKey: [prefix] });
      }
      void queryClient.invalidateQueries({ queryKey: ['de-activations', bankId] });
      void queryClient.invalidateQueries({ queryKey: ['de-summary', bankId] });
    },
  });
}

export function useDataActivations(bankId: string | undefined) {
  return useQuery({
    queryKey: ['de-activations', bankId],
    queryFn: () =>
      apiCall(() => ingestionApi.listBankDataActivations({ bankId: bankId! })),
    enabled: Boolean(bankId),
  });
}

export function useUploadAndIngest(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ file, asOfDate }: { file: File; asOfDate: string }) => {
      const staged = await apiCall(() =>
        ingestionApi.uploadIngestionSource({ bankId: bankId!, file }),
      );
      const ingestionBatchCreate: IngestionBatchCreate = {
        sourceSystem: 'EXCEL_CSV',
        asOfDate: new Date(`${asOfDate}T00:00:00Z`),
        location: staged.location,
        reason: `Uploaded ${staged.filename} via the Data Engine console.`,
      };
      const started = await apiCall(() =>
        ingestionApi.startIngestionBatch({ bankId: bankId!, ingestionBatchCreate }),
      );
      return { staged, started };
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['de-batches', bankId] });
      void queryClient.invalidateQueries({ queryKey: ['de-positions', bankId] });
      void queryClient.invalidateQueries({ queryKey: ['de-summary', bankId] });
    },
  });
}
