import type {
  CalculationRunRead,
  CalculationRunSummaryRead,
  CapitalComparisonRead,
  CapitalProjectionRead,
} from "@aequoros/risk-service-api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import {
  Alert,
  Badge,
  Button,
  Label,
  Panel,
  PanelHeader,
  Select,
  SelectItem,
  Skeleton,
} from "../../components/ui";
import { riskApi, type TenantHeaders } from "../../lib/api";
import { formatJson, labelize, truncateId } from "../../lib/utils";
import { ErrorPanel } from "../../shared/route-ui";
import { FindingReviewItem } from "../findings/findings-tab";

type Projection = CapitalProjectionRead | CapitalComparisonRead["baseline"];

const pressureTone = {
  low: "success",
  medium: "warning",
  high: "danger",
  critical: "danger",
} as const;

export function CapitalTab({
  tenant,
  caseId,
  mutationDisabled = false,
}: {
  tenant: TenantHeaders;
  caseId: string;
  mutationDisabled?: boolean;
}) {
  const queryClient = useQueryClient();
  const [runId, setRunId] = useState("");
  const [attemptOffset, setAttemptOffset] = useState(0);
  const [projectionId, setProjectionId] = useState("");
  const scenarios = useQuery({
    queryKey: ["scenarios", tenant, caseId, "capital"],
    queryFn: () => riskApi.scenarios(tenant, caseId, true),
  });
  const runs = useQuery({
    queryKey: ["calculation-runs", tenant, caseId, "capital"],
    queryFn: () => riskApi.calculationRuns(tenant, caseId, undefined, 100, 0),
  });
  const latestSuccessfulRunId = runs.data?.latestSuccessfulRunId;
  const latestRunMissing = Boolean(
    latestSuccessfulRunId &&
    !runs.data?.runs.some((run) => run.id === latestSuccessfulRunId),
  );
  const latestRun = useQuery({
    queryKey: [
      "calculation-run",
      tenant,
      caseId,
      "capital-latest-successful",
      latestSuccessfulRunId,
    ],
    queryFn: () =>
      riskApi.calculationRun(tenant, caseId, latestSuccessfulRunId ?? ""),
    enabled: latestRunMissing,
  });
  const attempts = useQuery({
    queryKey: ["capital-projections", tenant, caseId, attemptOffset],
    queryFn: () =>
      riskApi.capitalProjections(tenant, caseId, 25, attemptOffset),
  });
  const summary = useQuery({
    queryKey: ["capital-summary", tenant, caseId],
    queryFn: () => riskApi.capitalSummary(tenant, caseId),
  });
  const comparison = useQuery({
    queryKey: ["capital-comparison", tenant, caseId],
    queryFn: () => riskApi.capitalComparison(tenant, caseId),
  });
  const successfulRuns = useMemo(() => {
    const page =
      runs.data?.runs.filter((run) => run.status === "succeeded") ?? [];
    if (
      latestRun.data?.status === "succeeded" &&
      !page.some((run) => run.id === latestRun.data?.id)
    ) {
      return [latestRun.data, ...page];
    }
    return page;
  }, [latestRun.data, runs.data]);
  const scenariosById = useMemo(
    () =>
      new Map(
        scenarios.data?.scenarios.map((scenario) => [scenario.id, scenario]) ??
          [],
      ),
    [scenarios.data],
  );

  useEffect(() => {
    setAttemptOffset(0);
    setProjectionId("");
  }, [caseId, tenant.orgId]);

  useEffect(() => {
    setProjectionId((current) =>
      attempts.data?.projections.some((projection) => projection.id === current)
        ? current
        : (attempts.data?.projections[0]?.id ?? ""),
    );
  }, [attempts.data]);

  useEffect(() => {
    setRunId((current) =>
      successfulRuns.some((run) => run.id === current)
        ? current
        : (successfulRuns[0]?.id ?? ""),
    );
  }, [successfulRuns]);

  const create = useMutation({
    mutationFn: () =>
      riskApi.createCapitalProjection(tenant, caseId, {
        calculationRunId: runId,
      }),
    onSuccess: async (projection) => {
      setAttemptOffset(0);
      setProjectionId(projection.id);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["capital-projections"] }),
        queryClient.invalidateQueries({ queryKey: ["capital-summary"] }),
        queryClient.invalidateQueries({ queryKey: ["capital-comparison"] }),
        queryClient.invalidateQueries({ queryKey: ["findings"] }),
      ]);
      if (projection.status === "failed") {
        toast.error(projection.error?.message ?? "Capital projection failed");
      } else {
        toast.success("Capital projection generated");
      }
    },
  });

  if (
    scenarios.isLoading ||
    runs.isLoading ||
    latestRun.isLoading ||
    attempts.isLoading ||
    summary.isLoading ||
    comparison.isLoading
  ) {
    return <Skeleton className="h-96" />;
  }
  if (scenarios.isError) return <ErrorPanel error={scenarios.error} />;
  if (runs.isError) return <ErrorPanel error={runs.error} />;
  if (latestRun.isError) return <ErrorPanel error={latestRun.error} />;
  if (attempts.isError) return <ErrorPanel error={attempts.error} />;
  if (summary.isError) return <ErrorPanel error={summary.error} />;
  if (comparison.isError) return <ErrorPanel error={comparison.error} />;

  const projection =
    attempts.data?.projections.find((attempt) => attempt.id === projectionId) ??
    (create.data?.id === projectionId ? create.data : undefined) ??
    (summary.data?.projection as Projection | null | undefined);

  return (
    <div className="space-y-3">
      <Panel>
        <PanelHeader
          title="Capital projection"
          meta="Equity buffer and balance-sheet pressure from immutable forecast outputs"
          actions={
            create.isPending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : null
          }
        />
        <div className="grid gap-3 p-3 md:grid-cols-[minmax(0,1fr)_auto] md:items-end">
          <div>
            <Label>Successful forecast run</Label>
            <Select
              ariaLabel="Capital forecast run"
              value={runId}
              onValueChange={setRunId}
              placeholder="Choose a forecast run"
            >
              {successfulRuns.map((run) => (
                <SelectItem key={run.id} value={run.id}>
                  {runLabel(run, scenariosById.get(run.scenarioId))}
                </SelectItem>
              ))}
            </Select>
          </div>
          <Button
            disabled={mutationDisabled || !runId || create.isPending}
            onClick={() => create.mutate()}
          >
            {create.isPending ? "Generating…" : "Generate projection"}
          </Button>
        </div>
        {mutationDisabled ? (
          <div className="px-3 pb-3">
            <Alert title="Mutation unavailable in demo mode">
              Switch to live API data to generate a capital projection.
            </Alert>
          </div>
        ) : !successfulRuns.length ? (
          <div className="px-3 pb-3">
            <Alert title="No successful forecast runs">
              Complete a baseline or downside forecast before projecting
              capital.
            </Alert>
          </div>
        ) : null}
        {create.isError ? (
          <div className="px-3 pb-3">
            <ErrorPanel error={create.error} />
          </div>
        ) : null}
      </Panel>

      {attempts.data?.projections.length ? (
        <Panel>
          <PanelHeader
            title="Projection attempt history"
            meta={`${attempts.data.total} immutable attempt${attempts.data.total === 1 ? "" : "s"}`}
          />
          <div className="grid gap-3 p-3 md:grid-cols-[minmax(0,1fr)_auto] md:items-end">
            <div>
              <Label>Projection attempt</Label>
              <Select
                ariaLabel="Capital projection attempt"
                value={projectionId}
                onValueChange={setProjectionId}
                placeholder="Choose a projection attempt"
              >
                {attempts.data.projections.map((attempt) => (
                  <SelectItem key={attempt.id} value={attempt.id}>
                    {attemptLabel(
                      attempt,
                      scenariosById.get(attempt.scenarioId),
                    )}
                  </SelectItem>
                ))}
              </Select>
            </div>
            <div className="flex gap-2">
              <Button
                variant="outline"
                disabled={attemptOffset === 0}
                onClick={() =>
                  setAttemptOffset(Math.max(0, attemptOffset - 25))
                }
              >
                Newer
              </Button>
              <Button
                variant="outline"
                disabled={!attempts.data.hasMore}
                onClick={() => setAttemptOffset(attemptOffset + 25)}
              >
                Older
              </Button>
            </div>
          </div>
        </Panel>
      ) : null}

      {!projection ? (
        <Alert title="No capital projection">
          Generate the first projection from a successful forecast run. Results,
          findings, and their evidence will appear here.
        </Alert>
      ) : projection.status === "failed" ? (
        <Alert
          title={projection.error?.message ?? "Capital projection failed"}
          tone="danger"
        >
          {projection.error ? formatJson(projection.error.details) : null}
        </Alert>
      ) : (
        <>
          <ProjectionSummary projection={projection} />
          <ScenarioComparison comparison={comparison.data} />
          <CapitalFindings
            projection={projection}
            tenant={tenant}
            mutationDisabled={mutationDisabled}
          />
        </>
      )}
    </div>
  );
}

function ProjectionSummary({ projection }: { projection: Projection }) {
  return (
    <Panel>
      <PanelHeader
        title="Projected capital indicators"
        meta={`Run ${truncateId(projection.calculationRunId)} · ${projection.engineVersion}`}
      />
      <div className="overflow-x-auto">
        <table className="w-full min-w-[680px] text-left text-xs">
          <thead className="border-b border-[rgb(var(--border))] text-[rgb(var(--muted-foreground))]">
            <tr>
              <th className="p-3">Period</th>
              <th className="p-3">Equity</th>
              <th className="p-3">Equity / assets</th>
              <th className="p-3">Liabilities / assets</th>
              <th className="p-3">Equity change</th>
              <th className="p-3">Pressure</th>
            </tr>
          </thead>
          <tbody>
            {projection.indicators.map((indicator) => (
              <tr
                key={indicator.id}
                className="border-b border-[rgb(var(--border))] last:border-0"
              >
                <td className="p-3">{indicator.periodNumber}</td>
                <td className="p-3 font-mono">{money(indicator.equity)}</td>
                <td className="p-3">
                  {percent(indicator.equityToAssetsRatio)}
                </td>
                <td className="p-3">
                  {percent(indicator.liabilitiesToAssetsRatio)}
                </td>
                <td className="p-3 font-mono">
                  {money(indicator.equityChange)}
                </td>
                <td className="p-3">
                  <Badge tone={pressureTone[indicator.pressureLevel]}>
                    {labelize(indicator.pressureLevel)}
                  </Badge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function ScenarioComparison({
  comparison,
}: {
  comparison?: CapitalComparisonRead;
}) {
  const baseline = comparison?.baseline as Projection | null | undefined;
  const downside = comparison?.downside as Projection | null | undefined;
  return (
    <Panel>
      <PanelHeader
        title="Baseline vs downside"
        meta="Latest successful projections"
      />
      {!baseline || !downside ? (
        <div className="p-3">
          <Alert title="Comparison not ready">
            Generate successful projections for both baseline and downside
            scenarios.
          </Alert>
        </div>
      ) : (
        <div className="grid gap-2 p-3 md:grid-cols-2">
          {comparison?.periods.map((period) => (
            <div
              key={period.periodNumber}
              className="rounded-md border border-[rgb(var(--border))] p-3 text-xs"
            >
              <div className="font-medium">Period {period.periodNumber}</div>
              <div className="mt-2 grid grid-cols-2 gap-2">
                <span>Baseline equity</span>
                <span className="text-right font-mono">
                  {money(period.baselineEquity)}
                </span>
                <span>Downside equity</span>
                <span className="text-right font-mono">
                  {money(period.downsideEquity)}
                </span>
                <span>Downside delta</span>
                <span className="text-right font-mono">
                  {money(period.equityDelta)}
                </span>
                <span>Ratio delta</span>
                <span className="text-right">
                  {percent(period.equityToAssetsRatioDelta)}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function CapitalFindings({
  projection,
  tenant,
  mutationDisabled,
}: {
  projection: Projection;
  tenant: TenantHeaders;
  mutationDisabled: boolean;
}) {
  const queryClient = useQueryClient();
  return (
    <Panel>
      <PanelHeader
        title="Capital findings and evidence"
        meta={`${projection.findings.length} generated`}
      />
      <div className="space-y-3 p-3">
        {!projection.findings.length ? (
          <Alert title="No capital pressure findings">
            The projected indicators did not cross the MVP capital thresholds.
          </Alert>
        ) : null}
        {projection.findings.map(({ finding, evidence }) => (
          <div key={finding.id} className="space-y-2">
            <FindingReviewItem
              finding={finding}
              tenant={tenant}
              disabled={mutationDisabled}
              onUpdated={() => {
                void queryClient.invalidateQueries({
                  queryKey: ["capital-summary"],
                });
                void queryClient.invalidateQueries({
                  queryKey: ["capital-comparison"],
                });
              }}
            />
            <div className="rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--surface-2))] p-3 text-xs">
              <div className="font-medium">Evidence</div>
              {evidence.map((item) => (
                <div key={item.id} className="mt-2 grid gap-1">
                  <div>{item.quote ?? "Calculation evidence"}</div>
                  <pre className="overflow-auto whitespace-pre-wrap text-[11px] text-[rgb(var(--muted-foreground))]">
                    {formatJson(item.locator)}
                  </pre>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function runLabel(
  run: Pick<
    CalculationRunSummaryRead | CalculationRunRead,
    "id" | "scenarioId" | "createdAt"
  >,
  scenario?: { name: string; scenarioType: string },
) {
  const scenarioLabel = scenario
    ? `${scenario.name} (${labelize(scenario.scenarioType)})`
    : `Unknown scenario ${truncateId(run.scenarioId)}`;
  return `${scenarioLabel} · ${run.createdAt.toLocaleString()} · ${truncateId(run.id)}`;
}

function attemptLabel(
  attempt: CapitalProjectionRead,
  scenario?: { name: string; scenarioType: string },
) {
  const scenarioLabel = scenario
    ? `${scenario.name} (${labelize(scenario.scenarioType)})`
    : `Unknown scenario ${truncateId(attempt.scenarioId)}`;
  return `${labelize(attempt.status)} · ${scenarioLabel} · ${attempt.createdAt.toLocaleString()}`;
}

function money(value: string | number) {
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Number(value));
}

function percent(value: string | number) {
  return new Intl.NumberFormat("en-US", {
    style: "percent",
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  }).format(Number(value));
}
