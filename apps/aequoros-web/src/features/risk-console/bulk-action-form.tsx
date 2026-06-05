import {
  CaseStatus,
  type CaseStatus as CaseStatusType,
  type Payload,
} from "@aequoros/risk-service-api";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import {
  Alert,
  Button,
  Input,
  Label,
  Select,
  SelectItem,
} from "../../components/ui";
import { riskApi, type TenantHeaders } from "../../lib/api";
import { labelize } from "../../lib/utils";
import { ErrorPanel } from "../../shared/route-ui";
import { BulkResult } from "./bulk-result";

const bulkActionSchema = z.object({
  action: z.enum(["assign", "unassign", "update_status", "archive"]),
  assignedToUserId: z.string().optional(),
  status: z.string().optional(),
});

type BulkActionFormValues = z.infer<typeof bulkActionSchema>;

export function BulkActionForm({
  selectedIds,
  tenant,
}: {
  selectedIds: string[];
  tenant: TenantHeaders;
}) {
  const queryClient = useQueryClient();
  const [result, setResult] = useState<Awaited<ReturnType<typeof riskApi.bulkCases>> | null>(null);
  const form = useForm<BulkActionFormValues>({
    resolver: zodResolver(bulkActionSchema),
    defaultValues: { action: "assign", status: CaseStatus.InReview },
  });
  const action = form.watch("action");
  const mutation = useMutation({
    mutationFn: (payload: Payload) => riskApi.bulkCases(tenant, payload),
    onSuccess: (data) => {
      setResult(data);
      void queryClient.invalidateQueries({ queryKey: ["cases"] });
    },
  });

  const submit = form.handleSubmit((values) => {
    if (selectedIds.length === 0) return;
    if (values.action === "assign") {
      mutation.mutate({
        action: "assign",
        caseIds: selectedIds,
        assignedToUserId: values.assignedToUserId?.trim() || tenant.userId,
      });
    } else if (values.action === "update_status") {
      mutation.mutate({
        action: "update_status",
        caseIds: selectedIds,
        status: (values.status as CaseStatusType | undefined) ?? CaseStatus.InReview,
      });
    } else {
      mutation.mutate({ action: values.action, caseIds: selectedIds });
    }
  });

  return (
    <form className="space-y-4 p-4" onSubmit={submit}>
      <Alert title={`${selectedIds.length} selected cases`} />
      <div className="grid gap-2">
        <Label>Action</Label>
        <Select value={action} onValueChange={(value) => form.setValue("action", value as BulkActionFormValues["action"])} placeholder="Action">
          {bulkActionSchema.shape.action.options.map((bulkAction) => (
            <SelectItem key={bulkAction} value={bulkAction}>
              {labelize(bulkAction)}
            </SelectItem>
          ))}
        </Select>
      </div>
      {action === "assign" ? (
        <div className="grid gap-2">
          <Label>Assignee user ID</Label>
          <Input {...form.register("assignedToUserId")} placeholder={tenant.userId} />
        </div>
      ) : null}
      {action === "update_status" ? (
        <div className="grid gap-2">
          <Label>Status</Label>
          <Select value={form.watch("status") ?? CaseStatus.InReview} onValueChange={(value) => form.setValue("status", value)} placeholder="Status">
            {Object.values(CaseStatus).map((status) => (
              <SelectItem key={status} value={status}>
                {labelize(status)}
              </SelectItem>
            ))}
          </Select>
        </div>
      ) : null}
      {mutation.isError ? <ErrorPanel error={mutation.error} /> : null}
      <Button type="submit" disabled={mutation.isPending}>
        {mutation.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
        Apply action
      </Button>
      {result ? <BulkResult result={result} /> : null}
    </form>
  );
}
