import type {
  LiquidityFindingRead,
  LiquidityMetricRead,
  LiquidityReviewAction,
  LiquiditySummaryRead,
} from "@aequoros/risk-service-api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
  Alert,
  Button,
  Input,
  Label,
  Panel,
  PanelHeader,
  Select,
  SelectItem,
  Skeleton,
} from "../../components/ui";
import { riskApi, type TenantHeaders } from "../../lib/api";
import { formatMoney } from "../../lib/money";
import { truncateId } from "../../lib/utils";
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
  const [scenarioId, setScenarioId] = useState("");
  const [runId, setRunId] = useState("");
  const [runOffset, setRunOffset] = useState(0);
  const scenarios = useQuery({
    queryKey: ["scenarios", tenant, caseId],
    queryFn: () => riskApi.scenarios(tenant, caseId),
  });
  const availableScenarios = scenarios.data?.scenarios ?? [];
  const selectedScenario =
    availableScenarios.find((scenario) => scenario.id === scenarioId) ??
    availableScenarios[0];
  const runs = useQuery({
    queryKey: [
      "calculation-runs",
      tenant,
      caseId,
      selectedScenario?.id,
      runOffset,
    ],
    queryFn: () =>
      riskApi.calculationRuns(
        tenant,
        caseId,
        selectedScenario?.id,
        25,
        runOffset,
      ),
    enabled: Boolean(selectedScenario),
  });
  const pageSuccessfulRuns =
    runs.data?.runs.filter((run) => run.status === "succeeded") ?? [];
  const selectedRunId = runId || runs.data?.latestSuccessfulRunId || "";
  const selectedRun = useQuery({
    queryKey: ["calculation-run", tenant, caseId, selectedRunId],
    queryFn: () => riskApi.calculationRun(tenant, caseId, selectedRunId),
    enabled: Boolean(selectedRunId),
  });
  const selectableRuns = selectedRun.data
    ? [
        selectedRun.data,
        ...pageSuccessfulRuns.filter((run) => run.id !== selectedRun.data.id),
      ]
    : pageSuccessfulRuns;
  const query = useQuery({
    queryKey: [
      "liquidity-summary",
      tenant,
      caseId,
      selectedScenario?.id,
      selectedRun.data?.id,
    ],
    queryFn: () =>
      liquidityReviewClient.summary(
        tenant,
        caseId,
        selectedScenario?.id,
        selectedRun.data?.id,
      ),
    enabled: Boolean(
      selectedScenario && selectedRun.data?.status === "succeeded",
    ),
  });

  useEffect(() => {
    setScenarioId("");
    setRunId("");
    setRunOffset(0);
  }, [caseId, tenant.orgId]);

  if (scenarios.isLoading) {
    return (
      <div aria-label="Loading liquidity analysis" className="space-y-3">
        <Skeleton className="h-24" />
        <Skeleton className="h-52" />
      </div>
    );
  }
  if (scenarios.error) return <ErrorPanel error={scenarios.error} />;
  if (!selectedScenario) {
    return (
      <Alert title="No liquidity analysis">
        Initialize a scenario and run a successful balance-sheet forecast to
        calculate liquidity metrics and findings.
      </Alert>
    );
  }

  const selectScenario = (value: string) => {
    setScenarioId(value);
    setRunId("");
    setRunOffset(0);
  };

  const analysis = runs.isLoading ? (
    <div aria-label="Loading liquidity analysis" className="space-y-3">
      <Skeleton className="h-24" />
      <Skeleton className="h-52" />
    </div>
  ) : runs.error ? (
    <ErrorPanel error={runs.error} />
  ) : !selectedRunId ? (
    <Alert title="No liquidity analysis">
      Run a successful balance-sheet forecast for {selectedScenario.name} to
      calculate liquidity metrics and findings.
    </Alert>
  ) : selectedRun.isLoading ? (
    <div aria-label="Loading liquidity analysis" className="space-y-3">
      <Skeleton className="h-24" />
      <Skeleton className="h-52" />
    </div>
  ) : selectedRun.error ? (
    <ErrorPanel error={selectedRun.error} />
  ) : selectedRun.data?.status !== "succeeded" ? (
    <Alert title="No liquidity analysis">
      Select a successful balance-sheet forecast to review liquidity analysis.
    </Alert>
  ) : query.isLoading ? (
    <div aria-label="Loading liquidity analysis" className="space-y-3">
      <Skeleton className="h-24" />
      <Skeleton className="h-52" />
    </div>
  ) : query.error ? (
    <ErrorPanel error={query.error} />
  ) : !query.data || query.data.status === "not_calculated" ? (
    <Alert title="Liquidity analysis not available for this run">
      <span>
        Liquidity analysis not available for this run — rerun to generate it.
      </span>{" "}
      <a
        className="font-medium text-[rgb(var(--primary))] underline"
        href={`/cases/${caseId}?tab=calculations#calculation-run-${selectedRun.data.id}-forecast-period-1`}
      >
        Open Forecast to rerun
      </a>
      .
    </Alert>
  ) : (
    <LiquidityAnalysis
      tenant={tenant}
      caseId={caseId}
      scenarioName={selectedScenario.name}
      summary={query.data}
    />
  );

  return (
    <div className="space-y-4">
      <Panel>
        <PanelHeader
          title="Liquidity analysis context"
          meta="Choose the scenario and successful forecast run to review"
        />
        <div className="grid gap-3 p-3 sm:grid-cols-2">
          <div>
            <Label>Scenario</Label>
            <Select
              ariaLabel="Liquidity scenario"
              value={selectedScenario.id}
              onValueChange={selectScenario}
              placeholder="Choose a scenario"
            >
              {availableScenarios.map((scenario) => (
                <SelectItem key={scenario.id} value={scenario.id}>
                  {scenario.name}
                </SelectItem>
              ))}
            </Select>
          </div>
          <div>
            <Label>Run</Label>
            {selectedRunId &&
            selectableRuns.some((run) => run.id === selectedRunId) ? (
              <Select
                ariaLabel="Liquidity forecast run"
                value={selectedRunId}
                onValueChange={setRunId}
                placeholder="Choose a successful run"
              >
                {selectableRuns.map((run) => (
                  <SelectItem key={run.id} value={run.id}>
                    {truncateId(run.id)} · {run.createdAt.toLocaleString()}
                  </SelectItem>
                ))}
              </Select>
            ) : selectedRunId ? (
              <span className="block h-8 pt-1.5 text-sm text-[rgb(var(--muted-foreground))]">
                Loading successful run…
              </span>
            ) : (
              <span className="block h-8 pt-1.5 text-sm text-[rgb(var(--muted-foreground))]">
                No successful runs
              </span>
            )}
            {runs.data && (runs.data.offset > 0 || runs.data.hasMore) ? (
              <div className="mt-2 flex justify-between gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={runs.data.offset === 0}
                  onClick={() =>
                    setRunOffset((current) => Math.max(0, current - 25))
                  }
                >
                  Previous
                </Button>
                <span className="self-center text-xs text-[rgb(var(--muted-foreground))]">
                  {runs.data.total} runs
                </span>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!runs.data.hasMore}
                  onClick={() => setRunOffset((current) => current + 25)}
                >
                  Next
                </Button>
              </div>
            ) : null}
          </div>
        </div>
      </Panel>
      {analysis}
    </div>
  );
}

function LiquidityAnalysis({
  tenant,
  caseId,
  scenarioName,
  summary,
}: {
  tenant: TenantHeaders;
  caseId: string;
  scenarioName: string;
  summary: LiquiditySummaryRead;
}) {
  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">Liquidity risk summary</h2>
          <p className="mt-1 text-xs text-[rgb(var(--muted-foreground))]">
            {scenarioName} · run {truncateId(summary.calculationRunId ?? "")} ·
            forecast input {summary.calculationInputHash?.slice(0, 12)} · as of{" "}
            {formatDate(summary.asOfDate)}
          </p>
        </div>
        <span className="text-xs text-[rgb(var(--muted-foreground))]">
          {summary.findings.length} findings
        </span>
      </header>
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
        {summary.metrics.map((metric) => (
          <MetricCard key={metric.key} metric={metric} />
        ))}
      </div>
      <section
        aria-labelledby="liquidity-findings-heading"
        className="space-y-2"
      >
        <Label id="liquidity-findings-heading">Liquidity findings</Label>
        {summary.findings.length ? (
          summary.findings.map((finding) => (
            <LiquidityFindingCard
              key={finding.id}
              tenant={tenant}
              caseId={caseId}
              finding={finding}
            />
          ))
        ) : (
          <Alert title="No liquidity concerns">
            The selected forecast did not cross an MVP liquidity risk threshold.
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
          <div className="font-medium">Terminal finding · read only</div>
          Review note: {finding.dispositionReason}
        </div>
      ) : resolved ? (
        <div className="border-t border-[rgb(var(--border))] pt-2 font-medium text-[rgb(var(--muted-foreground))]">
          Terminal finding · read only
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
  return formatMoney(metric.value, metric.unit);
}

function formatDate(value: string | null | undefined) {
  return value
    ? new Date(`${value}T00:00:00`).toLocaleDateString()
    : "not available";
}
