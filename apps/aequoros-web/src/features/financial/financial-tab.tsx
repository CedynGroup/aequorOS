import type { FinancialDataWorkspaceRead } from "@aequoros/risk-service-api";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2 } from "lucide-react";
import { useState } from "react";

import { Alert, Button, Input, Label, Skeleton } from "../../components/ui";
import type { TenantHeaders } from "../../lib/api";
import { ErrorPanel } from "../../shared/route-ui";
import { emptyWorkspace } from "../demo-data/demo-data";
import {
  financialErrorMessage,
  financialReviewClient,
} from "./financial-client";
import { FinancialSections } from "./financial-sections";

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
  });
  const workspace =
    query.data ??
    (mockWorkspace ? emptyWorkspace(tenant.orgId, caseId) : undefined);

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
        <>
          {!mockWorkspace || query.data ? (
            <FinancialControls
              tenant={tenant}
              caseId={caseId}
              workspace={workspace}
              onWorkspace={(nextWorkspace) =>
                queryClient.setQueryData(queryKey, nextWorkspace)
              }
              onRefresh={() => void queryClient.invalidateQueries({ queryKey })}
            />
          ) : null}
          <FinancialSections
            workspace={workspace}
            mocked={mockWorkspace && !query.data}
            tenant={tenant}
            caseId={caseId}
            client={financialReviewClient}
            onMutation={(nextWorkspace) => {
              queryClient.setQueryData(queryKey, nextWorkspace);
              void queryClient.invalidateQueries({ queryKey });
            }}
          />
        </>
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
  onWorkspace: (workspace: FinancialDataWorkspaceRead) => void;
  onRefresh: () => void;
}) {
  const [documentId, setDocumentId] = useState("");
  const [extractionId, setExtractionId] = useState("");
  const [pending, setPending] = useState<"map" | "validate">();
  const [error, setError] = useState<string>();
  const [success, setSuccess] = useState<string>();

  async function mapWorkspace() {
    if (!documentId.trim() && !extractionId.trim()) {
      setError("Enter a document ID or document extraction ID to map.");
      return;
    }
    setPending("map");
    setError(undefined);
    setSuccess(undefined);
    try {
      const result = await financialReviewClient.map(tenant, caseId, {
        documentId: documentId.trim() || undefined,
        documentExtractionId: extractionId.trim() || undefined,
      });
      setSuccess(
        `Mapping complete: ${result.summary.sourceRowCount} source rows reviewed.`,
      );
      onRefresh();
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
      onWorkspace({
        ...workspace,
        validationIssues: result.issues,
        validationSummary: result.summary,
      });
      setSuccess(`Validation refreshed: ${result.issueCount} issues.`);
      onRefresh();
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
    </section>
  );
}
