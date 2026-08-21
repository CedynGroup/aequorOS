'use client';

/**
 * SDI simplified-capital hooks (docs/sdi.md §4.2). Fetch the /sdi/capital-checks
 * and /sdi/loan-classification tenant endpoints so the reframed `/basel` screen
 * can show the s.29 view (CAR + paid-up + statutory reserve + provisioning)
 * instead of the Basel 3-tier build for a savings-&-loans tenant.
 */

import { useQuery } from '@tanstack/react-query';
import { getSession } from 'next-auth/react';
import {
  apiCall,
  ApiError,
  apiBaseUrl,
  liquidityMonitoringApi,
} from '@/lib/api/client';
import { getAccessToken, setAccessToken } from '@/lib/api/token';

async function bearer(): Promise<string> {
  const cached = getAccessToken();
  if (cached) return cached;
  const session = await getSession();
  const token = session?.accessToken ?? '';
  if (token) setAccessToken(token);
  return token;
}

async function sdiFetch<T>(path: string): Promise<T> {
  const token = await bearer();
  const response = await fetch(`${apiBaseUrl}${path}`, {
    headers: {
      'content-type': 'application/json',
      ...(token ? { authorization: `Bearer ${token}` } : {}),
    },
  });
  if (!response.ok) {
    let message = `Request failed (${response.status}).`;
    try {
      const body = await response.json();
      const envelope = body?.error ?? body;
      if (envelope && typeof envelope.message === 'string') message = envelope.message;
    } catch {
      // non-JSON body
    }
    throw new ApiError({ message, status: response.status, code: null, errorCode: null, details: null });
  }
  return (await response.json()) as T;
}

export type CapitalCheck = {
  check: string;
  compliant: boolean | null;
  actual_ghs: string | null;
  required_ghs: string | null;
  detail: string;
  source_citation: string;
  confirmation_status: 'confirmed' | 'pending';
};

export type SdiCapitalChecks = { as_of: string; checks: CapitalCheck[] };

export type LoanGradeBucket = {
  grade: string;
  count: number;
  exposure_ghs: string;
  provision_required_ghs: string;
  non_performing: boolean;
};

export type DelinquencyBucket = {
  code: string;
  label: string;
  count: number;
  exposure_ghs: string;
};

export type PortfolioAtRisk = {
  code: string;
  label: string;
  exposure_ghs: string;
  ratio: string;
};

export type SdiLoanClassification = {
  as_of: string;
  institution_class: string;
  loan_count: number;
  total_exposure_ghs: string;
  npl_exposure_ghs: string;
  npl_ratio: string;
  total_provision_required_ghs: string;
  stage_proxy_count: number;
  dpd_covered_count: number;
  dpd_covered_exposure_ghs: string;
  unclassified_count: number;
  buckets: LoanGradeBucket[];
  delinquency_buckets: DelinquencyBucket[];
  portfolio_at_risk: PortfolioAtRisk[];
  pending_parameters: string[];
};

export type RiskWeightBand = {
  bucket: string;
  weight_pct: string;
  exposure_ghs: string;
  rwa_ghs: string;
  confirmation_status: 'confirmed' | 'pending';
};

export type SdiCapitalSummary = {
  as_of: string;
  net_own_funds_ghs: string;
  total_rwa_ghs: string;
  car_pct: string | null;
  car_min_pct: string;
  status: 'green' | 'red' | 'na';
  car_min_confirmation: 'confirmed' | 'pending';
  computable: boolean;
  bands: RiskWeightBand[];
  pending_parameters: string[];
};

export type SdiCapitalHistoryPoint = {
  as_of: string;
  net_own_funds_ghs: string;
  total_rwa_ghs: string;
  car_pct: string | null;
  capital_headroom_ghs: string | null;
  npl_exposure_ghs: string;
  npl_ratio: string;
  required_provision_ghs: string;
  actual_provision_ghs: string | null;
  provision_coverage_pct: string | null;
  assessment_status: 'provisional' | 'review_required' | 'not_computable';
};

export type SdiCapitalAssurance = {
  as_of: string;
  current: SdiCapitalHistoryPoint;
  history: SdiCapitalHistoryPoint[];
  mapped_gl_capital_ghs: string | null;
  capital_to_gl_difference_ghs: string | null;
  gl_reconciliation_status: 'mapped' | 'not_mapped' | 'mapping_incomplete';
  reserve_change_ghs: string | null;
  filing_status: 'blocked';
  filing_blockers: string[];
};

export type SdiLiquidityRatio = {
  code: string;
  label: string;
  value_pct: string | null;
  threshold_pct: string;
  status: 'ok' | 'below_minimum' | 'not_computable';
  threshold_source: string;
};

export type SdiLiquidityReserve = {
  code: string;
  label: string;
  value_pct: string | null;
  threshold_pct: string;
  status: 'ok' | 'below_minimum' | 'not_computable';
  source_citation: string;
  confirmation_status: 'confirmed' | 'pending';
};

export type SdiMaturityBucket = {
  code: string;
  label: string;
  net_mismatch_ghs: string;
  cumulative_mismatch_ghs: string | null;
};

export type SdiFundingProvider = {
  name: string;
  deposit_ghs: string;
  pct_total_deposits: string | null;
  related: boolean;
};

export type SdiFundingConcentration = {
  total_deposits_ghs: string;
  top_five_deposits_ghs: string;
  top_five_pct: string | null;
  unattributed_deposits_ghs: string;
  providers: SdiFundingProvider[];
};

export type SdiCounterbalancingCapacity = {
  gross_unencumbered_ghs: string;
  monetized_value_ghs: string;
  bog_eligible_ghs: string;
  uncalibrated_asset_count: number;
};

export type LiquidityMonitoring = {
  as_of: string;
  institution_class: string;
  maturity_ladder: SdiMaturityBucket[];
  funding_concentration: SdiFundingConcentration;
  counterbalancing_capacity: SdiCounterbalancingCapacity;
  readiness: SdiReadiness[];
};

export type SdiReadiness = {
  module: string;
  status: 'ready' | 'partial' | 'blocked';
  reasons: string[];
};

export type SdiLiquidityPosition = {
  as_of: string;
  ratios: SdiLiquidityRatio[];
  reserves: SdiLiquidityReserve[];
  maturity_ladder: SdiMaturityBucket[];
  funding_concentration: SdiFundingConcentration;
  counterbalancing_capacity: SdiCounterbalancingCapacity;
  readiness: SdiReadiness[];
};

export type SdiExposure = {
  counterparty_name: string;
  connection: string;
  exposure_ghs: string;
  pct_net_own_funds: string | null;
  single_obligor_limit_pct: string;
  large_exposure_limit_pct: string;
  status: 'ok' | 'above_limit' | 'not_computable';
  exempt: boolean;
};

export type SdiLargeExposures = {
  as_of: string;
  net_own_funds_ghs: string;
  exposures: SdiExposure[];
  findings: string[];
};

export function useSdiCapitalChecks(bankId: string | undefined) {
  return useQuery({
    queryKey: ['sdi-capital-checks', bankId],
    queryFn: () => sdiFetch<SdiCapitalChecks>(`/banks/${bankId}/sdi/capital-checks`),
    enabled: Boolean(bankId),
  });
}

export function useSdiCapitalSummary(bankId: string | undefined) {
  return useQuery({
    queryKey: ['sdi-capital-summary', bankId],
    queryFn: () => sdiFetch<SdiCapitalSummary>(`/banks/${bankId}/sdi/capital-summary`),
    enabled: Boolean(bankId),
  });
}

export function useSdiCapitalAssurance(bankId: string | undefined) {
  return useQuery({
    queryKey: ['sdi-capital-assurance', bankId],
    queryFn: () => sdiFetch<SdiCapitalAssurance>(`/banks/${bankId}/sdi/capital-assurance`),
    enabled: Boolean(bankId),
  });
}

export function useLoanClassification(bankId: string | undefined) {
  return useQuery({
    queryKey: ['loan-classification', bankId],
    queryFn: () => sdiFetch<SdiLoanClassification>(`/banks/${bankId}/loan-classification`),
    enabled: Boolean(bankId),
  });
}

/** @deprecated Use the institution-neutral loan-classification endpoint. */
export const useSdiLoanClassification = useLoanClassification;

export function useSdiLiquidityPosition(bankId: string | undefined) {
  return useQuery({
    queryKey: ['sdi-liquidity-position', bankId],
    queryFn: () => sdiFetch<SdiLiquidityPosition>(`/banks/${bankId}/sdi/liquidity-position`),
    enabled: Boolean(bankId),
  });
}

export function useLiquidityMonitoring(bankId: string | undefined) {
  return useQuery({
    queryKey: ['liquidity-monitoring', bankId],
    queryFn: async (): Promise<LiquidityMonitoring> => {
      const response = await apiCall(() =>
        liquidityMonitoringApi.getLiquidityMonitoring({ bankId: bankId! })
      );
      return {
        as_of: response.asOf,
        institution_class: response.institutionClass,
        maturity_ladder: response.maturityLadder.map((row) => ({
          code: row.code,
          label: row.label,
          net_mismatch_ghs: row.netMismatchGhs,
          cumulative_mismatch_ghs: row.cumulativeMismatchGhs,
        })),
        funding_concentration: {
          total_deposits_ghs: response.fundingConcentration.totalDepositsGhs,
          top_five_deposits_ghs: response.fundingConcentration.topFiveDepositsGhs,
          top_five_pct: response.fundingConcentration.topFivePct,
          unattributed_deposits_ghs: response.fundingConcentration.unattributedDepositsGhs,
          providers: response.fundingConcentration.providers.map((row) => ({
            name: row.name,
            deposit_ghs: row.depositGhs,
            pct_total_deposits: row.pctTotalDeposits,
            related: row.related,
          })),
        },
        counterbalancing_capacity: {
          gross_unencumbered_ghs: response.counterbalancingCapacity.grossUnencumberedGhs,
          monetized_value_ghs: response.counterbalancingCapacity.monetizedValueGhs,
          bog_eligible_ghs: response.counterbalancingCapacity.bogEligibleGhs,
          uncalibrated_asset_count: response.counterbalancingCapacity.uncalibratedAssetCount,
        },
        readiness: response.readiness.map((row) => ({
          module: row.module,
          status: row.status as SdiReadiness['status'],
          reasons: row.reasons,
        })),
      };
    },
    enabled: Boolean(bankId),
  });
}

export function useSdiLargeExposures(bankId: string | undefined) {
  return useQuery({
    queryKey: ['sdi-large-exposures', bankId],
    queryFn: () => sdiFetch<SdiLargeExposures>(`/banks/${bankId}/sdi/large-exposures`),
    enabled: Boolean(bankId),
  });
}

export function useSdiReadiness(bankId: string | undefined) {
  return useQuery({
    queryKey: ['sdi-readiness', bankId],
    queryFn: () => sdiFetch<{ as_of: string; modules: SdiReadiness[] }>(`/banks/${bankId}/sdi/readiness`),
    enabled: Boolean(bankId),
  });
}
