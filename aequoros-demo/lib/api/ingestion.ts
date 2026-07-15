'use client';

/**
 * Data Engine API layer: generated IngestionApi client, TanStack Query hooks,
 * and starter mapping templates authored against the Sample Bank demo files
 * (data/02_Customer_Positions.xlsx, 03_gl_accounts.csv, 04_products.csv).
 *
 * Kept separate from lib/api/client.ts and hooks.ts so Data Engine work does
 * not contend with the regulatory modules' files.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Configuration,
  IngestionApi,
  type IngestionBatchCreate,
  type MappingConfig,
} from '@aequoros/risk-service-api';
import { apiCall, tenant } from './client';

const baseUrl =
  process.env.NEXT_PUBLIC_RISK_API_BASE_URL ?? 'http://127.0.0.1:8003/api/v1';

export const ingestionApi = new IngestionApi(
  new Configuration({ basePath: baseUrl.replace(/\/api\/v1\/?$/, '') }),
);

const t = { xOrgId: tenant.orgId, xUserId: tenant.userId } as const;

// ---------------------------------------------------------------------------
// Starter mapping templates for the Sample Bank demo dataset.
//
// Column names below are the REAL headers of the demo files; source_table is
// the sheet name (xlsx) or the file stem (csv) as the adapter's analyzer
// names recovered tables. One mapping can serve several files: entities whose
// table is absent from an uploaded file are simply skipped with a warning.
// ---------------------------------------------------------------------------

const glAccountMapping = {
  sourceTable: '03_gl_accounts',
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
  fields: {
    source_reference: 'product_code',
    product_code: 'product_code',
    name: 'product_name',
    regulatory_category: 'regulatory_category',
    risk_weight_code: 'risk_weight',
  },
};

const counterpartyMapping = {
  sourceTable: 'Counterparties_Sample',
  fields: {
    source_reference: 'counterparty_id',
    name: 'counterparty_name',
    counterparty_type: 'counterparty_type',
    country_code: 'country',
    rating: 'credit_rating',
  },
};

const loanPositionFields = {
  source_reference: 'position_id',
  position_type: 'position_type',
  currency: 'currency',
  balance: 'balance_ccy',
  notional: 'notional_ccy',
  counterparty_reference: 'counterparty_id',
  product_code: 'product_code',
  gl_account_code: 'gl_code',
  origination_date: 'origination_date',
  contractual_maturity: 'contractual_maturity',
  next_repricing_date: 'next_repricing_date',
  interest_rate: 'interest_rate',
  rate_type: 'rate_type',
  rate_index: 'rate_index',
  rate_spread: 'rate_spread',
  ifrs9_stage: 'ifrs9_stage',
};

// Deposits carry rate_type "NONE" (non-maturity products); the canonical
// model treats that as no contractual rate basis, so the column is unmapped.
const depositPositionFields = {
  source_reference: 'position_id',
  position_type: 'position_type',
  currency: 'currency',
  balance: 'balance_ccy',
  notional: 'notional_ccy',
  counterparty_reference: 'counterparty_id',
  product_code: 'product_code',
  gl_account_code: 'gl_code',
  origination_date: 'origination_date',
  contractual_maturity: 'contractual_maturity',
  interest_rate: 'interest_rate',
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
    key: 'loans',
    name: 'Sample Bank — loans & reference data',
    description:
      'Maps GL accounts and products from the reference CSVs, plus counterparties and the Loans sheet of the Customer Positions workbook.',
    ingests: [
      '03_gl_accounts.csv',
      '04_products.csv',
      '02_Customer_Positions.xlsx (Counterparties + Loans)',
    ],
    config: {
      fieldMappings: {
        gl_account: glAccountMapping,
        product: productMapping,
        counterparty: counterpartyMapping,
        position: { sourceTable: 'Loans', fields: loanPositionFields },
      },
      enumMappings: {},
      productMappings: {},
      options: {},
    },
  },
  {
    key: 'deposits',
    name: 'Sample Bank — deposits',
    description:
      'Maps counterparties and the Deposits sheet of the Customer Positions workbook. Activate after ingesting loans, then re-upload the same workbook.',
    ingests: ['02_Customer_Positions.xlsx (Counterparties + Deposits)'],
    config: {
      fieldMappings: {
        counterparty: counterpartyMapping,
        position: { sourceTable: 'Deposits', fields: depositPositionFields },
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
      apiCall(() => ingestionApi.listMappingConfigs({ ...t, bankId: bankId! })),
    enabled: Boolean(bankId),
  });
}

export function useIngestionBatches(bankId: string | undefined) {
  return useQuery({
    queryKey: ['de-batches', bankId],
    queryFn: () =>
      apiCall(() => ingestionApi.listIngestionBatches({ ...t, bankId: bankId! })),
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
        ingestionApi.getIngestionBatch({ ...t, bankId: bankId!, batchId: batchId! }),
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
          ...t,
          bankId: bankId!,
          batchId: batchId!,
        }),
      ),
    enabled: Boolean(bankId && batchId),
  });
}

export function useCanonicalPositions(bankId: string | undefined) {
  return useQuery({
    queryKey: ['de-positions', bankId],
    queryFn: () =>
      apiCall(() => ingestionApi.listCanonicalPositions({ ...t, bankId: bankId! })),
    enabled: Boolean(bankId),
  });
}

export function useLineageWalk(lineageId: string | undefined) {
  return useQuery({
    queryKey: ['de-lineage', lineageId],
    queryFn: () =>
      apiCall(() => ingestionApi.walkLineage({ ...t, lineageId: lineageId! })),
    enabled: Boolean(lineageId),
  });
}

export function useActivateTemplate(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (template: StarterTemplate) =>
      apiCall(() =>
        ingestionApi.createMappingConfig({
          ...t,
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

export function useUploadAndIngest(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ file, asOfDate }: { file: File; asOfDate: string }) => {
      const staged = await apiCall(() =>
        ingestionApi.uploadIngestionSource({ ...t, bankId: bankId!, file }),
      );
      const ingestionBatchCreate: IngestionBatchCreate = {
        sourceSystem: 'EXCEL_CSV',
        asOfDate: new Date(`${asOfDate}T00:00:00Z`),
        location: staged.location,
        reason: `Uploaded ${staged.filename} via the Data Engine console.`,
      };
      const started = await apiCall(() =>
        ingestionApi.startIngestionBatch({ ...t, bankId: bankId!, ingestionBatchCreate }),
      );
      return { staged, started };
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['de-batches', bankId] });
      void queryClient.invalidateQueries({ queryKey: ['de-positions', bankId] });
    },
  });
}
