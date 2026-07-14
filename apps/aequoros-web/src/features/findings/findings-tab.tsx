import type { FindingRead } from "@aequoros/risk-service-api";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  Badge,
  Button,
  Input,
  Label,
  Select,
  SelectItem,
  Textarea,
} from "../../components/ui";
import { riskApi, type TenantHeaders } from "../../lib/api";
import { labelize } from "../../lib/utils";
import { DataList, ErrorPanel } from "../../shared/route-ui";

const findingSchema = z.object({
  riskType: z.string().min(1),
  title: z.string().min(1),
  summary: z.string().min(1),
  severity: z.string().min(1),
});

const findingStatusSchema = z.object({
  status: z.enum([
    "open",
    "accepted",
    "acknowledged",
    "dismissed",
    "needs_review",
    "resolved",
    "superseded",
  ]),
  dispositionReason: z.string().max(500).optional(),
});

type FindingForm = z.infer<typeof findingSchema>;
type FindingStatusForm = z.infer<typeof findingStatusSchema>;

export function FindingsTab({
  tenant,
  caseId,
  mutationDisabled = false,
  mutationDisabledReason = "retired-case",
}: {
  tenant: TenantHeaders;
  caseId: string;
  mutationDisabled?: boolean;
  mutationDisabledReason?: "demo" | "retired-case";
}) {
  const queryClient = useQueryClient();
  const form = useForm<FindingForm>({
    resolver: zodResolver(findingSchema),
    defaultValues: {
      riskType: "manual_review",
      title: "",
      summary: "",
      severity: "medium",
    },
  });
  const query = useQuery({
    queryKey: ["findings", tenant, caseId],
    queryFn: () => riskApi.findings(tenant, caseId),
  });
  const mutation = useMutation({
    mutationFn: (values: FindingForm) =>
      riskApi.createFinding(tenant, caseId, {
        ...values,
        details: {},
      }),
    onSuccess: () => {
      form.reset({
        riskType: "manual_review",
        title: "",
        summary: "",
        severity: "medium",
      });
      void queryClient.invalidateQueries({ queryKey: ["findings"] });
    },
  });

  return (
    <div className="grid gap-3 lg:grid-cols-[280px_1fr]">
      <form
        className="space-y-3 rounded-md border border-[rgb(var(--border))] p-3"
        onSubmit={form.handleSubmit((values) => mutation.mutate(values))}
      >
        <Label>Create manual finding</Label>
        {mutationDisabled ? (
          <div className="text-xs text-[rgb(var(--muted-foreground))]">
            {mutationDisabledReason === "retired-case"
              ? "Finding mutations are unavailable for retired cases."
              : "Finding mutations are unavailable in demo mode."}
          </div>
        ) : null}
        <Input
          disabled={mutationDisabled}
          placeholder="Risk type"
          {...form.register("riskType")}
        />
        <Input
          disabled={mutationDisabled}
          placeholder="Title"
          {...form.register("title")}
        />
        <Textarea
          disabled={mutationDisabled}
          placeholder="Summary"
          {...form.register("summary")}
        />
        <Input
          disabled={mutationDisabled}
          placeholder="Severity"
          {...form.register("severity")}
        />
        {mutation.isError ? <ErrorPanel error={mutation.error} /> : null}
        <Button type="submit" disabled={mutationDisabled || mutation.isPending}>
          Create finding
        </Button>
      </form>
      <DataList
        loading={query.isLoading}
        error={query.error}
        empty="No findings"
      >
        {query.data?.map((finding) => (
          <FindingReviewItem
            key={finding.id}
            finding={finding}
            tenant={tenant}
            disabled={mutationDisabled}
          />
        ))}
      </DataList>
    </div>
  );
}

export function FindingReviewItem({
  finding,
  tenant,
  onUpdated,
  disabled = false,
}: {
  finding: FindingRead;
  tenant: TenantHeaders;
  onUpdated?: () => void;
  disabled?: boolean;
}) {
  const queryClient = useQueryClient();
  const form = useForm<FindingStatusForm>({
    resolver: zodResolver(findingStatusSchema),
    defaultValues: {
      status: finding.status as FindingStatusForm["status"],
      dispositionReason: finding.dispositionReason ?? "",
    },
  });
  const mutation = useMutation({
    mutationFn: (values: FindingStatusForm) =>
      riskApi.updateFinding(tenant, finding.id, {
        status: values.status,
        dispositionReason: values.dispositionReason?.trim() || undefined,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["findings"] });
      void queryClient.invalidateQueries({ queryKey: ["cases"] });
      onUpdated?.();
      toast.success("Finding updated");
    },
  });

  return (
    <div className="grid gap-2 rounded-md border border-[rgb(var(--border))] p-3 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <Badge
          tone={
            finding.severity === "high" || finding.severity === "critical"
              ? "danger"
              : "warning"
          }
        >
          {finding.severity}
        </Badge>
        <Badge>{finding.status}</Badge>
        <span className="font-medium">{finding.title}</span>
      </div>
      <div>{finding.summary}</div>
      <div className="text-[rgb(var(--muted-foreground))]">
        {finding.riskType} - score impact {finding.scoreImpact ?? "n/a"}
      </div>
      <form
        className="grid gap-2 border-t border-[rgb(var(--border))] pt-2 md:grid-cols-[160px_1fr_auto]"
        onSubmit={form.handleSubmit((values) => mutation.mutate(values))}
      >
        <Select
          disabled={disabled}
          value={form.watch("status")}
          onValueChange={(value) =>
            form.setValue("status", value as FindingStatusForm["status"])
          }
          placeholder="Status"
        >
          {findingStatusSchema.shape.status.options.map((status) => (
            <SelectItem key={status} value={status}>
              {labelize(status)}
            </SelectItem>
          ))}
        </Select>
        <Input
          disabled={disabled}
          placeholder="Disposition reason"
          {...form.register("dispositionReason")}
        />
        <Button
          type="submit"
          size="sm"
          disabled={disabled || mutation.isPending}
        >
          {mutation.isPending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : null}
          Update
        </Button>
      </form>
      {mutation.isError ? <ErrorPanel error={mutation.error} /> : null}
    </div>
  );
}
