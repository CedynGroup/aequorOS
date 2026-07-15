import type {
  RegulatoryRunRead,
  RegulatoryRunSummaryRead,
} from "@aequoros/risk-service-api";
import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Loader2, PlayCircle } from "lucide-react";
import type { ReactNode } from "react";
import { toast } from "sonner";

import {
  Alert,
  Badge,
  Button,
  Panel,
  PanelHeader,
  Skeleton,
} from "../../components/ui";
import { isApiError, riskApi } from "../../lib/api";
import { labelize } from "../../lib/utils";
import { ErrorPanel } from "../../shared/route-ui";
import type { AlmTabProps } from "./alm-console";
import { DenseTable, NumCell, Td, Th } from "./shared/dense-table";
import { formatPct, statusTone } from "./shared/format";
import { metricString } from "./shared/regulatory";
import { RunBadge } from "./shared/run-badge";
import { TrendChart, type TrendReferenceLine } from "./shared/trend-chart";

const capitalScenarios = ["baseline", "mild", "moderate", "severe"] as const;
const stressScenarios = ["mild", "moderate", "severe"] as const;

type StressPathPoint = { quarter: number; car: string };
type StressTrigger = {
  code: string;
  thresholdPct: string;
  fired: boolean;
  firstQuarter: number | null;
  action: string;
};

function stressPath(run: RegulatoryRunRead): StressPathPoint[] {
  const path = (run.metrics as { stress_path?: unknown }).stress_path;
  if (!Array.isArray(path)) return [];
  return path.map((row: { quarter: number; car: string }) => ({
    quarter: Number(row.quarter),
    car: String(row.car),
  }));
}

function stressTriggers(run: RegulatoryRunRead): StressTrigger[] {
  const triggers = (run.metrics as { triggers?: unknown }).triggers;
  if (!Array.isArray(triggers)) return [];
  return triggers.map(
    (row: {
      code: string;
      threshold_pct: string;
      fired: boolean;
      first_quarter: number | null;
      action: string;
    }) => ({
      code: String(row.code),
      thresholdPct: String(row.threshold_pct),
      fired: Boolean(row.fired),
      firstQuarter: row.first_quarter === null ? null : Number(row.first_quarter),
      action: String(row.action),
    }),
  );
}

function endStateBadge(triggers: StressTrigger[]): {
  tone: "success" | "warning" | "danger";
  label: string;
} {
  const fired = triggers.filter((trigger) => trigger.fired);
  if (fired.some((trigger) => trigger.code === "critical")) {
    return { tone: "danger", label: "Critical" };
  }
  if (fired.some((trigger) => trigger.code === "breach")) {
    return { tone: "danger", label: "Breach" };
  }
  if (fired.length) {
    return { tone: "warning", label: "Early warning" };
  }
  return { tone: "success", label: "No trigger" };
}

function triggerReferenceLines(triggers: StressTrigger[]): TrendReferenceLine[] {
  return triggers.map((trigger) => ({
    value: Number(trigger.thresholdPct),
    label: labelize(trigger.code),
    tone: trigger.code === "early_warning" ? "warning" : "danger",
  }));
}

export function CapitalStressTab({ tenant, bank, period }: AlmTabProps) {
  const queryClient = useQueryClient();
  const runsQuery = useQuery({
    queryKey: ["alm-regulatory-runs", tenant, bank.id, "capital", period.id],
    queryFn: () =>
      riskApi.listRegulatoryRuns(tenant, bank.id, {
        module: "capital",
        reportingPeriodId: period.id,
        limit: 100,
      }),
  });
  const latestByScenario = new Map<string, RegulatoryRunSummaryRead>();
  for (const run of runsQuery.data?.runs ?? []) {
    if (!latestByScenario.has(run.scenarioCode)) {
      latestByScenario.set(run.scenarioCode, run);
    }
  }
  const runQueries = useQueries({
    queries: capitalScenarios.map((scenario) => {
      const runId = latestByScenario.get(scenario)?.id;
      return {
        queryKey: ["alm-regulatory-run", tenant, bank.id, runId],
        queryFn: () => riskApi.getRegulatoryRun(tenant, bank.id, runId ?? ""),
        enabled: Boolean(runId),
      };
    }),
  });
  const runAll = useMutation({
    mutationFn: () =>
      riskApi.runAllCapitalScenarios(tenant, bank.id, {
        reportingPeriodId: period.id,
      }),
    onSuccess: (batch) => {
      batch.runs.forEach((run) => {
        const label = labelize(run.scenarioCode);
        if (run.status === "succeeded") {
          toast.success(`${label} capital scenario succeeded`);
        } else {
          toast.error(
            `${label} capital scenario ${run.status}${
              run.error ? `: ${run.error.message}` : ""
            }`,
          );
        }
      });
      [
        "alm-regulatory-runs",
        "alm-regulatory-run",
        "alm-capital-dashboard",
        "alm-rwa-breakdown",
        "alm-capital-structure",
        "alm-bsd2",
      ].forEach((prefix) => {
        void queryClient.invalidateQueries({ queryKey: [prefix] });
      });
    },
    onError: (error) => {
      toast.error(
        isApiError(error)
          ? error.message
          : "Capital stress scenarios could not be started.",
      );
    },
  });

  if (runsQuery.isLoading) {
    return (
      <div aria-label="Loading capital stress scenarios" className="space-y-3">
        <Skeleton className="h-24" />
        <Skeleton className="h-64" />
      </div>
    );
  }
  if (runsQuery.error) return <ErrorPanel error={runsQuery.error} />;

  const runsByScenario = new Map<string, RegulatoryRunRead>();
  runQueries.forEach((query, index) => {
    if (query.data) runsByScenario.set(capitalScenarios[index], query.data);
  });

  return (
    <div className="space-y-3">
      <Panel>
        <PanelHeader
          title="Capital stress scenarios"
          meta="Quarterly 12-month CAR paths with Bank of Ghana action triggers"
          actions={
            <Button
              size="sm"
              disabled={runAll.isPending}
              onClick={() => runAll.mutate()}
            >
              {runAll.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <PlayCircle className="size-3.5" />
              )}
              Run all scenarios
            </Button>
          }
        />
        <div className="px-3 py-2 text-xs text-[rgb(var(--muted-foreground))]">
          Each scenario projects the capital position quarterly over twelve
          months and evaluates early warning, breach, and critical action
          triggers for {period.label}.
        </div>
      </Panel>
      {!latestByScenario.size ? (
        <Alert title="No capital stress runs for this period">
          Run all scenarios to calculate baseline, mild, moderate, and severe
          capital stress results for {period.label}.
        </Alert>
      ) : (
        <>
          <BaselineCard
            summary={latestByScenario.get("baseline")}
            query={runQueries[0]}
          />
          <div className="grid gap-3 xl:grid-cols-3">
            {stressScenarios.map((scenario, index) => (
              <StressScenarioCard
                key={scenario}
                scenario={scenario}
                summary={latestByScenario.get(scenario)}
                query={runQueries[index + 1]}
              />
            ))}
          </div>
          <Panel>
            <PanelHeader
              title="Scenario comparison"
              meta="End-state CAR after four stressed quarters"
            />
            <div className="p-3">
              <ComparisonTable runs={runsByScenario} />
            </div>
          </Panel>
        </>
      )}
    </div>
  );
}

type ScenarioQuery = {
  data?: RegulatoryRunRead;
  isLoading: boolean;
  error: unknown;
};

function ScenarioFrame({
  title,
  query,
  summary,
  children,
}: {
  title: string;
  query: ScenarioQuery;
  summary?: RegulatoryRunSummaryRead;
  children: (run: RegulatoryRunRead) => ReactNode;
}) {
  if (!summary) {
    return (
      <Panel>
        <PanelHeader title={title} />
        <div className="p-3 text-xs text-[rgb(var(--muted-foreground))]">
          Not yet run for this period.
        </div>
      </Panel>
    );
  }
  if (query.isLoading) {
    return (
      <Panel>
        <PanelHeader title={title} />
        <div aria-label={`Loading ${title} scenario`} className="p-3">
          <Skeleton className="h-44" />
        </div>
      </Panel>
    );
  }
  if (query.error) {
    return (
      <Panel>
        <PanelHeader title={title} />
        <div className="p-3">
          <ErrorPanel error={query.error} />
        </div>
      </Panel>
    );
  }
  const run = query.data;
  if (!run) return null;
  if (run.status !== "succeeded") {
    return (
      <Panel>
        <PanelHeader title={title} actions={<RunBadge run={run} />} />
        <div className="p-3">
          <Alert title={`Run ${run.status}`} tone="danger">
            {run.error?.message ?? "The scenario run did not succeed."}
          </Alert>
        </div>
      </Panel>
    );
  }
  return <>{children(run)}</>;
}

function BaselineCard({
  summary,
  query,
}: {
  summary?: RegulatoryRunSummaryRead;
  query: ScenarioQuery;
}) {
  return (
    <ScenarioFrame title="Baseline" query={query} summary={summary}>
      {(run) => (
        <Panel>
          <PanelHeader
            title="Baseline"
            meta="As-of capital ratios without stress"
            actions={<RunBadge run={run} />}
          />
          <div className="grid gap-2 p-3 sm:grid-cols-2 xl:grid-cols-4">
            {["car_pct", "tier1_ratio_pct", "cet1_ratio_pct", "leverage_ratio_pct"].map(
              (code) => {
                const metric = run.metricResults.find(
                  (result) => result.metricCode === code,
                );
                if (!metric) return null;
                return (
                  <div
                    key={code}
                    className="min-w-0 rounded-md border border-[rgb(var(--border))] p-3"
                  >
                    <div className="flex items-center justify-between gap-2 text-xs text-[rgb(var(--muted-foreground))]">
                      {baselineMetricLabels[code]}
                      <Badge tone={statusTone(metric.status)}>
                        {labelize(metric.status)}
                      </Badge>
                    </div>
                    <div className="mt-1 font-mono text-xl font-semibold tabular-nums">
                      {formatPct(metric.metricValue)}
                    </div>
                    {metric.thresholdMin !== null ? (
                      <div className="mt-0.5 font-mono text-[11px] tabular-nums text-[rgb(var(--muted-foreground))]">
                        Min {formatPct(metric.thresholdMin)}
                      </div>
                    ) : null}
                  </div>
                );
              },
            )}
          </div>
        </Panel>
      )}
    </ScenarioFrame>
  );
}

const baselineMetricLabels: Record<string, string> = {
  car_pct: "CAR",
  tier1_ratio_pct: "Tier 1",
  cet1_ratio_pct: "CET1",
  leverage_ratio_pct: "Leverage",
};

function StressScenarioCard({
  scenario,
  summary,
  query,
}: {
  scenario: string;
  summary?: RegulatoryRunSummaryRead;
  query: ScenarioQuery;
}) {
  const title = labelize(scenario);
  return (
    <ScenarioFrame title={title} query={query} summary={summary}>
      {(run) => {
        const path = stressPath(run);
        const triggers = stressTriggers(run);
        const endState = path[path.length - 1];
        const badge = endStateBadge(triggers);
        return (
          <Panel>
            <PanelHeader
              title={title}
              actions={<Badge tone={badge.tone}>{badge.label}</Badge>}
            />
            <div className="space-y-3 p-3">
              <div>
                <div className="text-xs text-[rgb(var(--muted-foreground))]">
                  CAR after Q{endState ? endState.quarter : "4"}
                </div>
                <div className="font-mono text-2xl font-semibold tabular-nums">
                  {formatPct(endState?.car ?? null)}
                </div>
              </div>
              <TrendChart
                seriesLabel="CAR"
                height={150}
                points={path.map((point) => ({
                  label: `Q${point.quarter}`,
                  value: point.car,
                  stored: true,
                }))}
                referenceLines={triggerReferenceLines(triggers)}
              />
              <div>
                <div className="mb-1 text-[11px] font-semibold uppercase tracking-[0.04em] text-[rgb(var(--muted-foreground))]">
                  Action triggers
                </div>
                <ul className="space-y-1.5">
                  {triggers.map((trigger) => (
                    <li key={trigger.code} className="text-xs">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <Badge tone={trigger.fired ? (trigger.code === "early_warning" ? "warning" : "danger") : "success"}>
                          {trigger.fired
                            ? `Fired Q${trigger.firstQuarter ?? "?"}`
                            : "Not fired"}
                        </Badge>
                        <span className="font-medium">{labelize(trigger.code)}</span>
                        <span className="font-mono text-[11px] tabular-nums text-[rgb(var(--muted-foreground))]">
                          {formatPct(trigger.thresholdPct)} CAR
                        </span>
                      </div>
                      <div className="mt-0.5 text-[11px] text-[rgb(var(--muted-foreground))]">
                        {trigger.action}
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
              <RunBadge run={run} />
            </div>
          </Panel>
        );
      }}
    </ScenarioFrame>
  );
}

function ComparisonTable({ runs }: { runs: Map<string, RegulatoryRunRead> }) {
  return (
    <DenseTable ariaLabel="Capital scenario comparison">
      <thead>
        <tr>
          <Th>Metric</Th>
          <Th align="right">Baseline (as-of)</Th>
          {stressScenarios.map((scenario) => (
            <Th key={scenario} align="right">
              {labelize(scenario)}
            </Th>
          ))}
        </tr>
      </thead>
      <tbody>
        <tr>
          <Td>Q4 CAR</Td>
          <NumCell
            value={formatPct(metricString(runs.get("baseline")?.metrics, "car_pct"))}
            emphasis
          />
          {stressScenarios.map((scenario) => {
            const run = runs.get(scenario);
            const path = run && run.status === "succeeded" ? stressPath(run) : [];
            const endState = path[path.length - 1];
            return (
              <NumCell
                key={scenario}
                value={formatPct(endState?.car ?? null)}
                emphasis
              />
            );
          })}
        </tr>
      </tbody>
    </DenseTable>
  );
}
