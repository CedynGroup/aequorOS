"use client";

/**
 * ICAAP risk & capital API surface (P2): risk register, materiality, appetite,
 * Pillar 2, reconciliation, audit review and the challenge log.
 *
 * All transport is the GENERATED client. The hand-written contract and shim
 * this module carried while P2-API was still being built are gone: `IcaapApi`
 * now holds all 73 ICAAP operations, so there is no second code path
 * re-implementing auth, the error envelope or the snake_case mapping.
 *
 * TWO THINGS SURVIVED THE SWAP, deliberately:
 *
 * 1. **The normalisers** (`icaapRiskCapitalNormalize.ts`). The generated models
 *    declare most fields OPTIONAL — `parameters?`, `violations?`,
 *    `staleReasons?`, every amount as `string | null` — which is honest about
 *    the wire and useless to a component that has to render something. Every
 *    read below passes through a normaliser, so a panel can iterate a list
 *    without asking whether it exists. This is the fix for the crash the
 *    founder hit (`register.parameters.length` on an undefined field), and it
 *    matters MORE against the generated types, not less.
 * 2. **The fail-closed defaults** those normalisers apply: an absent list is
 *    empty, an absent figure stays absent (never 0), an unknown RAG is "none"
 *    and never green, and an action flag the server did not send is false.
 *
 * D-024 — no regulatory number is declared here. Every threshold, band, floor,
 * tolerance and deadline the P2 screens show arrives on a payload with its
 * provenance (`IcaapParameterUseRead`).
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { apiCall, icaapApi } from "./client";
import {
  HEAVY_DASHBOARD_QUERY_POLICY,
  invalidateScopedPrefixes,
  scopedQueryKey,
  type QueryAuthorityScope,
} from "./queryPolicy";
import { useQueryAuthorityScope } from "./useQueryScope";
import { filenameFrom, saveBlob, useIcaapWorkspaceEnabled } from "./icaap";
import {
  normalizeAllocation,
  normalizeAppetite,
  normalizeAuditReviews,
  normalizeCapitalTriggers,
  normalizeChallenges,
  normalizeParameterRegister,
  normalizePillar2Register,
  normalizePillar2Revisions,
  normalizeReconciliation,
  normalizeRiskRegister,
  normalizeStressEvidence,
  normalizeSupervisoryAddons,
  normalizeTable5,
} from "./icaapRiskCapitalNormalize";

/**
 * The generated models, re-exported so every P2 component imports its types
 * from one place. They are the OpenAPI contract, not a parallel declaration: a
 * backend schema change lands here through regeneration.
 *
 * NOTE for readers coming from the section checklist: `IcaapRequirementRead` is
 * the CHECKLIST requirement (`app/schemas/icaap.py`, used by
 * `components/icaap/RequirementChecklist.tsx`). The Pillar 2 capital
 * requirement statement is `IcaapCapitalRequirementRead` — the two were
 * colliding in the generator and the P2 one was renamed.
 */
/**
 * What a P2 component imports.
 *
 * READS are the adapter's view models (`icaapRiskCapitalNormalize`), not the
 * generated response models: the wire declares `parameters?`, `violations?` and
 * every amount as `string | null`, and a screen cannot render an "optional
 * list". The adapter is the only place that reconciles the two, and it is
 * tested on its own.
 *
 * WRITES are the generated request bodies, unmediated — the server's shape is
 * the only correct shape for a mutation, and a hand-written copy would drift.
 *
 * NOTE for readers coming from the section checklist: `IcaapRequirementRead` is
 * the CHECKLIST requirement (`app/schemas/icaap.py`, used by
 * `components/icaap/RequirementChecklist.tsx`). The Pillar 2 capital
 * requirement statement is `IcaapCapitalRequirementRead` — the two collided in
 * the generator and the P2 one was renamed.
 */
export type {
  IcaapAddonStatus,
  IcaapAllocation,
  IcaapAllocationCell,
  IcaapAllocationDriverKind,
  IcaapAllocationLine,
  IcaapAllocationUnit,
  IcaapAllocationUnitKind,
  IcaapAppetite,
  IcaapAppetiteMetric,
  IcaapAppetiteMetricDef,
  IcaapAuditReview,
  IcaapAuditReviews,
  IcaapCapitalTriggers,
  IcaapChallenge,
  IcaapChallenges,
  IcaapConsistency,
  IcaapMaterialityMatrix,
  IcaapParameterRegister,
  IcaapParameterUse,
  IcaapPillar2Item,
  IcaapPillar2Register,
  IcaapPillar2Revision,
  IcaapPillar2Revisions,
  IcaapReconciliation,
  IcaapRequirementLine,
  IcaapResourcesLine,
  IcaapRevisionKind,
  IcaapRisk,
  IcaapRiskRegister,
  IcaapStressEvidence,
  IcaapStressEvidenceBlock,
  IcaapSupervisoryAddon,
  IcaapSupervisoryAddons,
  IcaapTable5,
  IcaapTriggerPoint,
  IcaapTriggerResult,
  IcaapTriggerStatus,
  AppetiteDirection,
} from "./icaapRiskCapitalNormalize";
export {
  ICAAP_CHALLENGE_FORUMS,
  ICAAP_STRESS_BLOCK_TYPES,
} from "./icaapRiskCapitalNormalize";
/**
 * What the preparer typed, in the wire's own vocabulary.
 *
 * Dates are ISO `YYYY-MM-DD` strings and `basisValue` is a DECIMAL STRING kept
 * byte for byte as entered — the server re-parses it with `Decimal`, and a
 * round trip through a JavaScript number is exactly how a supervisory add-on of
 * 1.005% becomes 1.0049999999999999.
 */
export type IcaapSupervisoryAddonDraft = {
  letterReference: string;
  letterDate: string;
  effectiveFrom: string;
  appliesToBasis: string;
  basis: string;
  basisValue: string;
  letter: File;
  componentKey?: string | null;
  description?: string | null;
  table5Row?: string | null;
  supersedesAddonId?: string | null;
};

import type {
  IcaapAllocationPut,
  IcaapAppetiteMetricCreate,
  IcaapAppetiteMetricUpdate,
  IcaapAuditReviewCreate,
  IcaapCapitalPlanProposalCreate,
  IcaapChallengeCreate,
  IcaapChallengeResponseCreate,
  IcaapCustomRiskCreate,
  IcaapExplanation,
  IcaapPillar2Approve,
  IcaapPillar2Compute,
  IcaapPillar2ItemCreate,
  IcaapReason,
  IcaapResourcesLineCreate,
  IcaapRiskPut,
} from "@aequoros/risk-service-api";

export type {
  IcaapAllocationDriverPut,
  IcaapAllocationPut,
  IcaapAllocationUnitPut,
  IcaapAppetiteMetricCreate,
  IcaapAppetiteMetricUpdate,
  IcaapAuditReviewCreate,
  IcaapCapitalPlanProposalCreate,
  IcaapCapitalPlanProposalRead,
  IcaapChallengeCreate,
  IcaapChallengeForum,
  IcaapChallengeResponseCreate,
  IcaapCustomRiskCreate,
  IcaapExplanation,
  IcaapMeasureKind,
  IcaapP2InputMode,
  IcaapP2Source,
  IcaapP2Status,
  IcaapPillar2Approve,
  IcaapPillar2Compute,
  IcaapPillar2ItemCreate,
  IcaapReason,
  IcaapResourcesLineCreate,
  IcaapReviewKind,
  IcaapReviewOpinion,
  IcaapRiskPut,
  IcaapSeverityLevel,
} from "@aequoros/risk-service-api";

// ---------------------------------------------------------------------------
// Query keys and invalidation
// ---------------------------------------------------------------------------

export const ICAAP_P2_PREFIXES = [
  "icaap-risks",
  "icaap-appetite",
  "icaap-capital-triggers",
  "icaap-pillar2",
  "icaap-pillar2-revisions",
  "icaap-table5",
  "icaap-reconciliation",
  "icaap-allocation",
  "icaap-audit-reviews",
  "icaap-challenges",
  "icaap-p2-parameters",
  "icaap-supervisory-addons",
] as const;

/**
 * Every P2 mutation invalidates the whole P2 prefix set plus P1's readiness and
 * blocks: a risk verdict moves readiness, a Pillar 2 approval moves Table 5 and
 * the section blocks that render it.
 */
function invalidateP2(
  queryClient: QueryClient,
  scope: QueryAuthorityScope,
  bankId: string | undefined,
): Promise<void[]> {
  return invalidateScopedPrefixes(
    queryClient,
    [
      ...ICAAP_P2_PREFIXES,
      "icaap-readiness",
      "icaap-blocks",
      "icaap-block",
      "icaap-stress-evidence",
    ],
    scope,
    bankId,
  );
}

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------

/**
 * One scoped P2 read.
 *
 * `normalize` is REQUIRED, not optional: it is the only thing standing between
 * a component and whatever the server actually sent, and an optional argument
 * would eventually be left off. The query's `data` therefore always satisfies
 * the declared type — the components do not have to trust the wire.
 */
function useP2Query<T>(
  prefix: string,
  bankId: string | undefined,
  cycleId: string | undefined,
  fetcher: (p: { bankId: string; cycleId: string }) => Promise<unknown>,
  normalize: (value: unknown) => T,
) {
  return useP2KeyedQuery(prefix, bankId, cycleId, null, fetcher, normalize);
}

/**
 * The same read, keyed by one further dimension — a Pillar 2 item's id for its
 * revision history, for instance.
 *
 * `key` joins the query key AND gates `enabled`, so a panel that has not chosen
 * an item yet fetches nothing rather than fetching the wrong thing.
 */
function useP2KeyedQuery<T>(
  prefix: string,
  bankId: string | undefined,
  cycleId: string | undefined,
  key: string | null,
  fetcher: (p: {
    bankId: string;
    cycleId: string;
    key: string;
  }) => Promise<unknown>,
  normalize: (value: unknown) => T,
  options: { keyed?: boolean } = {},
) {
  const keyed = options.keyed ?? key !== null;
  const workspaceEnabled = useIcaapWorkspaceEnabled();
  const scope = useQueryAuthorityScope();
  return useQuery({
    queryKey: scopedQueryKey(
      prefix,
      scope,
      bankId ?? null,
      cycleId ?? null,
      key,
    ),
    queryFn: async () =>
      normalize(
        await apiCall(() =>
          fetcher({ bankId: bankId!, cycleId: cycleId!, key: key ?? "" }),
        ),
      ),
    enabled:
      workspaceEnabled && Boolean(bankId && cycleId) && (!keyed || Boolean(key)),
    ...HEAVY_DASHBOARD_QUERY_POLICY,
  });
}

/**
 * A read scoped to the INSTITUTION rather than to one ICAAP cycle.
 *
 * Supervisory add-ons are the bank's, not a cycle's: a letter from the
 * supervisor stays in force across cycles until it is withdrawn or superseded.
 * The normaliser is required here for the same reason it is above.
 */
function useP2BankQuery<T>(
  prefix: string,
  bankId: string | undefined,
  dimensions: readonly unknown[],
  fetcher: (p: { bankId: string }) => Promise<unknown>,
  normalize: (value: unknown) => T,
) {
  const workspaceEnabled = useIcaapWorkspaceEnabled();
  const scope = useQueryAuthorityScope();
  return useQuery({
    queryKey: scopedQueryKey(prefix, scope, bankId ?? null, ...dimensions),
    queryFn: async () =>
      normalize(await apiCall(() => fetcher({ bankId: bankId! }))),
    enabled: workspaceEnabled && Boolean(bankId),
    ...HEAVY_DASHBOARD_QUERY_POLICY,
  });
}

export function useIcaapRisks(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP2Query("icaap-risks", bankId, cycleId, icaapApi.listIcaapRisks, normalizeRiskRegister);
}

export function useIcaapAppetite(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP2Query("icaap-appetite", bankId, cycleId, icaapApi.getIcaapAppetite, normalizeAppetite);
}

export function useIcaapPillar2Register(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP2Query(
    "icaap-pillar2",
    bankId,
    cycleId,
    icaapApi.getIcaapPillar2Register,
  normalizePillar2Register,
  );
}

export function useIcaapTable5(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP2Query("icaap-table5", bankId, cycleId, icaapApi.getIcaapTable5, normalizeTable5);
}

export function useIcaapReconciliation(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP2Query(
    "icaap-reconciliation",
    bankId,
    cycleId,
    icaapApi.getIcaapReconciliation,
  normalizeReconciliation,
  );
}

export function useIcaapAuditReviews(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP2Query(
    "icaap-audit-reviews",
    bankId,
    cycleId,
    icaapApi.listIcaapAuditReviews,
  normalizeAuditReviews,
  );
}

export function useIcaapChallenges(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP2Query(
    "icaap-challenges",
    bankId,
    cycleId,
    icaapApi.listIcaapChallenges,
  normalizeChallenges,
  );
}

export function useIcaapCapitalTriggers(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP2Query(
    "icaap-capital-triggers",
    bankId,
    cycleId,
    icaapApi.getIcaapCapitalTriggers,
    normalizeCapitalTriggers,
  );
}

export function useIcaapCapitalAllocation(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP2Query(
    "icaap-allocation",
    bankId,
    cycleId,
    icaapApi.getIcaapCapitalAllocation,
    normalizeAllocation,
  );
}

/** Every governed value this cycle's Pillar 2 work rests on, with provenance. */
export function useIcaapParameterRegister(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP2Query(
    "icaap-p2-parameters",
    bankId,
    cycleId,
    icaapApi.listIcaapParameters,
    normalizeParameterRegister,
  );
}

/** The revision history of ONE Pillar 2 item; idle until an item is chosen. */
export function useIcaapPillar2ItemRevisions(
  bankId: string | undefined,
  cycleId: string | undefined,
  itemId: string | null,
) {
  return useP2KeyedQuery(
    "icaap-pillar2-revisions",
    bankId,
    cycleId,
    itemId,
    ({ bankId: bank, cycleId: cycle, key }) =>
      icaapApi.listIcaapPillar2ItemRevisions({
        bankId: bank,
        cycleId: cycle,
        itemId: key,
      }),
    normalizePillar2Revisions,
  );
}

/**
 * Supervisory add-ons for the institution.
 *
 * `asOf` decides which letters are in force and what each converts to; it is
 * the cycle's own date on the ICAAP tab, so the figures agree with the rest of
 * the assessment rather than with today.
 */
export function useIcaapSupervisoryAddons(
  bankId: string | undefined,
  options: { asOf?: Date | string | null; includeInactive?: boolean } = {},
) {
  const asOf = asDate(options.asOf);
  const includeInactive = options.includeInactive ?? false;
  return useP2BankQuery(
    "icaap-supervisory-addons",
    bankId,
    // The key carries the date as text: two `Date` objects for the same day are
    // different objects, and the cache would miss every render.
    [asOf === null ? null : asOf.toISOString(), includeInactive],
    ({ bankId: bank }) =>
      icaapApi.listIcaapSupervisoryAddons({
        bankId: bank,
        asOf: asOf ?? undefined,
        includeInactive,
      }),
    normalizeSupervisoryAddons,
  );
}

/** A date the caller may express either way, or null. Never an invalid Date. */
function asDate(value: Date | string | null | undefined): Date | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = value instanceof Date ? value : new Date(value);
  return Number.isFinite(parsed.getTime()) ? parsed : null;
}

/**
 * The stress and capital-plan evidence this cycle is BOUND to.
 *
 * It reads P1's blocks route with payloads, through this module's own adapter,
 * because the stress tab must show what the ICAAP rests on — not the newest
 * run in the platform.
 */
export function useIcaapStressEvidence(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP2Query(
    "icaap-stress-evidence",
    bankId,
    cycleId,
    ({ bankId: bank, cycleId: cycle }) =>
      icaapApi.listIcaapDataBlocks({
        bankId: bank,
        cycleId: cycle,
        includePayload: true,
      }),
    normalizeStressEvidence,
  );
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

function useP2Mutation<TVars, TResult>(
  bankId: string | undefined,
  mutationFn: (vars: TVars) => Promise<TResult>,
) {
  const scope = useQueryAuthorityScope();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: TVars) => apiCall(() => mutationFn(vars)),
    onSuccess: () => invalidateP2(queryClient, scope, bankId),
  });
}

export function usePutIcaapRisk(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP2Mutation(
    bankId,
    (vars: { riskKey: string; payload: IcaapRiskPut }) =>
      icaapApi.putIcaapRisk({
        bankId: bankId!,
        cycleId: cycleId!,
        riskKey: vars.riskKey,
        icaapRiskPut: vars.payload,
      }),
  );
}

export function useCreateIcaapCustomRisk(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP2Mutation(bankId, (payload: IcaapCustomRiskCreate) =>
    icaapApi.createIcaapCustomRisk({
      bankId: bankId!,
      cycleId: cycleId!,
      icaapCustomRiskCreate: payload,
    }),
  );
}

export function useCreateIcaapAppetiteMetric(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP2Mutation(bankId, (payload: IcaapAppetiteMetricCreate) =>
    icaapApi.createIcaapAppetiteMetric({
      bankId: bankId!,
      cycleId: cycleId!,
      icaapAppetiteMetricCreate: payload,
    }),
  );
}

export function useUpdateIcaapAppetiteMetric(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP2Mutation(
    bankId,
    (vars: { metricId: string; payload: IcaapAppetiteMetricUpdate }) =>
      icaapApi.updateIcaapAppetiteMetric({
        bankId: bankId!,
        cycleId: cycleId!,
        metricId: vars.metricId,
        icaapAppetiteMetricUpdate: vars.payload,
      }),
  );
}

export function useCreateIcaapPillar2Item(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP2Mutation(bankId, (payload: IcaapPillar2ItemCreate) =>
    icaapApi.createIcaapPillar2Item({
      bankId: bankId!,
      cycleId: cycleId!,
      icaapPillar2ItemCreate: payload,
    }),
  );
}

export function useComputeIcaapPillar2Item(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP2Mutation(
    bankId,
    (vars: { itemId: string; payload: IcaapPillar2Compute }) =>
      icaapApi.computeIcaapPillar2Item({
        bankId: bankId!,
        cycleId: cycleId!,
        itemId: vars.itemId,
        icaapPillar2Compute: vars.payload,
      }),
  );
}

export function useApproveIcaapPillar2Item(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP2Mutation(
    bankId,
    (vars: { itemId: string; payload: IcaapPillar2Approve }) =>
      icaapApi.approveIcaapPillar2Item({
        bankId: bankId!,
        cycleId: cycleId!,
        itemId: vars.itemId,
        icaapPillar2Approve: vars.payload,
      }),
  );
}

export function useProposeIcaapCapitalPlanUpdate(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP2Mutation(bankId, (payload: IcaapCapitalPlanProposalCreate) =>
    icaapApi.proposeIcaapCapitalPlanUpdate({
      bankId: bankId!,
      cycleId: cycleId!,
      icaapCapitalPlanProposalCreate: payload,
    }),
  );
}

export function useComputeIcaapRequirementReconciliation(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP2Mutation(bankId, (payload: IcaapReason) =>
    icaapApi.computeIcaapRequirementReconciliation({
      bankId: bankId!,
      cycleId: cycleId!,
      icaapReason: payload,
    }),
  );
}

export function useExplainIcaapRequirementLine(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP2Mutation(
    bankId,
    (vars: { lineKey: string; payload: IcaapExplanation }) =>
      icaapApi.explainIcaapRequirementLine({
        bankId: bankId!,
        cycleId: cycleId!,
        lineKey: vars.lineKey,
        icaapExplanation: vars.payload,
      }),
  );
}

export function useLoadIcaapRegulatoryCapitalComponents(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP2Mutation(bankId, (payload: IcaapReason) =>
    icaapApi.loadIcaapRegulatoryCapitalComponents({
      bankId: bankId!,
      cycleId: cycleId!,
      icaapReason: payload,
    }),
  );
}

export function useCreateIcaapResourcesLine(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP2Mutation(bankId, (payload: IcaapResourcesLineCreate) =>
    icaapApi.createIcaapResourcesLine({
      bankId: bankId!,
      cycleId: cycleId!,
      icaapResourcesLineCreate: payload,
    }),
  );
}

export function useExplainIcaapControlDifference(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP2Mutation(
    bankId,
    (vars: {
      controlCode: string;
      comparisonKey: string;
      payload: IcaapExplanation;
    }) =>
      icaapApi.explainIcaapControlDifference({
        bankId: bankId!,
        cycleId: cycleId!,
        controlCode: vars.controlCode,
        comparisonKey: vars.comparisonKey,
        icaapExplanation: vars.payload,
      }),
  );
}

export function useCreateIcaapAuditReview(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP2Mutation(bankId, (payload: IcaapAuditReviewCreate) =>
    icaapApi.createIcaapAuditReview({
      bankId: bankId!,
      cycleId: cycleId!,
      icaapAuditReviewCreate: payload,
    }),
  );
}

export function useFinaliseIcaapAuditReview(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP2Mutation(bankId, (vars: { reviewId: string; reason: string }) =>
    icaapApi.finaliseIcaapAuditReview({
      bankId: bankId!,
      cycleId: cycleId!,
      reviewId: vars.reviewId,
      icaapReason: { reason: vars.reason },
    }),
  );
}

export function useRaiseIcaapChallenge(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP2Mutation(bankId, (payload: IcaapChallengeCreate) =>
    icaapApi.raiseIcaapChallenge({
      bankId: bankId!,
      cycleId: cycleId!,
      icaapChallengeCreate: payload,
    }),
  );
}

export function useRespondIcaapChallenge(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP2Mutation(
    bankId,
    (vars: { challengeId: string; payload: IcaapChallengeResponseCreate }) =>
      icaapApi.respondIcaapChallenge({
        bankId: bankId!,
        cycleId: cycleId!,
        challengeId: vars.challengeId,
        icaapChallengeResponseCreate: vars.payload,
      }),
  );
}

export function usePutIcaapCapitalAllocation(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP2Mutation(bankId, (payload: IcaapAllocationPut) =>
    icaapApi.putIcaapCapitalAllocation({
      bankId: bankId!,
      cycleId: cycleId!,
      icaapAllocationPut: payload,
    }),
  );
}

export function useConfirmIcaapSupervisoryAddon(bankId: string | undefined) {
  return useP2Mutation(bankId, (vars: { addonId: string; reason: string }) =>
    icaapApi.confirmIcaapSupervisoryAddon({
      bankId: bankId!,
      addonId: vars.addonId,
      icaapReason: { reason: vars.reason },
    }),
  );
}

export function useWithdrawIcaapSupervisoryAddon(bankId: string | undefined) {
  return useP2Mutation(bankId, (vars: { addonId: string; reason: string }) =>
    icaapApi.withdrawIcaapSupervisoryAddon({
      bankId: bankId!,
      addonId: vars.addonId,
      icaapReason: { reason: vars.reason },
    }),
  );
}

// ---------------------------------------------------------------------------
// Recording a supervisor's letter (multipart)
// ---------------------------------------------------------------------------

export function useCreateIcaapSupervisoryAddon(bankId: string | undefined) {
  return useP2Mutation(bankId, (draft: IcaapSupervisoryAddonDraft) =>
    icaapApi.createIcaapSupervisoryAddon({
      bankId: bankId!,
      appliesToBasis: draft.appliesToBasis,
      basis: draft.basis,
      // A decimal crosses a form body as text. The generated client sends it
      // that way (D-050), so the draft's own string goes straight through.
      basisValue: draft.basisValue,
      effectiveFrom: new Date(draft.effectiveFrom),
      letter: draft.letter,
      letterDate: new Date(draft.letterDate),
      letterReference: draft.letterReference,
      componentKey: draft.componentKey,
      description: draft.description,
      supersedesAddonId: draft.supersedesAddonId,
      table5Row: draft.table5Row,
    }),
  );
}

/**
 * The supervisor's letter itself.
 *
 * Downloading one is an audited event on the server (`…letter_downloaded`),
 * which is why this is a deliberate action rather than an inline preview.
 */
export async function downloadIcaapSupervisoryAddonLetter(
  bankId: string,
  addon: { addonId: string; letterOriginalFilename: string },
): Promise<void> {
  const response = await apiCall(() =>
    icaapApi.downloadIcaapSupervisoryAddonLetterRaw({
      bankId,
      addonId: addon.addonId,
    }),
  );
  saveBlob(
    await response.value(),
    filenameFrom(response.raw, addon.letterOriginalFilename),
  );
}
