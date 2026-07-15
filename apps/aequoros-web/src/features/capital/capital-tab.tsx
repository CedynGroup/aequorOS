import type {
  CalculationRunRead,
  CalculationRunSummaryRead,
  CapitalComparisonBasisRead,
  CapitalComparisonRead,
  CapitalProjectionRead,
  CapitalProjectionSummaryRead,
} from "@aequoros/risk-service-api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { lazy, useEffect, useMemo, useRef, useState } from "react";
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
import { formatPercent } from "../../lib/money";
import { formatJson, formatMoney, labelize } from "../../lib/utils";
import { ErrorPanel } from "../../shared/route-ui";
import { capitalComparisonToSeries } from "../charts/analysis-chart-adapters";
import { ChartBoundary } from "../charts/chart-shell";
import { FindingReviewItem } from "../findings/findings-tab";

const CapitalComparisonChart = lazy(() =>
  import("../charts/analysis-charts").then((module) => ({
    default: module.CapitalComparisonChart,
  })),
);

type Projection = CapitalProjectionRead;

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
  mutationDisabledReason = "demo",
}: {
  tenant: TenantHeaders;
  caseId: string;
  mutationDisabled?: boolean;
  mutationDisabledReason?: "demo" | "retired-case";
}) {
  const queryClient = useQueryClient();
  const [runId, setRunId] = useState("");
  const [attemptOffset, setAttemptOffset] = useState(0);
  const [projectionId, setProjectionId] = useState("");
  const createdProjectionId = useRef("");
  const scenarios = useQuery({
    queryKey: ["scenarios", tenant, caseId, "capital"],
    queryFn: () => riskApi.scenarios(tenant, caseId, true),
  });
  const attempts = useQuery({
    queryKey: ["capital-projections", tenant, caseId, attemptOffset],
    queryFn: () =>
      riskApi.capitalProjections(tenant, caseId, 25, attemptOffset),
  });
  const selectedAttempt = useQuery({
    queryKey: ["capital-projection", tenant, caseId, projectionId],
    queryFn: () => riskApi.capitalProjection(tenant, caseId, projectionId),
    enabled: Boolean(projectionId),
  });
  const summary = useQuery({
    queryKey: ["capital-summary", tenant, caseId],
    queryFn: () => riskApi.capitalSummary(tenant, caseId),
  });
  const comparison = useQuery({
    queryKey: ["capital-comparison", tenant, caseId],
    queryFn: () => riskApi.capitalComparison(tenant, caseId),
  });
  const scenariosById = useMemo(
    () =>
      new Map(
        scenarios.data?.scenarios.map((scenario) => [scenario.id, scenario]) ??
          [],
      ),
    [scenarios.data],
  );
  const runs = useQuery({
    queryKey: ["calculation-runs", tenant, caseId, "capital-active"],
    queryFn: async () => {
      const limit = 100;
      const firstPage = await riskApi.calculationRuns(
        tenant,
        caseId,
        undefined,
        limit,
        0,
        true,
      );
      const latestSuccessfulRunsByScenario = [
        ...firstPage.latestSuccessfulRunsByScenario,
      ];
      let page = firstPage;
      while (page.latestSuccessfulRunsByScenario.length === limit) {
        page = await riskApi.calculationRuns(
          tenant,
          caseId,
          undefined,
          limit,
          page.offset + limit,
          true,
        );
        latestSuccessfulRunsByScenario.push(
          ...page.latestSuccessfulRunsByScenario,
        );
      }
      return { ...firstPage, latestSuccessfulRunsByScenario };
    },
  });
  const successfulRuns = useMemo(() => {
    const candidates = [
      ...(runs.data?.runs ?? []),
      ...(runs.data?.latestSuccessfulRunsByScenario ?? []),
    ];
    return candidates
      .filter((run, index) => {
        const scenario = scenariosById.get(run.scenarioId);
        return (
          run.status === "succeeded" &&
          scenario?.archivedAt === null &&
          candidates.findIndex((candidate) => candidate.id === run.id) === index
        );
      })
      .sort(
        (left, right) => right.createdAt.getTime() - left.createdAt.getTime(),
      );
  }, [runs.data, scenariosById]);

  useEffect(() => {
    setAttemptOffset(0);
    setProjectionId("");
    createdProjectionId.current = "";
  }, [caseId, tenant.orgId]);

  useEffect(() => {
    setProjectionId((current) => {
      const currentIsAvailable = attempts.data?.projections.some(
        (projection) => projection.id === current,
      );
      if (
        currentIsAvailable ||
        (current !== "" && createdProjectionId.current === current)
      )
        return current;
      if (createdProjectionId.current !== "")
        return createdProjectionId.current;
      return attempts.data?.projections[0]?.id ?? "";
    });
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
      createdProjectionId.current = projection.id;
      queryClient.setQueryData(
        ["capital-projection", tenant, caseId, projection.id],
        projection,
      );
      setProjectionId(projection.id);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["capital-projections"] }),
        queryClient.invalidateQueries({ queryKey: ["capital-summary"] }),
        queryClient.invalidateQueries({ queryKey: ["capital-comparison"] }),
        queryClient.invalidateQueries({ queryKey: ["findings"] }),
        queryClient.invalidateQueries({ queryKey: ["capital-projection"] }),
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
    attempts.isLoading ||
    (projectionId && selectedAttempt.isLoading) ||
    summary.isLoading ||
    comparison.isLoading
  ) {
    return <Skeleton className="h-96" />;
  }
  if (scenarios.isError) return <ErrorPanel error={scenarios.error} />;
  if (runs.isError) return <ErrorPanel error={runs.error} />;
  if (attempts.isError) return <ErrorPanel error={attempts.error} />;
  if (selectedAttempt.isError)
    return <ErrorPanel error={selectedAttempt.error} />;
  if (summary.isError) return <ErrorPanel error={summary.error} />;

  const projection =
    selectedAttempt.data ??
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
        <div className="grid gap-3 p-3">
          <div className="min-w-0">
            <Label>Successful forecast run</Label>
            <Select
              ariaLabel="Capital forecast run"
              value={runId}
              onValueChange={setRunId}
              placeholder="Choose a forecast run"
              disabled={mutationDisabled}
              className="w-full min-w-0"
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
            {mutationDisabledReason === "retired-case" ? (
              <Alert title="Capital mutations unavailable for retired case">
                Historical capital projections remain available for review, but
                archived cases cannot generate projections or update findings.
              </Alert>
            ) : (
              <Alert title="Mutation unavailable in demo mode">
                Switch to live API data to generate a capital projection.
              </Alert>
            )}
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
          <div className="grid gap-3 p-3">
            <div className="min-w-0">
              <Label>Projection attempt</Label>
              <Select
                ariaLabel="Capital projection attempt"
                value={projectionId}
                onValueChange={(value) => {
                  createdProjectionId.current = "";
                  setProjectionId(value);
                }}
                placeholder="Choose a projection attempt"
                className="w-full min-w-0"
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
                onClick={() => {
                  createdProjectionId.current = "";
                  setAttemptOffset(Math.max(0, attemptOffset - 25));
                }}
              >
                Newer
              </Button>
              <Button
                variant="outline"
                disabled={!attempts.data.hasMore}
                onClick={() => {
                  createdProjectionId.current = "";
                  setAttemptOffset(attemptOffset + 25);
                }}
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
          <ScenarioComparison
            comparison={comparison.data}
            error={comparison.error}
          />
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
        meta={`Immutable forecast evidence · ${projection.engineVersion}`}
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
                <td className="p-3 font-mono">
                  {formatMoney(indicator.equity, projection.reportingCurrency)}
                </td>
                <td className="p-3">
                  {percent(indicator.equityToAssetsRatio)}
                </td>
                <td className="p-3">
                  {percent(indicator.liabilitiesToAssetsRatio)}
                </td>
                <td className="p-3 font-mono">
                  {formatMoney(
                    indicator.equityChange,
                    projection.reportingCurrency,
                  )}
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
  error,
}: {
  comparison?: CapitalComparisonRead;
  error: Error | null;
}) {
  const baseline = comparison?.baseline as Projection | null | undefined;
  const downside = comparison?.downside as Projection | null | undefined;
  const diagnostic = comparison?.diagnostic;
  return (
    <Panel>
      <PanelHeader
        title="Baseline vs downside"
        meta="Latest successful projections"
      />
      <div className="px-3 pt-1">
        <ChartBoundary
          title="Capital comparison"
          resetKey={`${comparison?.baseline?.id ?? "none"}:${comparison?.downside?.id ?? "none"}`}
        >
          <CapitalComparisonChart
            series={capitalComparisonToSeries(comparison)}
          />
        </ChartBoundary>
      </div>
      {error ? (
        <div className="p-3">
          <ErrorPanel error={error} />
        </div>
      ) : !baseline || !downside ? (
        <div className="p-3">
          <Alert title="Comparison not ready">
            Generate successful projections for both baseline and downside
            scenarios.
          </Alert>
        </div>
      ) : diagnostic ? (
        <div className="p-3">
          <Alert title="Comparison not ready" tone="danger">
            <div>{diagnostic.message}</div>
            <dl className="mt-2 grid grid-cols-[auto_1fr_1fr] gap-x-3 gap-y-1 text-xs">
              <dt className="font-medium">Basis</dt>
              <dd className="font-medium">Baseline</dd>
              <dd className="font-medium">Downside</dd>
              {diagnostic.differingAttributes.map((attribute) => (
                <ComparisonBasisDifference
                  key={attribute}
                  attribute={attribute}
                  baseline={diagnostic.baselineBasis}
                  downside={diagnostic.downsideBasis}
                />
              ))}
            </dl>
            <div className="mt-2">{diagnostic.correctiveAction}</div>
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
                  {formatMoney(
                    period.baselineEquity,
                    baseline.reportingCurrency,
                  )}
                </span>
                <span>Downside equity</span>
                <span className="text-right font-mono">
                  {formatMoney(
                    period.downsideEquity,
                    baseline.reportingCurrency,
                  )}
                </span>
                <span>Baseline equity / assets</span>
                <span className="text-right font-mono">
                  {percent(period.baselineEquityToAssetsRatio)}
                </span>
                <span>Downside equity / assets</span>
                <span className="text-right font-mono">
                  {percent(period.downsideEquityToAssetsRatio)}
                </span>
                <span>Downside delta</span>
                <span className="text-right font-mono">
                  {formatMoney(period.equityDelta, baseline.reportingCurrency)}
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

function ComparisonBasisDifference({
  attribute,
  baseline,
  downside,
}: {
  attribute: "as_of_date" | "reporting_currency" | "forecast_horizon";
  baseline: CapitalComparisonBasisRead;
  downside: CapitalComparisonBasisRead;
}) {
  const value = (basis: CapitalComparisonBasisRead) => {
    if (attribute === "as_of_date")
      return basis.asOfDate.toISOString().slice(0, 10);
    if (attribute === "reporting_currency") return basis.reportingCurrency;
    return `${basis.forecastHorizon} periods`;
  };
  return (
    <>
      <dt>{labelize(attribute)}</dt>
      <dd>{value(baseline)}</dd>
      <dd>{value(downside)}</dd>
    </>
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
            />
            <div className="rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--surface-2))] p-3 text-xs">
              <div className="font-medium">Evidence</div>
              {evidence.map((item) => (
                <div key={item.id} className="mt-2 grid gap-1">
                  <div>{item.quote ?? "Calculation evidence"}</div>
                  {typeof item.locator.source_url === "string" ? (
                    <a
                      className="text-[rgb(var(--primary))] underline"
                      href={item.locator.source_url}
                    >
                      {typeof item.locator.label === "string"
                        ? item.locator.label
                        : "Open forecast evidence"}
                    </a>
                  ) : (
                    <span className="text-[rgb(var(--muted-foreground))]">
                      Linked to immutable forecast evidence
                    </span>
                  )}
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
    : "Unlabelled scenario";
  return `${scenarioLabel} · forecast from ${run.createdAt.toLocaleString()}`;
}

function attemptLabel(
  attempt: CapitalProjectionSummaryRead,
  scenario?: { name: string; scenarioType: string },
) {
  const scenarioLabel = scenario
    ? `${scenario.name} (${labelize(scenario.scenarioType)})`
    : "Unlabelled scenario";
  return `${labelize(attempt.status)} · ${scenarioLabel} · ${attempt.createdAt.toLocaleString()}`;
}

function percent(value: string | number) {
  return formatPercent(value);
}
