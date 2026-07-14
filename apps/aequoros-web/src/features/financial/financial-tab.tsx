import type { FinancialDataWorkspaceRead } from "@aequoros/risk-service-api";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2 } from "lucide-react";
import { Fragment, useRef, useState } from "react";

import { Alert, Button, Input, Label, Skeleton } from "../../components/ui";
import type { TenantHeaders } from "../../lib/api";
import { ErrorPanel } from "../../shared/route-ui";
import { mockCaseHealth } from "../demo-data/demo-data";
import {
  financialErrorMessage,
  financialReviewClient,
} from "./financial-client";
import { FinancialSections } from "./financial-sections";

type WorkspaceUpdater = (
  workspace: FinancialDataWorkspaceRead,
) => FinancialDataWorkspaceRead;

export function FinancialTab({
  tenant,
  caseId,
  mockWorkspace,
}: {
  tenant: TenantHeaders;
  caseId: string;
  mockWorkspace: boolean;
}) {
  const queryClient = useQueryClient();
  const queryKey = ["financial-workspace", tenant, caseId] as const;
  const query = useQuery({
    queryKey,
    queryFn: () => financialReviewClient.workspace(tenant, caseId),
    enabled: !mockWorkspace,
  });
  const workspace = mockWorkspace
    ? mockCaseHealth(tenant.orgId, caseId).financial
    : query.data;
  const workspaceIdentity = JSON.stringify([
    tenant.orgId,
    tenant.userId,
    caseId,
    mockWorkspace,
  ]);
  const refreshQueues = useRef(new Map<string, Promise<void>>());

  function refreshWorkspace() {
    const previous = refreshQueues.current.get(workspaceIdentity);
    const refresh = (previous ?? Promise.resolve())
      .catch(() => undefined)
      .then(async () => {
        await queryClient.cancelQueries({ queryKey, exact: true });
        await queryClient.fetchQuery({
          queryKey,
          queryFn: () => financialReviewClient.workspace(tenant, caseId),
          staleTime: 0,
        });
      })
      .finally(() => {
        if (refreshQueues.current.get(workspaceIdentity) === refresh) {
          refreshQueues.current.delete(workspaceIdentity);
        }
      });
    refreshQueues.current.set(workspaceIdentity, refresh);
    return refresh;
  }

  return (
    <div className="space-y-3">
      {mockWorkspace ? (
        <Alert title="Mocked demo seed data" tone="warning">
          Financial workspace data is frontend-only. Editing is disabled for
          mocked records.
        </Alert>
      ) : null}
      {query.isError && !workspace ? <ErrorPanel error={query.error} /> : null}
      {!workspace ? (
        <div aria-label="Loading financial workspace" className="space-y-3">
          <Skeleton className="h-20" />
          <Skeleton className="h-96" />
        </div>
      ) : (
        <Fragment key={workspaceIdentity}>
          {!mockWorkspace ? (
            <FinancialControls
              tenant={tenant}
              caseId={caseId}
              workspace={workspace}
              onWorkspace={(updateWorkspace) =>
                queryClient.setQueryData(queryKey, updateWorkspace)
              }
              onRefresh={refreshWorkspace}
            />
          ) : null}
          <FinancialSections
            workspace={workspace}
            mocked={mockWorkspace}
            tenant={tenant}
            caseId={caseId}
            client={financialReviewClient}
            onMutation={async (updateWorkspace) => {
              queryClient.setQueryData(queryKey, updateWorkspace);
              await refreshWorkspace();
            }}
          />
        </Fragment>
      )}
    </div>
  );
}

function FinancialControls({
  tenant,
  caseId,
  workspace,
  onWorkspace,
  onRefresh,
}: {
  tenant: TenantHeaders;
  caseId: string;
  workspace: FinancialDataWorkspaceRead;
  onWorkspace: (updateWorkspace: WorkspaceUpdater) => void;
  onRefresh: () => Promise<void>;
}) {
  const [documentId, setDocumentId] = useState("");
  const [extractionId, setExtractionId] = useState("");
  const [pending, setPending] = useState<"map" | "validate">();
  const [error, setError] = useState<string>();
  const [success, setSuccess] = useState<string>();
  const linkedSourceRowIds = new Set(
    workspace.recordSourceLinks.map((link) => link.sourceRowId),
  );
  const unmappedRows = workspace.sourceRows.filter(
    (row) => !linkedSourceRowIds.has(row.id),
  );

  async function mapWorkspace() {
    const trimmedDocumentId = documentId.trim();
    const trimmedExtractionId = extractionId.trim();
    if (Boolean(trimmedDocumentId) === Boolean(trimmedExtractionId)) {
      setError("Enter exactly one document ID or document extraction ID.");
      return;
    }
    setPending("map");
    setError(undefined);
    setSuccess(undefined);
    try {
      const result = await financialReviewClient.map(tenant, caseId, {
        documentId: trimmedDocumentId || undefined,
        documentExtractionId: trimmedExtractionId || undefined,
      });
      await onRefresh();
      setSuccess(
        `Mapping complete: ${result.summary.mappedSourceRowCount} of ${result.summary.sourceRowCount} source rows mapped.`,
      );
    } catch (caught) {
      setError(await financialErrorMessage(caught));
    } finally {
      setPending(undefined);
    }
  }

  async function validateWorkspace() {
    setPending("validate");
    setError(undefined);
    setSuccess(undefined);
    try {
      const result = await financialReviewClient.validate(tenant, caseId);
      onWorkspace((currentWorkspace) => ({
        ...currentWorkspace,
        validationIssues: result.issues,
        validationSummary: result.summary,
      }));
      await onRefresh();
      setSuccess(`Validation refreshed: ${result.issueCount} issues.`);
    } catch (caught) {
      setError(await financialErrorMessage(caught));
    } finally {
      setPending(undefined);
    }
  }

  return (
    <section
      className="mb-4 rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--surface))] p-3"
      aria-label="Map and validate financial data"
    >
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold">Map & validate source data</h2>
          <p className="text-xs text-[rgb(var(--muted-foreground))]">
            Map a completed document extraction, then run canonical validation.
          </p>
        </div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={Boolean(pending)}
          onClick={() => void validateWorkspace()}
        >
          {pending === "validate" ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : null}{" "}
          Revalidate
        </Button>
      </div>
      <div className="grid gap-2 md:grid-cols-[1fr_1fr_auto]">
        <div className="space-y-1">
          <Label>Document ID</Label>
          <Input
            aria-label="Document ID"
            value={documentId}
            onChange={(event) => setDocumentId(event.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label>Extraction ID</Label>
          <Input
            aria-label="Extraction ID"
            value={extractionId}
            onChange={(event) => setExtractionId(event.target.value)}
          />
        </div>
        <Button
          type="button"
          className="self-end"
          disabled={Boolean(pending)}
          onClick={() => void mapWorkspace()}
        >
          {pending === "map" ? (
            <Loader2 className="size-4 animate-spin" />
          ) : null}{" "}
          Map financial data
        </Button>
      </div>
      {error ? (
        <div className="mt-2">
          <Alert title="Financial workflow failed" tone="danger">
            {error}
          </Alert>
        </div>
      ) : null}
      {success ? (
        <output className="mt-2 block text-xs text-emerald-800">
          <CheckCircle2 className="mr-1 inline size-3.5" />
          {success}
        </output>
      ) : null}
      {unmappedRows.length ? (
        <div className="mt-3 rounded-md border border-amber-300 bg-amber-50 p-3 text-xs text-amber-950">
          <div className="font-semibold">
            {unmappedRows.length} unmapped source rows need review
          </div>
          <div className="mt-2 space-y-2" aria-label="Unmapped source rows">
            {unmappedRows.map((row) => (
              <div
                key={row.id}
                className="rounded border border-amber-200 bg-white p-2"
              >
                <div className="font-medium">
                  Row {row.rowIndex ?? "unknown"}
                </div>
                <code className="mt-1 block break-all text-[10px]">
                  {JSON.stringify(row.locator)}
                </code>
                <code className="mt-1 block break-all text-[10px]">
                  {JSON.stringify(row.rawPayload)}
                </code>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </section>
  );
}
