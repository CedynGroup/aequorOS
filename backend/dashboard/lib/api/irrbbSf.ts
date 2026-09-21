"use client";

/**
 * The IRRBB Standardised Framework API surface: the result for one reporting
 * date, the attempt history behind it, and the mint.
 *
 * All transport is the GENERATED client. `RegulatoryIrrApi` carries both
 * framework operations and `RegulatoryLiquidityApi` carries the run registry,
 * so there is no second code path re-implementing auth, the error envelope or
 * the snake_case mapping.
 *
 * `normalize` is a REQUIRED argument on every read, exactly as it is in
 * `icaapFiling.ts`, and for the same reason: it is the only thing standing
 * between a component and whatever the server actually sent, and an optional
 * argument would eventually be left off. The query's `data` therefore always
 * satisfies the declared view type.
 *
 * WHY TWO READS. `getIrrbbStandardisedFramework` serves the newest SUCCEEDED
 * run and answers 404 when there is none. A REFUSED attempt — a book holding
 * interest-rate options, refused under `irrbb_sf_options_unsupported` (D-061)
 * — is a `failed` run, so it never reaches that route. Reading the run
 * registry beside the result is what lets the screen distinguish "nobody has
 * run it" from "we ran it and it refused", which are different answers and
 * only one of them is actionable. It is the same authority (IRRBB, aggregated,
 * view), so it costs a query and no new permission.
 *
 * D-024 — no regulatory number is declared here.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiCall, regulatoryIrrApi, regulatoryLiquidityApi } from "./client";
import {
  HEAVY_DASHBOARD_QUERY_POLICY,
  invalidateScopedPrefixes,
  scopedQueryKey,
} from "./queryPolicy";
import { useQueryAuthorityScope } from "./useQueryScope";
import { ICAAP_PREFIXES } from "./icaap";
import { ICAAP_P2_PREFIXES } from "./icaapRiskCapital";
import {
  normalizeAttempts,
  normalizeStandardisedFramework,
} from "./irrbbSfNormalize";

export type {
  SfAssumption,
  SfAttempts,
  SfBucket,
  SfCurrencyScenario,
  SfCurrencyScope,
  SfDataQuality,
  SfExclusion,
  SfLadderRow,
  SfMandate,
  SfMeasure,
  SfMeasures,
  SfNmdCategory,
  SfParameter,
  SfRun,
  SfScenario,
  SfTable8Row,
  StandardisedFramework,
} from "./irrbbSfNormalize";

/**
 * How many attempts the history reads.
 *
 * A page size, not a regulatory quantity: it bounds the registry query that
 * answers "was the framework tried, and what happened". The newest entry is
 * the one the screen acts on.
 */
const ATTEMPT_PAGE_SIZE = 10;

/** The persisted run module for the framework. A wire key, not a label. */
const SF_MODULE = "irr_sf" as const;

export const IRRBB_SF_PREFIXES = [
  "irrbb-sf",
  "irrbb-sf-attempts",
] as const;

/**
 * Reads the Standardised Framework result for one reporting date.
 *
 * 404 is NOT an error here — it is "no result for this date yet", which the
 * screen renders as a first-class state. `QueryBoundary` still sees genuine
 * failures (403, 409, 500, a network drop).
 */
export function useIrrbbStandardisedFramework(
  bankId: string | undefined,
  reportingPeriodId: string | undefined,
  enabled = true,
) {
  const scope = useQueryAuthorityScope();
  return useQuery({
    queryKey: scopedQueryKey(
      "irrbb-sf",
      scope,
      bankId ?? null,
      reportingPeriodId ?? null,
    ),
    queryFn: async () =>
      normalizeStandardisedFramework(
        await apiCall(() =>
          regulatoryIrrApi.getIrrbbStandardisedFramework({
            bankId: bankId!,
            reportingPeriodId: reportingPeriodId!,
          }),
        ),
      ),
    enabled: enabled && Boolean(bankId && reportingPeriodId),
    retry: false,
    ...HEAVY_DASHBOARD_QUERY_POLICY,
  });
}

/**
 * Every framework attempt at this reporting date, newest first.
 *
 * THE TRANSPORT IS ONE LINE BEHIND THE CONTRACT. The backend now serves
 * `GET /banks/{bankId}/irr/standardised-framework/attempts`
 * (`listIrrbbStandardisedFrameworkAttempts`), which answers this question
 * directly and carries the server's own sentence for a refusal instead of
 * leaving the screen to compose one. `normalizeAttempts` already reads that
 * shape, with its own tests. This hook still calls the generic run registry
 * because the generated client does not carry the new operation until
 * `mise run risk-service:openapi-client` is next run; the swap is this call
 * and nothing else, and the normaliser needs no change when it happens.
 */
export function useIrrbbStandardisedFrameworkAttempts(
  bankId: string | undefined,
  reportingPeriodId: string | undefined,
  enabled = true,
) {
  const scope = useQueryAuthorityScope();
  return useQuery({
    queryKey: scopedQueryKey(
      "irrbb-sf-attempts",
      scope,
      bankId ?? null,
      reportingPeriodId ?? null,
    ),
    queryFn: async () =>
      normalizeAttempts(
        // Called as a METHOD, not passed as a reference: `regulatoryIrrApi` is
        // autobound but `regulatoryLiquidityApi` is not, and a bare reference
        // to a generated operation loses `this` and throws before any request
        // is made — the defect that made three ICAAP panels report a dead
        // backend against a healthy one.
        await apiCall(() =>
          regulatoryLiquidityApi.listRegulatoryRuns({
            bankId: bankId!,
            module: SF_MODULE,
            reportingPeriodId: reportingPeriodId!,
            limit: ATTEMPT_PAGE_SIZE,
          }),
        ),
      ),
    enabled: enabled && Boolean(bankId && reportingPeriodId),
    retry: false,
    ...HEAVY_DASHBOARD_QUERY_POLICY,
  });
}

/**
 * Mints one immutable framework run.
 *
 * A refusal is a 201 carrying a `failed` run with its error code, not an
 * exception — so the caller must read the returned run rather than treat a
 * resolved promise as a success.
 */
export function useRunIrrbbStandardisedFramework(bankId: string | undefined) {
  const queryClient = useQueryClient();
  const scope = useQueryAuthorityScope();
  return useMutation({
    mutationFn: (variables: { reportingPeriodId: string }) =>
      apiCall(() =>
        regulatoryIrrApi.runIrrbbStandardisedFramework({
          bankId: bankId!,
          irrbbSfRunCreate: {
            reportingPeriodId: variables.reportingPeriodId,
          },
        }),
      ),
    onSuccess: async () => {
      // A framework run is Pillar 2 evidence: the ICAAP block that binds it,
      // the register item computed from it and the readiness verdict that
      // depends on both are all stale the moment it lands. The prefix lists are
      // imported rather than restated so a renamed ICAAP key cannot leave this
      // invalidation quietly pointing at nothing.
      await invalidateScopedPrefixes(
        queryClient,
        [...IRRBB_SF_PREFIXES, ...ICAAP_PREFIXES, ...ICAAP_P2_PREFIXES],
        scope,
        bankId,
      );
      // The run registry is not authority-scoped; it is keyed by prefix.
      void queryClient.invalidateQueries({ queryKey: ["reg-runs"] });
    },
  });
}
