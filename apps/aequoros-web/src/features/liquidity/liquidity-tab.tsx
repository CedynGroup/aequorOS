import type {
  LiquidityFindingRead,
  LiquidityMetricRead,
  LiquidityReviewAction,
} from "@aequoros/risk-service-api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Alert, Button, Input, Label, Skeleton } from "../../components/ui";
import type { TenantHeaders } from "../../lib/api";
import { ErrorPanel } from "../../shared/route-ui";
import { FindingReviewCard } from "../findings/finding-review-card";
import { liquidityReviewClient } from "./liquidity-client";

export function LiquidityTab({
  tenant,
  caseId,
}: {
  tenant: TenantHeaders;
  caseId: string;
}) {
  const query = useQuery({
    queryKey: ["liquidity-summary", tenant, caseId],
    queryFn: () => liquidityReviewClient.summary(tenant, caseId),
  });

  if (query.isLoading) {
    return (
      <div aria-label="Loading liquidity analysis" className="space-y-3">
        <Skeleton className="h-24" />
        <Skeleton className="h-52" />
      </div>
    );
  }
  if (query.error) return <ErrorPanel error={query.error} />;
  if (!query.data || query.data.status === "not_calculated") {
    return (
      <Alert title="No liquidity analysis">
        Run a successful balance-sheet forecast to calculate liquidity metrics
        and findings.
      </Alert>
    );
  }

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">Liquidity risk summary</h2>
          <p className="mt-1 text-xs text-[rgb(var(--muted-foreground))]">
            Forecast input {query.data.calculationInputHash?.slice(0, 12)} · as
            of {formatDate(query.data.asOfDate)}
          </p>
        </div>
        <span className="text-xs text-[rgb(var(--muted-foreground))]">
          {query.data.findings.length} findings
        </span>
      </header>
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
        {query.data.metrics.map((metric) => (
          <MetricCard key={metric.key} metric={metric} />
        ))}
      </div>
      <section
        aria-labelledby="liquidity-findings-heading"
        className="space-y-2"
      >
        <Label id="liquidity-findings-heading">Liquidity findings</Label>
        {query.data.findings.length ? (
          query.data.findings.map((finding) => (
            <LiquidityFindingCard
              key={finding.id}
              tenant={tenant}
              caseId={caseId}
              finding={finding}
            />
          ))
        ) : (
          <Alert title="No liquidity concerns">
            The latest successful forecast did not cross an MVP liquidity risk
            threshold.
          </Alert>
        )}
      </section>
    </div>
  );
}

function MetricCard({ metric }: { metric: LiquidityMetricRead }) {
  return (
    <div className="min-w-0 rounded-md border border-[rgb(var(--border))] p-3">
      <div className="text-xs text-[rgb(var(--muted-foreground))]">
        {metric.label}
      </div>
      <div className="mt-1 truncate text-lg font-semibold">
        {formatMetric(metric)}
      </div>
      <div className="mt-1 text-[11px] text-[rgb(var(--muted-foreground))]">
        {metric.periodNumber ? `Period ${metric.periodNumber} · ` : ""}
        {metric.description}
      </div>
    </div>
  );
}

function LiquidityFindingCard({
  tenant,
  caseId,
  finding,
}: {
  tenant: TenantHeaders;
  caseId: string;
  finding: LiquidityFindingRead;
}) {
  const queryClient = useQueryClient();
  const [dismissReason, setDismissReason] = useState("");
  const mutation = useMutation({
    mutationFn: (action: LiquidityReviewAction) =>
      liquidityReviewClient.review(tenant, caseId, finding.id, {
        action,
        reason: action === "dismiss" ? dismissReason.trim() : undefined,
      }),
    onSuccess: (_updated, action) => {
      void queryClient.invalidateQueries({ queryKey: ["liquidity-summary"] });
      void queryClient.invalidateQueries({ queryKey: ["findings"] });
      toast.success(
        action === "dismiss"
          ? "Liquidity finding dismissed"
          : "Liquidity finding acknowledged",
      );
    },
  });
  const resolved = [
    "accepted",
    "acknowledged",
    "dismissed",
    "resolved",
    "superseded",
  ].includes(finding.status);

  return (
    <FindingReviewCard
      finding={finding}
      metadata={`${finding.ruleId} · ${finding.ruleVersion}`}
      evidence={
        <details className="rounded border border-[rgb(var(--border))] p-2">
          <summary className="cursor-pointer font-medium">
            Supporting evidence ({finding.evidence.length})
          </summary>
          <ul className="mt-2 space-y-2">
            {finding.evidence.map((evidence) => (
              <li key={evidence.id} className="min-w-0">
                <a
                  className="inline-flex max-w-full items-center gap-1 text-[rgb(var(--primary))] underline"
                  href={evidence.sourceUrl}
                >
                  <span className="truncate">{evidence.label}</span>
                </a>
                {evidence.quote ? (
                  <div className="mt-0.5 text-[rgb(var(--muted-foreground))]">
                    {evidence.quote}
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        </details>
      }
    >
      {!resolved ? (
        <div className="grid gap-2 border-t border-[rgb(var(--border))] pt-2 md:grid-cols-[1fr_auto_auto]">
          <Input
            aria-label={`Dismissal reason for ${finding.title}`}
            placeholder="Dismissal reason (required to dismiss)"
            value={dismissReason}
            onChange={(event) => setDismissReason(event.target.value)}
            disabled={mutation.isPending}
          />
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={mutation.isPending}
            onClick={() => mutation.mutate("acknowledge")}
          >
            {mutation.isPending && mutation.variables === "acknowledge" ? (
              <Loader2 className="size-4 animate-spin" />
            ) : null}
            Acknowledge
          </Button>
          <Button
            type="button"
            size="sm"
            variant="danger"
            disabled={mutation.isPending || !dismissReason.trim()}
            onClick={() => mutation.mutate("dismiss")}
          >
            {mutation.isPending && mutation.variables === "dismiss" ? (
              <Loader2 className="size-4 animate-spin" />
            ) : null}
            Dismiss
          </Button>
        </div>
      ) : finding.dispositionReason ? (
        <div className="border-t border-[rgb(var(--border))] pt-2 text-[rgb(var(--muted-foreground))]">
          Review note: {finding.dispositionReason}
        </div>
      ) : null}
      {mutation.error ? <ErrorPanel error={mutation.error} /> : null}
    </FindingReviewCard>
  );
}

function formatMetric(metric: LiquidityMetricRead) {
  const value = Number(metric.value);
  if (metric.unit === "ratio") return `${value.toFixed(2)}x`;
  if (metric.unit === "forecast_periods") return `${value} periods`;
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: metric.unit,
    maximumFractionDigits: 0,
  }).format(value);
}

function formatDate(value: string | null | undefined) {
  return value
    ? new Date(`${value}T00:00:00`).toLocaleDateString()
    : "not available";
}
