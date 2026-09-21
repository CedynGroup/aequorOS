"use client";

/**
 * ICAAP Workspace API surface (P1).
 *
 * Every read is an authority-scoped TanStack query (`scopedQueryKey`) under the
 * heavy-dashboard policy, and every call goes through `apiCall`, so a backend
 * error envelope arrives as a typed `ApiError` (carrying `details.error_code`,
 * which the conflict dialog reads) and a "no computed data yet" envelope as
 * `ModuleUnavailableError`.
 *
 * All transport is the GENERATED client. The pre-integration shim this module
 * used to carry is gone: `IcaapApi` and `FeatureFlagsApi` now exist, and there
 * is no second code path that has to re-implement auth, the error envelope or
 * the snake_case mapping.
 *
 * Streamed endpoints (attachment download, both draft exports) use the
 * generated `…Raw()` variants rather than their convenience wrappers. The
 * wrapper resolves to a `Blob` and discards the response, which would lose
 * `Content-Disposition` — and the filename is what tells a reader on disk that
 * the file is a DRAFT. `…Raw()` gives the `Response` and the `Blob` both.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import type {
  IcaapAttachmentRead,
  IcaapCycleArchive,
  IcaapCycleCreate,
  IcaapManualTablePut,
  IcaapRequirementStatus,
} from "@aequoros/risk-service-api";
import { useModuleScope } from "@/components/shell/BankContext";
import { hrefAccess } from "@/lib/modules";
import { apiCall, icaapApi } from "./client";
import {
  HEAVY_DASHBOARD_QUERY_POLICY,
  invalidateScopedPrefixes,
  scopedQueryKey,
  type QueryAuthorityScope,
} from "./queryPolicy";
import { useQueryAuthorityScope } from "./useQueryScope";

/**
 * The generated models, re-exported so every ICAAP component imports its types
 * from one place. They are the OpenAPI contract, not a parallel declaration:
 * a backend schema change lands here through regeneration.
 */
export type {
  IcaapAttachmentListRead,
  IcaapAttachmentRead,
  IcaapAttachmentRequirementStatusRead,
  IcaapBasis,
  IcaapBlockBindingRead,
  IcaapBlockRefreshRead,
  IcaapBlockStatus,
  IcaapBlockTypeRead,
  IcaapCitationRead,
  IcaapCycleArchive,
  IcaapCycleCreate,
  IcaapCycleKind,
  IcaapCycleListRead,
  IcaapCycleRead,
  IcaapCycleSummaryRead,
  IcaapDataBlockListRead,
  IcaapDataBlockRead,
  IcaapDeadlineStatusRead,
  IcaapDueDateBasis,
  IcaapFactChangeRead,
  IcaapFactValueRead,
  IcaapFrameworkSummaryRead,
  IcaapManualColumn,
  IcaapManualRow,
  IcaapManualTablePut,
  IcaapRag,
  IcaapReadinessItemRead,
  IcaapReadinessRead,
  IcaapRequirementRead,
  IcaapRequirementStatus,
  IcaapSectionRead,
  IcaapSectionSummaryRead,
  IcaapSeverity,
} from "@aequoros/risk-service-api";

/**
 * A ProseMirror document as the editor and the API exchange it.
 *
 * The contract types it as an opaque object (`{ [key: string]: any }`) because
 * the ALLOWLIST, not the OpenAPI schema, is what decides a document's shape —
 * `app/domain/icaap/editor_schema.json`, which the editor mirrors and
 * `components/icaap/editor/schema.parity.test.ts` pins.
 */
export type ProseMirrorDoc = { type: "doc"; content?: unknown[] };

// ---------------------------------------------------------------------------
// The one gate every ICAAP query passes through
// ---------------------------------------------------------------------------

/**
 * Is the ICAAP workspace reachable for the signed-in user on this bank?
 *
 * Every read below ANDs its `enabled` with this. Gating on `bankId` alone was
 * not enough and the SDI journey proved it: the route guard stays permissive
 * until the tenant's scope resolves (so a deep-link refresh waits rather than
 * flashing a 404), which is a window in which the page mounts and its queries
 * fire. An SDI tenant — which has no Pillar 2 regime and gets a 404 from the
 * API — was therefore still asking `/icaap/cycles` and `/icaap/frameworks`
 * before the guard caught up.
 *
 * `hrefAccess` is the same pure decision the sidebar and the route guard use,
 * so there is one answer to "may this user see ICAAP", not three.
 */
export function useIcaapWorkspaceEnabled(): boolean {
  return hrefAccess("/icaap", useModuleScope()).state === "enabled";
}

// ---------------------------------------------------------------------------
// Query keys and invalidation
// ---------------------------------------------------------------------------

export const ICAAP_PREFIXES = [
  "icaap-cycles",
  "icaap-cycle",
  "icaap-readiness",
  "icaap-sections",
  "icaap-section",
  "icaap-section-versions",
  "icaap-blocks",
  "icaap-block",
  "icaap-attachments",
  // P2's stress & capital-plan tab reads the SAME blocks route through its own
  // adapter, so it needs its own key — and it is listed here so that a refresh,
  // pin or retire on the Sections tab invalidates it too. A stress tab showing
  // evidence the preparer has just replaced would be worse than showing none.
  "icaap-stress-evidence",
] as const;

function invalidateCycle(
  queryClient: QueryClient,
  scope: QueryAuthorityScope,
  bankId: string | undefined,
): Promise<void[]> {
  return invalidateScopedPrefixes(queryClient, [...ICAAP_PREFIXES], scope, bankId);
}

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------

export function useIcaapFrameworks(bankId: string | undefined) {
  const workspaceEnabled = useIcaapWorkspaceEnabled();
  const scope = useQueryAuthorityScope();
  return useQuery({
    queryKey: scopedQueryKey("icaap-frameworks", scope, bankId ?? null),
    queryFn: () => apiCall(() => icaapApi.listIcaapFrameworks({ bankId: bankId! })),
    enabled: workspaceEnabled && Boolean(bankId),
    ...HEAVY_DASHBOARD_QUERY_POLICY,
  });
}

export function useIcaapBlockTypes(bankId: string | undefined) {
  const workspaceEnabled = useIcaapWorkspaceEnabled();
  const scope = useQueryAuthorityScope();
  return useQuery({
    queryKey: scopedQueryKey("icaap-block-types", scope, bankId ?? null),
    queryFn: () => apiCall(() => icaapApi.listIcaapBlockTypes({ bankId: bankId! })),
    enabled: workspaceEnabled && Boolean(bankId),
    staleTime: 5 * 60_000,
  });
}

export function useIcaapCycles(
  bankId: string | undefined,
  options: { fiscalYear?: number; includeArchived?: boolean } = {},
) {
  const workspaceEnabled = useIcaapWorkspaceEnabled();
  const scope = useQueryAuthorityScope();
  return useQuery({
    queryKey: scopedQueryKey(
      "icaap-cycles",
      scope,
      bankId ?? null,
      options.fiscalYear ?? null,
      options.includeArchived ?? false,
    ),
    queryFn: () =>
      apiCall(() =>
        icaapApi.listIcaapCycles({
          bankId: bankId!,
          fiscalYear: options.fiscalYear,
          includeArchived: options.includeArchived,
        }),
      ),
    enabled: workspaceEnabled && Boolean(bankId),
    ...HEAVY_DASHBOARD_QUERY_POLICY,
  });
}

export function useIcaapCycle(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  const workspaceEnabled = useIcaapWorkspaceEnabled();
  const scope = useQueryAuthorityScope();
  return useQuery({
    queryKey: scopedQueryKey("icaap-cycle", scope, bankId ?? null, cycleId ?? null),
    queryFn: () =>
      apiCall(() => icaapApi.getIcaapCycle({ bankId: bankId!, cycleId: cycleId! })),
    enabled: workspaceEnabled && Boolean(bankId && cycleId),
    ...HEAVY_DASHBOARD_QUERY_POLICY,
  });
}

export function useIcaapReadiness(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  const workspaceEnabled = useIcaapWorkspaceEnabled();
  const scope = useQueryAuthorityScope();
  return useQuery({
    queryKey: scopedQueryKey(
      "icaap-readiness",
      scope,
      bankId ?? null,
      cycleId ?? null,
    ),
    queryFn: () =>
      apiCall(() =>
        icaapApi.getIcaapReadiness({ bankId: bankId!, cycleId: cycleId! }),
      ),
    enabled: workspaceEnabled && Boolean(bankId && cycleId),
    ...HEAVY_DASHBOARD_QUERY_POLICY,
  });
}

export function useIcaapSections(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  const workspaceEnabled = useIcaapWorkspaceEnabled();
  const scope = useQueryAuthorityScope();
  return useQuery({
    queryKey: scopedQueryKey(
      "icaap-sections",
      scope,
      bankId ?? null,
      cycleId ?? null,
    ),
    queryFn: () =>
      apiCall(() =>
        icaapApi.listIcaapSections({ bankId: bankId!, cycleId: cycleId! }),
      ),
    enabled: workspaceEnabled && Boolean(bankId && cycleId),
    ...HEAVY_DASHBOARD_QUERY_POLICY,
  });
}

export function useIcaapSection(
  bankId: string | undefined,
  cycleId: string | undefined,
  sectionKey: string | undefined,
) {
  const workspaceEnabled = useIcaapWorkspaceEnabled();
  const scope = useQueryAuthorityScope();
  return useQuery({
    queryKey: scopedQueryKey(
      "icaap-section",
      scope,
      bankId ?? null,
      cycleId ?? null,
      sectionKey ?? null,
    ),
    queryFn: () =>
      apiCall(() =>
        icaapApi.getIcaapSection({
          bankId: bankId!,
          cycleId: cycleId!,
          sectionKey: sectionKey!,
        }),
      ),
    enabled: workspaceEnabled && Boolean(bankId && cycleId && sectionKey),
    ...HEAVY_DASHBOARD_QUERY_POLICY,
  });
}

export function useIcaapSectionVersions(
  bankId: string | undefined,
  cycleId: string | undefined,
  sectionKey: string | undefined,
) {
  const workspaceEnabled = useIcaapWorkspaceEnabled();
  const scope = useQueryAuthorityScope();
  return useQuery({
    queryKey: scopedQueryKey(
      "icaap-section-versions",
      scope,
      bankId ?? null,
      cycleId ?? null,
      sectionKey ?? null,
    ),
    queryFn: () =>
      apiCall(() =>
        icaapApi.listIcaapSectionVersions({
          bankId: bankId!,
          cycleId: cycleId!,
          sectionKey: sectionKey!,
        }),
      ),
    enabled: workspaceEnabled && Boolean(bankId && cycleId && sectionKey),
    ...HEAVY_DASHBOARD_QUERY_POLICY,
  });
}

export function useIcaapBlocks(
  bankId: string | undefined,
  cycleId: string | undefined,
  options: { includePayload?: boolean } = {},
) {
  const workspaceEnabled = useIcaapWorkspaceEnabled();
  const scope = useQueryAuthorityScope();
  return useQuery({
    queryKey: scopedQueryKey(
      "icaap-blocks",
      scope,
      bankId ?? null,
      cycleId ?? null,
      options.includePayload ?? false,
    ),
    queryFn: () =>
      apiCall(() =>
        icaapApi.listIcaapDataBlocks({
          bankId: bankId!,
          cycleId: cycleId!,
          includePayload: options.includePayload,
        }),
      ),
    enabled: workspaceEnabled && Boolean(bankId && cycleId),
    ...HEAVY_DASHBOARD_QUERY_POLICY,
  });
}

export function useIcaapBlock(
  bankId: string | undefined,
  cycleId: string | undefined,
  blockId: string | undefined,
) {
  const workspaceEnabled = useIcaapWorkspaceEnabled();
  const scope = useQueryAuthorityScope();
  return useQuery({
    queryKey: scopedQueryKey(
      "icaap-block",
      scope,
      bankId ?? null,
      cycleId ?? null,
      blockId ?? null,
    ),
    queryFn: () =>
      apiCall(() =>
        icaapApi.getIcaapDataBlock({
          bankId: bankId!,
          cycleId: cycleId!,
          blockId: blockId!,
        }),
      ),
    enabled: workspaceEnabled && Boolean(bankId && cycleId && blockId),
    ...HEAVY_DASHBOARD_QUERY_POLICY,
  });
}

export function useIcaapAttachments(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  const workspaceEnabled = useIcaapWorkspaceEnabled();
  const scope = useQueryAuthorityScope();
  return useQuery({
    queryKey: scopedQueryKey(
      "icaap-attachments",
      scope,
      bankId ?? null,
      cycleId ?? null,
    ),
    queryFn: () =>
      apiCall(() =>
        icaapApi.listIcaapAttachments({ bankId: bankId!, cycleId: cycleId! }),
      ),
    enabled: workspaceEnabled && Boolean(bankId && cycleId),
    ...HEAVY_DASHBOARD_QUERY_POLICY,
  });
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

export function useCreateIcaapCycle(bankId: string | undefined) {
  const scope = useQueryAuthorityScope();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: IcaapCycleCreate) =>
      apiCall(() =>
        icaapApi.createIcaapCycle({ bankId: bankId!, icaapCycleCreate: payload }),
      ),
    onSuccess: () => {
      void invalidateCycle(queryClient, scope, bankId);
    },
  });
}

export function useArchiveIcaapCycle(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  const scope = useQueryAuthorityScope();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: IcaapCycleArchive) =>
      apiCall(() =>
        icaapApi.archiveIcaapCycle({
          bankId: bankId!,
          cycleId: cycleId!,
          icaapCycleArchive: payload,
        }),
      ),
    onSuccess: () => {
      void invalidateCycle(queryClient, scope, bankId);
    },
  });
}

/**
 * Save the working document.
 *
 * `baseRev` is the revision the author was looking at. A 409
 * `section_rev_conflict` means someone else saved in between; the caller shows
 * the conflict dialog. RETRY IS OFF: replaying the save would overwrite the
 * other author's work, which is the exact outcome the optimistic write exists
 * to prevent.
 */
export function useSaveIcaapSection(
  bankId: string | undefined,
  cycleId: string | undefined,
  sectionKey: string | undefined,
) {
  const scope = useQueryAuthorityScope();
  const queryClient = useQueryClient();
  return useMutation({
    retry: false,
    mutationFn: (payload: { doc: ProseMirrorDoc; baseRev: number }) =>
      apiCall(() =>
        icaapApi.saveIcaapSectionWorking({
          bankId: bankId!,
          cycleId: cycleId!,
          sectionKey: sectionKey!,
          icaapSectionWorkingSave: {
            doc: payload.doc as { [key: string]: unknown },
            baseRev: payload.baseRev,
          },
        }),
      ),
    onSuccess: (section) => {
      queryClient.setQueryData(
        scopedQueryKey(
          "icaap-section",
          scope,
          bankId ?? null,
          cycleId ?? null,
          sectionKey ?? null,
        ),
        section,
      );
      void invalidateScopedPrefixes(
        queryClient,
        ["icaap-readiness", "icaap-sections", "icaap-cycle"],
        scope,
        bankId,
      );
    },
  });
}

export function useCommitIcaapSection(
  bankId: string | undefined,
  cycleId: string | undefined,
  sectionKey: string | undefined,
) {
  const scope = useQueryAuthorityScope();
  const queryClient = useQueryClient();
  return useMutation({
    retry: false,
    mutationFn: (payload: { baseRev: number; note?: string | null }) =>
      apiCall(() =>
        icaapApi.commitIcaapSectionVersion({
          bankId: bankId!,
          cycleId: cycleId!,
          sectionKey: sectionKey!,
          icaapSectionCommit: payload,
        }),
      ),
    onSuccess: () => {
      void invalidateCycle(queryClient, scope, bankId);
    },
  });
}

export function useSetIcaapRequirementState(
  bankId: string | undefined,
  cycleId: string | undefined,
  sectionKey: string | undefined,
) {
  const scope = useQueryAuthorityScope();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: {
      itemId: string;
      status: IcaapRequirementStatus;
      reason?: string | null;
    }) =>
      apiCall(() =>
        icaapApi.setIcaapRequirementState({
          bankId: bankId!,
          cycleId: cycleId!,
          sectionKey: sectionKey!,
          itemId: payload.itemId,
          icaapRequirementStateUpdate: {
            status: payload.status,
            reason: payload.reason,
          },
        }),
      ),
    onSuccess: () => {
      void invalidateCycle(queryClient, scope, bankId);
    },
  });
}

export function useCreateIcaapBlock(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  const scope = useQueryAuthorityScope();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: {
      blockType: string;
      title?: string | null;
      params?: { [key: string]: unknown };
    }) =>
      apiCall(() =>
        icaapApi.createIcaapDataBlock({
          bankId: bankId!,
          cycleId: cycleId!,
          icaapDataBlockCreate: payload,
        }),
      ),
    onSuccess: () => {
      void invalidateCycle(queryClient, scope, bankId);
    },
  });
}

export function useRefreshIcaapBlock(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  const scope = useQueryAuthorityScope();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { blockId: string; reason?: string | null }) =>
      apiCall(() =>
        icaapApi.refreshIcaapDataBlock({
          bankId: bankId!,
          cycleId: cycleId!,
          blockId: payload.blockId,
          icaapDataBlockRefresh: { reason: payload.reason },
        }),
      ),
    onSuccess: () => {
      void invalidateCycle(queryClient, scope, bankId);
    },
  });
}

export function usePinIcaapBlock(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  const scope = useQueryAuthorityScope();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { blockId: string; reason: string }) =>
      apiCall(() =>
        icaapApi.pinIcaapDataBlock({
          bankId: bankId!,
          cycleId: cycleId!,
          blockId: payload.blockId,
          icaapDataBlockPin: { reason: payload.reason },
        }),
      ),
    onSuccess: () => {
      void invalidateCycle(queryClient, scope, bankId);
    },
  });
}

export function useUnpinIcaapBlock(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  const scope = useQueryAuthorityScope();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { blockId: string }) =>
      apiCall(() =>
        icaapApi.unpinIcaapDataBlock({
          bankId: bankId!,
          cycleId: cycleId!,
          blockId: payload.blockId,
        }),
      ),
    onSuccess: () => {
      void invalidateCycle(queryClient, scope, bankId);
    },
  });
}

export function usePutIcaapManualTable(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  const scope = useQueryAuthorityScope();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { blockId: string; table: IcaapManualTablePut }) =>
      apiCall(() =>
        icaapApi.putIcaapManualTable({
          bankId: bankId!,
          cycleId: cycleId!,
          blockId: payload.blockId,
          icaapManualTablePut: payload.table,
        }),
      ),
    onSuccess: () => {
      void invalidateCycle(queryClient, scope, bankId);
    },
  });
}

export function useRetireIcaapBlock(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  const scope = useQueryAuthorityScope();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { blockId: string; reason: string }) =>
      apiCall(() =>
        icaapApi.retireIcaapDataBlock({
          bankId: bankId!,
          cycleId: cycleId!,
          blockId: payload.blockId,
          icaapDataBlockRetire: { reason: payload.reason },
        }),
      ),
    onSuccess: () => {
      void invalidateCycle(queryClient, scope, bankId);
    },
  });
}

export function useWithdrawIcaapAttachment(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  const scope = useQueryAuthorityScope();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { attachmentId: string; reason: string }) =>
      apiCall(() =>
        icaapApi.withdrawIcaapAttachment({
          bankId: bankId!,
          cycleId: cycleId!,
          attachmentId: payload.attachmentId,
          icaapAttachmentWithdraw: { reason: payload.reason },
        }),
      ),
    onSuccess: () => {
      void invalidateCycle(queryClient, scope, bankId);
    },
  });
}

/**
 * Upload an attachment (multipart).
 *
 * The server is authoritative on media type and size: it sniffs the bytes, so
 * a PDF renamed `.docx` is refused however the browser labelled it.
 *
 * There is no percentage here. `fetch` — which the generated client uses —
 * cannot report upload progress, and a second XHR transport purely for a
 * progress bar would re-implement auth and error handling outside the contract.
 * The dialog shows an indeterminate "uploading" state instead.
 */
export function useUploadIcaapAttachment(
  bankId: string | undefined,
  cycleId: string | undefined,
) {
  const scope = useQueryAuthorityScope();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: {
      kind: string;
      title: string;
      file: File;
      sectionKey?: string | null;
    }) =>
      apiCall(() =>
        icaapApi.uploadIcaapAttachment({
          bankId: bankId!,
          cycleId: cycleId!,
          kind: payload.kind,
          title: payload.title,
          sectionKey: payload.sectionKey,
          file: payload.file,
        }),
      ),
    onSuccess: () => {
      void invalidateCycle(queryClient, scope, bankId);
    },
  });
}

// ---------------------------------------------------------------------------
// Streamed downloads
// ---------------------------------------------------------------------------

/**
 * The server's filename, or the caller's fallback.
 *
 * Exported so P2's supervisory-addon letter download uses the SAME rule: the
 * supervisor's own filename is part of the evidence, and two download paths
 * that disagreed about it would produce two names for one letter.
 */
export function filenameFrom(response: Response, fallback: string): string {
  const disposition = response.headers.get("Content-Disposition") ?? "";
  return /filename\*?=(?:UTF-8'')?"?([^";]+)"?/.exec(disposition)?.[1] ?? fallback;
}

export function saveBlob(blob: Blob, filename: string): void {
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(href);
}

export async function downloadIcaapAttachment(
  bankId: string,
  cycleId: string,
  attachment: Pick<IcaapAttachmentRead, "id" | "originalFilename">,
): Promise<void> {
  const response = await apiCall(() =>
    icaapApi.downloadIcaapAttachmentRaw({
      bankId,
      cycleId,
      attachmentId: attachment.id,
    }),
  );
  saveBlob(
    await response.value(),
    filenameFrom(response.raw, attachment.originalFilename),
  );
}

/**
 * The draft export (P1-DESIGN §3.8 routes 32–33). Both artifacts are WORKING
 * COPIES — never a filing — which the document states on every page and the
 * server's own filename repeats.
 */
export async function downloadIcaapDraft(
  bankId: string,
  cycleId: string,
  kind: "pdf" | "docx",
  content: "working" | "committed",
): Promise<void> {
  const request = { bankId, cycleId, content } as const;
  const response = await apiCall(() =>
    kind === "pdf"
      ? icaapApi.exportIcaapDraftPdfRaw(request)
      : icaapApi.exportIcaapDraftDocxRaw(request),
  );
  saveBlob(
    await response.value(),
    filenameFrom(response.raw, `icaap-${cycleId}-DRAFT.${kind}`),
  );
}
