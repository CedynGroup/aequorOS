"use client";

/**
 * The ICAAP filing API surface (P3): the review chain, the freeze, the filing
 * view, the documents filed with the return, the bank's review-chain template
 * and the ¶82 disclosure.
 *
 * All transport is the GENERATED client — `IcaapApi` carries all seventeen P3
 * operations, and the package plane's attachment routes live on
 * `RegulatoryReportingApi`. There is no second code path re-implementing auth,
 * the error envelope or the snake_case mapping.
 *
 * `normalize` is a REQUIRED argument on every read, exactly as it is in
 * `icaapRiskCapital.ts`, and for the same reason: it is the only thing
 * standing between a component and whatever the server actually sent, and an
 * optional argument would eventually be left off. The query's `data`
 * therefore always satisfies the declared view type.
 *
 * D-024 — no regulatory number is declared here.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { apiCall, icaapApi, regulatoryReportingApi } from "./client";
import {
  HEAVY_DASHBOARD_QUERY_POLICY,
  invalidateScopedPrefixes,
  scopedQueryKey,
  type QueryAuthorityScope,
} from "./queryPolicy";
import { useQueryAuthorityScope } from "./useQueryScope";
import { ICAAP_PREFIXES, useIcaapWorkspaceEnabled } from "./icaap";
import { ICAAP_P2_PREFIXES } from "./icaapRiskCapital";
import {
  normalizeArtifactVersions,
  normalizeArtifacts,
  normalizeDisclosure,
  normalizeFiling,
  normalizePackageAttachments,
  normalizePreflight,
  normalizeStages,
  normalizeWorkflowTemplates,
} from "./icaapFilingNormalize";

/**
 * The view models the P3 components import. Reads are the adapter's types
 * (never the generated response models — the wire declares most fields
 * optional and a screen cannot render an "optional list"); writes are the
 * generated request bodies, unmediated.
 */
export type {
  IcaapChainSource,
  IcaapDisclosure,
  IcaapDisclosureSection,
  IcaapDisclosureStatus,
  IcaapDisclosureWithheld,
  IcaapFiling,
  IcaapFilingAttachment,
  IcaapFilingSlot,
  IcaapPackageSummary,
  IcaapPreflight,
  IcaapPreflightItem,
  IcaapScope,
  IcaapSeverity,
  IcaapStage,
  IcaapStageDecision,
  IcaapStageInput,
  IcaapStageKind,
  IcaapStageState,
  IcaapStages,
  IcaapTemplateStatus,
  IcaapWorkflowTemplate,
  IcaapWorkflowTemplates,
  PackageArtifact,
  PackageArtifactVersion,
  PackageAttachment,
  PackageAttachmentRequirement,
  PackageAttachments,
} from "./icaapFilingNormalize";
export {
  ICAAP_SEVERITIES,
  ICAAP_STAGE_KINDS,
  ICAAP_STAGE_STATES,
} from "./icaapFilingNormalize";

// ---------------------------------------------------------------------------
// Query keys and invalidation
// ---------------------------------------------------------------------------

export const ICAAP_P3_PREFIXES = [
  "icaap-stages",
  "icaap-preflight",
  "icaap-filing",
  "icaap-package-attachments",
  "icaap-package-artifacts",
  "icaap-package-artifact-versions",
  "icaap-workflow-templates",
  "icaap-disclosure",
] as const;

/**
 * A P3 mutation moves the whole cycle.
 *
 * A stage decision changes readiness and the filing view; a freeze mints a
 * package and locks every section; a signature changes what may be submitted.
 * So the invalidation is the union of the three phases' prefixes rather than a
 * guess about which screen cares — a stale "ready to file" is the one thing
 * this workspace must never show.
 */
function invalidateP3(
  queryClient: QueryClient,
  scope: QueryAuthorityScope,
  bankId: string | undefined,
): Promise<void[]> {
  return invalidateScopedPrefixes(
    queryClient,
    [...ICAAP_P3_PREFIXES, ...ICAAP_PREFIXES, ...ICAAP_P2_PREFIXES],
    scope,
    bankId,
  );
}

/**
 * The reporting workspace's own keys, which are NOT authority-scoped.
 *
 * A freeze mints a regulatory package and a submission moves it, so the
 * Returns, Calendar and History screens are stale the moment either happens.
 * They are invalidated by prefix because that is the shape `lib/api/hooks.ts`
 * uses for them.
 */
const REPORTING_PREFIXES = [
  "rr-packages",
  "rr-package",
  "rr-artifacts",
  "rr-artifact-versions",
  "rr-attestation",
  "rr-obligations",
  "rr-submission-events",
] as const;

function invalidateReporting(queryClient: QueryClient): void {
  for (const prefix of REPORTING_PREFIXES) {
    void queryClient.invalidateQueries({ queryKey: [prefix] });
  }
}

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------

/** One scoped, cycle-keyed P3 read. `normalize` is required — see the header. */
function useP3Query<T>(
  prefix: string,
  bankId: string | undefined,
  cycleId: string | undefined,
  fetcher: (p: { bankId: string; cycleId: string }) => Promise<unknown>,
  normalize: (value: unknown) => T,
) {
  const workspaceEnabled = useIcaapWorkspaceEnabled();
  const scope = useQueryAuthorityScope();
  return useQuery({
    queryKey: scopedQueryKey(prefix, scope, bankId ?? null, cycleId ?? null),
    queryFn: async () =>
      normalize(
        await apiCall(() => fetcher({ bankId: bankId!, cycleId: cycleId! })),
      ),
    enabled: workspaceEnabled && Boolean(bankId && cycleId),
    ...HEAVY_DASHBOARD_QUERY_POLICY,
  });
}

/** A read scoped to one PACKAGE rather than to the cycle. */
function useP3PackageQuery<T>(
  prefix: string,
  bankId: string | undefined,
  packageId: string | null | undefined,
  fetcher: (p: { bankId: string; packageId: string }) => Promise<unknown>,
  normalize: (value: unknown) => T,
) {
  const workspaceEnabled = useIcaapWorkspaceEnabled();
  const scope = useQueryAuthorityScope();
  return useQuery({
    queryKey: scopedQueryKey(prefix, scope, bankId ?? null, packageId ?? null),
    queryFn: async () =>
      normalize(
        await apiCall(() =>
          fetcher({ bankId: bankId!, packageId: packageId! }),
        ),
      ),
    enabled: workspaceEnabled && Boolean(bankId && packageId),
    ...HEAVY_DASHBOARD_QUERY_POLICY,
  });
}

export function useIcaapStages(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP3Query(
    "icaap-stages",
    bankId,
    cycleId,
    icaapApi.getIcaapCycleStages,
    normalizeStages,
  );
}

export function useIcaapFreezePreflight(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP3Query(
    "icaap-preflight",
    bankId,
    cycleId,
    icaapApi.getIcaapFreezePreflight,
    normalizePreflight,
  );
}

export function useIcaapFiling(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP3Query(
    "icaap-filing",
    bankId,
    cycleId,
    icaapApi.getIcaapFiling,
    normalizeFiling,
  );
}

export function useIcaapDisclosure(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP3Query(
    "icaap-disclosure",
    bankId,
    cycleId,
    icaapApi.getIcaapDisclosure,
    normalizeDisclosure,
  );
}

export function useIcaapWorkflowTemplates(bankId: string | undefined) {
  const workspaceEnabled = useIcaapWorkspaceEnabled();
  const scope = useQueryAuthorityScope();
  return useQuery({
    queryKey: scopedQueryKey("icaap-workflow-templates", scope, bankId ?? null),
    queryFn: async () =>
      normalizeWorkflowTemplates(
        await apiCall(() =>
          icaapApi.listIcaapWorkflowTemplates({ bankId: bankId! }),
        ),
      ),
    enabled: workspaceEnabled && Boolean(bankId),
    ...HEAVY_DASHBOARD_QUERY_POLICY,
  });
}

export function usePackageAttachments(
  bankId: string | undefined,
  packageId: string | null | undefined,
) {
  return useP3PackageQuery(
    "icaap-package-attachments",
    bankId,
    packageId,
    regulatoryReportingApi.listPackageAttachments,
    normalizePackageAttachments,
  );
}

export function useIcaapPackageArtifacts(
  bankId: string | undefined,
  packageId: string | null | undefined,
) {
  return useP3PackageQuery(
    "icaap-package-artifacts",
    bankId,
    packageId,
    regulatoryReportingApi.listPackageArtifacts,
    normalizeArtifacts,
  );
}

export function useIcaapPackageArtifactVersions(
  bankId: string | undefined,
  packageId: string | null | undefined,
) {
  return useP3PackageQuery(
    "icaap-package-artifact-versions",
    bankId,
    packageId,
    regulatoryReportingApi.listPackageArtifactVersions,
    normalizeArtifactVersions,
  );
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

function useP3Mutation<TVars, TResult>(
  bankId: string | undefined,
  run: (vars: TVars) => Promise<TResult>,
) {
  const queryClient = useQueryClient();
  const scope = useQueryAuthorityScope();
  return useMutation({
    mutationFn: (vars: TVars) => apiCall(() => run(vars)),
    onSuccess: async () => {
      await invalidateP3(queryClient, scope, bankId);
      invalidateReporting(queryClient);
    },
  });
}

export function useSubmitIcaapForReview(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP3Mutation(
    bankId,
    (vars: { reviewDigest: string; note?: string | null }) =>
      icaapApi.submitIcaapCycleForReview({
        bankId: bankId!,
        cycleId: cycleId!,
        icaapSubmitForReview: {
          reviewDigest: vars.reviewDigest,
          note: vars.note ?? null,
        },
      }),
  );
}

export function useDecideIcaapStage(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP3Mutation(
    bankId,
    (vars: {
      seq: number;
      decision: "reviewed" | "approved" | "returned";
      round: number;
      reviewDigest: string;
      returnToSeq?: number | null;
      comment?: string | null;
    }) =>
      icaapApi.decideIcaapStage({
        bankId: bankId!,
        cycleId: cycleId!,
        seq: vars.seq,
        icaapStageDecisionCreate: {
          decision: vars.decision,
          round: vars.round,
          reviewDigest: vars.reviewDigest,
          returnToSeq: vars.returnToSeq ?? null,
          comment: vars.comment ?? null,
        },
      }),
  );
}

export function useReturnIcaapCycle(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP3Mutation(
    bankId,
    // `round` and `reviewDigest` became required on 2026-09-20: a send-back
    // unseals a signed report, so it now carries the same round and digest its
    // sibling stage decisions do, and the server refuses one aimed at a round
    // that has already moved. Sent from the stages read, never recomputed here.
    (vars: {
      returnToSeq: number;
      reason: string;
      round: number;
      reviewDigest: string;
    }) =>
      icaapApi.returnIcaapCycle({
        bankId: bankId!,
        cycleId: cycleId!,
        icaapReturnCreate: {
          returnToSeq: vars.returnToSeq,
          reason: vars.reason,
          round: vars.round,
          reviewDigest: vars.reviewDigest,
        },
      }),
  );
}

export function useFreezeIcaapCycle(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP3Mutation(
    bankId,
    (vars: { reviewDigest: string; reason: string }) =>
      icaapApi.freezeIcaapCycle({
        bankId: bankId!,
        cycleId: cycleId!,
        icaapFreezeCreate: {
          reviewDigest: vars.reviewDigest,
          reason: vars.reason,
        },
      }),
  );
}

export function useCloneIcaapCycle(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP3Mutation(
    bankId,
    (vars: {
      mode: "revision" | "update";
      reason: string;
      cycleKind?: "annual" | "material_change" | "regulator_request" | null;
      // ISO `YYYY-MM-DD`. The contract declares these as strings, not `Date`:
      // a reporting date is a calendar day in the institution's jurisdiction,
      // and a `Date` would carry a timezone the regulator never asked for.
      asOfDate?: string | null;
      changeTrigger?: string | null;
      changeDescription?: string | null;
      regulatorRequestRef?: string | null;
      requestedDueDate?: string | null;
    }) =>
      icaapApi.cloneIcaapCycle({
        bankId: bankId!,
        cycleId: cycleId!,
        icaapCloneCreate: {
          mode: vars.mode,
          reason: vars.reason,
          cycleKind: vars.cycleKind ?? null,
          asOfDate: vars.asOfDate ?? null,
          changeTrigger: vars.changeTrigger ?? null,
          changeDescription: vars.changeDescription ?? null,
          regulatorRequestRef: vars.regulatorRequestRef ?? null,
          requestedDueDate: vars.requestedDueDate ?? null,
        },
      }),
  );
}

// --- the filing itself -----------------------------------------------------

export function useValidateIcaapPackage(bankId: string | undefined) {
  return useP3Mutation(bankId, (vars: { packageId: string }) =>
    regulatoryReportingApi.validateRegulatoryPackage({
      bankId: bankId!,
      packageId: vars.packageId,
    }),
  );
}

/**
 * Record the filing.
 *
 * The ICAAP return's only permitted channel is `manual`: the report is taken
 * to the regulator outside the platform and the platform records which sealed
 * revision and which documents went with it. The server enforces that — this
 * only avoids offering a channel it would refuse.
 */
export function useSubmitIcaapPackage(bankId: string | undefined) {
  return useP3Mutation(
    bankId,
    (vars: { packageId: string; externalRef?: string | null }) =>
      regulatoryReportingApi.submitRegulatoryPackage({
        bankId: bankId!,
        packageId: vars.packageId,
        packageSubmitCreate: {
          channel: "manual",
          externalRef: vars.externalRef ?? null,
        },
      }),
  );
}

export function useUploadPackageAttachment(bankId: string | undefined) {
  return useP3Mutation(
    bankId,
    (vars: {
      packageId: string;
      kind: string;
      title: string;
      file: File;
      attributes?: Record<string, string>;
    }) =>
      regulatoryReportingApi.uploadPackageAttachment({
        bankId: bankId!,
        packageId: vars.packageId,
        kind: vars.kind,
        title: vars.title,
        file: vars.file,
        // `multipart/form-data` carries no types, so a structured field is
        // sent as JSON TEXT — the shape `_parse_attributes` expects. Sending
        // it any other way reaches FastAPI as an upload (D-050).
        attributes:
          vars.attributes && Object.keys(vars.attributes).length > 0
            ? JSON.stringify(vars.attributes)
            : undefined,
      }),
  );
}

export function useWithdrawPackageAttachment(bankId: string | undefined) {
  return useP3Mutation(
    bankId,
    (vars: { packageId: string; attachmentId: string; reason: string }) =>
      regulatoryReportingApi.withdrawPackageAttachment({
        bankId: bankId!,
        packageId: vars.packageId,
        attachmentId: vars.attachmentId,
        packageAttachmentWithdraw: { reason: vars.reason },
      }),
  );
}

// --- the ¶82 disclosure ----------------------------------------------------

export function usePutIcaapDisclosure(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP3Mutation(
    bankId,
    (vars: { selectedSectionKeys: string[]; reason: string }) =>
      icaapApi.putIcaapDisclosure({
        bankId: bankId!,
        cycleId: cycleId!,
        icaapDisclosurePut: {
          selectedSectionKeys: vars.selectedSectionKeys,
          reason: vars.reason,
        },
      }),
  );
}

export function useSubmitIcaapDisclosure(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP3Mutation(bankId, (vars: { reason: string }) =>
    icaapApi.submitIcaapDisclosure({
      bankId: bankId!,
      cycleId: cycleId!,
      icaapDisclosureSubmit: { reason: vars.reason },
    }),
  );
}

export function useDecideIcaapDisclosure(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  return useP3Mutation(
    bankId,
    (vars: { decision: "approved" | "rejected"; reason: string }) =>
      icaapApi.decideIcaapDisclosure({
        bankId: bankId!,
        cycleId: cycleId!,
        icaapDisclosureDecision: {
          decision: vars.decision,
          reason: vars.reason,
        },
      }),
  );
}

// --- the bank's review chain (settings) ------------------------------------

export function useProposeIcaapWorkflowTemplate(bankId: string | undefined) {
  return useP3Mutation(
    bankId,
    (vars: {
      stages: {
        seq: number;
        stageKey: string;
        title: string;
        decisionKind: "prepare" | "review" | "approve" | "attest";
        officerTitles: string[];
        freezeOnApprove: boolean;
      }[];
      reason: string;
    }) =>
      icaapApi.proposeIcaapWorkflowTemplate({
        bankId: bankId!,
        icaapWorkflowTemplateCreate: {
          stages: vars.stages,
          reason: vars.reason,
        },
      }),
  );
}

/**
 * Rewrites the stages of a DRAFT chain.
 *
 * Both this and `useProposeIcaapWorkflowTemplate` run
 * `app/domain/icaap/workflow.validate_stages` server-side BEFORE anything is
 * written, and answer 422 with that function's own code and sentence. That is
 * why the composer mirrors none of the chain rules: the one authority on what
 * a runnable chain is answers every save, and the screen prints what it said.
 */
export function useUpdateIcaapWorkflowTemplate(bankId: string | undefined) {
  return useP3Mutation(
    bankId,
    (vars: {
      templateId: string;
      stages: {
        seq: number;
        stageKey: string;
        title: string;
        decisionKind: "prepare" | "review" | "approve" | "attest";
        officerTitles: string[];
        freezeOnApprove: boolean;
      }[];
      reason: string;
    }) =>
      icaapApi.updateIcaapWorkflowTemplate({
        bankId: bankId!,
        templateId: vars.templateId,
        icaapWorkflowTemplateUpdate: {
          stages: vars.stages,
          reason: vars.reason,
        },
      }),
  );
}

export function useSubmitIcaapWorkflowTemplate(bankId: string | undefined) {
  return useP3Mutation(
    bankId,
    (vars: { templateId: string; reason: string }) =>
      icaapApi.submitIcaapWorkflowTemplate({
        bankId: bankId!,
        templateId: vars.templateId,
        icaapWorkflowTemplateSubmit: { reason: vars.reason },
      }),
  );
}

export function useDecideIcaapWorkflowTemplate(bankId: string | undefined) {
  return useP3Mutation(
    bankId,
    (vars: {
      templateId: string;
      decision: "approved" | "rejected";
      reason: string;
    }) =>
      icaapApi.decideIcaapWorkflowTemplate({
        bankId: bankId!,
        templateId: vars.templateId,
        icaapWorkflowTemplateDecision: {
          decision: vars.decision,
          reason: vars.reason,
        },
      }),
  );
}
