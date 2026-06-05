import type {
  CaseDecision,
  CaseDecisionRead,
} from "@aequoros/risk-service-api";
import { zodResolver } from "@hookform/resolvers/zod";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { z } from "zod";

import {
  Button,
  Label,
  Select,
  SelectItem,
  Textarea,
} from "../../components/ui";
import { riskApi, type TenantHeaders } from "../../lib/api";
import { labelize, truncateId } from "../../lib/utils";
import { DataList, ErrorPanel } from "../../shared/route-ui";
import { DecisionBadge, relative } from "../risk-console/format";

const decisionSchema = z.object({
  decision: z.enum(["approved", "rejected", "needs_more_info", "escalated"]),
  reason: z.string().max(500).optional(),
});

type DecisionForm = z.infer<typeof decisionSchema>;

export function DecisionsTab({ tenant, caseId }: { tenant: TenantHeaders; caseId: string }) {
  const queryClient = useQueryClient();
  const form = useForm<DecisionForm>({
    resolver: zodResolver(decisionSchema),
    defaultValues: { decision: "approved", reason: "" },
  });
  const query = useQuery({
    queryKey: ["decisions", tenant, caseId],
    queryFn: () => riskApi.decisions(tenant, caseId),
  });
  const mutation = useMutation({
    mutationFn: (values: DecisionForm) =>
      riskApi.createDecision(tenant, caseId, {
        decision: values.decision as CaseDecision,
        reason: values.reason || null,
      }),
    onSuccess: () => {
      form.reset({ decision: "approved", reason: "" });
      void queryClient.invalidateQueries({ queryKey: ["decisions"] });
      void queryClient.invalidateQueries({ queryKey: ["case"] });
    },
  });

  return (
    <div className="grid gap-3 lg:grid-cols-[280px_1fr]">
      <form
        className="space-y-3 rounded-md border border-[rgb(var(--border))] p-3"
        onSubmit={form.handleSubmit((values) => mutation.mutate(values))}
      >
        <Label>Record decision</Label>
        <Select
          value={form.watch("decision")}
          onValueChange={(value) => form.setValue("decision", value as DecisionForm["decision"])}
          placeholder="Decision"
        >
          {decisionSchema.shape.decision.options.map((decision) => (
            <SelectItem key={decision} value={decision}>{labelize(decision)}</SelectItem>
          ))}
        </Select>
        <Textarea placeholder="Reason" {...form.register("reason")} />
        {mutation.isError ? <ErrorPanel error={mutation.error} /> : null}
        <Button type="submit" disabled={mutation.isPending}>Submit decision</Button>
      </form>
      <DataList loading={query.isLoading} error={query.error} empty="No decisions recorded">
        {query.data?.map((decision: CaseDecisionRead) => (
          <div key={decision.id} className="grid gap-1 rounded-md border border-[rgb(var(--border))] p-3 text-xs">
            <div className="flex flex-wrap items-center gap-2">
              <DecisionBadge value={decision.decision} />
              <span>Previous: {labelize(decision.previousDecision)}</span>
              <span>{relative(decision.createdAt)}</span>
            </div>
            <div>{decision.reason ?? "No reason provided."}</div>
            <div className="text-[rgb(var(--muted-foreground))]">Decided by {truncateId(decision.decidedBy)}</div>
          </div>
        ))}
      </DataList>
    </div>
  );
}
