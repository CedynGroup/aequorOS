import type { UploadRequestResponse } from "@aequoros/risk-service-api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { Loader2 } from "lucide-react";

import {
  Badge,
  Button,
  Input,
  Label,
  Select,
  SelectItem,
} from "../../components/ui";
import { riskApi, type TenantHeaders } from "../../lib/api";
import { formatJson } from "../../lib/utils";
import { DataList, ErrorPanel } from "../../shared/route-ui";

const documentUploadSchema = z.object({
  filename: z.string().min(1),
  contentType: z.enum([
    "application/pdf",
    "text/csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  ]),
  byteSize: z.string().regex(/^[1-9]\d*$/, "Enter a positive byte count."),
  sha256: z.string().optional(),
});

type DocumentUploadForm = z.infer<typeof documentUploadSchema>;

export function DocumentsTab({
  tenant,
  caseId,
  mutationDisabled = false,
}: {
  tenant: TenantHeaders;
  caseId: string;
  mutationDisabled?: boolean;
}) {
  const queryClient = useQueryClient();
  const [uploadResult, setUploadResult] =
    useState<UploadRequestResponse | null>(null);
  const form = useForm<DocumentUploadForm>({
    resolver: zodResolver(documentUploadSchema),
    defaultValues: {
      filename: "borrowing-base.pdf",
      contentType: "application/pdf",
      byteSize: "1024",
      sha256: "",
    },
  });
  const query = useQuery({
    queryKey: ["documents", tenant, caseId],
    queryFn: () => riskApi.documents(tenant, caseId),
  });
  const uploadMutation = useMutation({
    mutationFn: (values: DocumentUploadForm) =>
      riskApi.requestUpload(tenant, {
        caseId,
        filename: values.filename,
        contentType: values.contentType,
        byteSize: Number(values.byteSize),
        sha256: values.sha256?.trim() || null,
      }),
    onSuccess: (data) => {
      setUploadResult(data);
      void queryClient.invalidateQueries({ queryKey: ["documents"] });
      toast.success("Upload request created");
    },
  });
  const completeMutation = useMutation({
    mutationFn: (documentId: string) =>
      riskApi.completeUpload(tenant, documentId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["documents"] });
      toast.success("Upload completion recorded");
    },
  });
  const parseMutation = useMutation({
    mutationFn: (documentId: string) =>
      riskApi.parseDocument(tenant, documentId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["documents"] });
      toast.success("Document parse requested");
    },
  });
  const downloadMutation = useMutation({
    mutationFn: (documentId: string) => riskApi.downloadUrl(tenant, documentId),
    onSuccess: (data) => window.open(data.url, "_blank", "noopener,noreferrer"),
  });

  return (
    <div className="grid gap-3 lg:grid-cols-[320px_1fr]">
      <form
        className="space-y-3 rounded-md border border-[rgb(var(--border))] p-3"
        onSubmit={form.handleSubmit((values) => uploadMutation.mutate(values))}
      >
        <Label>Request upload</Label>
        <Input
          placeholder="Filename"
          disabled={mutationDisabled}
          {...form.register("filename")}
        />
        <Select
          disabled={mutationDisabled}
          value={form.watch("contentType")}
          onValueChange={(value) =>
            form.setValue(
              "contentType",
              value as DocumentUploadForm["contentType"],
            )
          }
          placeholder="Content type"
        >
          <SelectItem value="application/pdf">PDF</SelectItem>
          <SelectItem value="text/csv">CSV</SelectItem>
          <SelectItem value="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet">
            XLSX
          </SelectItem>
        </Select>
        <Input
          placeholder="Byte size"
          inputMode="numeric"
          disabled={mutationDisabled}
          {...form.register("byteSize")}
        />
        <Input
          placeholder="SHA-256 optional"
          disabled={mutationDisabled}
          {...form.register("sha256")}
        />
        {uploadMutation.isError ? (
          <ErrorPanel error={uploadMutation.error} />
        ) : null}
        <Button
          type="submit"
          disabled={mutationDisabled || uploadMutation.isPending}
        >
          {uploadMutation.isPending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : null}
          Create request
        </Button>
        {uploadResult ? (
          <div className="space-y-2 rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--surface-2))] p-2 text-xs">
            <div className="font-medium">Upload request</div>
            <div>Upload destination ready</div>
            <div>
              {uploadResult.method} expires in {uploadResult.expiresInSeconds}s
            </div>
            <pre className="max-h-24 overflow-auto rounded bg-[rgb(var(--surface))] p-2">
              {formatJson(uploadResult.headers)}
            </pre>
            <div className="break-all text-[rgb(var(--muted-foreground))]">
              {uploadResult.uploadUrl}
            </div>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={mutationDisabled || completeMutation.isPending}
              onClick={() => completeMutation.mutate(uploadResult.documentId)}
            >
              Complete upload
            </Button>
          </div>
        ) : null}
        {completeMutation.isError ? (
          <ErrorPanel error={completeMutation.error} />
        ) : null}
      </form>
      <DataList
        loading={query.isLoading}
        error={query.error}
        empty="No documents uploaded"
      >
        {query.data?.map((document) => (
          <div
            key={document.id}
            className="grid grid-cols-[1fr_auto] gap-2 rounded-md border border-[rgb(var(--border))] p-3 text-xs"
          >
            <div className="min-w-0">
              <div className="truncate font-medium">{document.filename}</div>
              <div className="text-[rgb(var(--muted-foreground))]">
                {document.documentType ?? "Unclassified"} - {document.source}
              </div>
              {document.parseError ? (
                <div className="mt-1 text-red-700">{document.parseError}</div>
              ) : null}
            </div>
            <div className="flex flex-wrap justify-end gap-2">
              <Badge>{document.status}</Badge>
              <Badge
                tone={
                  document.parseStatus === "parsed"
                    ? "success"
                    : document.parseStatus === "failed"
                      ? "danger"
                      : "neutral"
                }
              >
                {document.parseStatus}
              </Badge>
              <Button
                size="sm"
                variant="outline"
                disabled={
                  mutationDisabled ||
                  document.status !== "upload_requested" ||
                  completeMutation.isPending
                }
                onClick={() => completeMutation.mutate(document.id)}
              >
                Complete
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={
                  mutationDisabled ||
                  document.status !== "uploaded" ||
                  parseMutation.isPending
                }
                onClick={() => parseMutation.mutate(document.id)}
              >
                Parse
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={
                  document.status !== "uploaded" || downloadMutation.isPending
                }
                onClick={() => downloadMutation.mutate(document.id)}
              >
                Download URL
              </Button>
            </div>
          </div>
        ))}
        {completeMutation.isError ? (
          <ErrorPanel error={completeMutation.error} />
        ) : null}
        {parseMutation.isError ? (
          <ErrorPanel error={parseMutation.error} />
        ) : null}
        {downloadMutation.isError ? (
          <ErrorPanel error={downloadMutation.error} />
        ) : null}
      </DataList>
    </div>
  );
}
