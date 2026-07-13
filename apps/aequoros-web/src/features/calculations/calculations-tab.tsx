import type {
  CalculationRunRead,
  CalculationStatus,
  ForecastPeriodRead,
} from "@aequoros/risk-service-api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
  Alert,
  Badge,
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
import { truncateId } from "../../lib/utils";
import { ErrorPanel } from "../../shared/route-ui";

const runTone: Record<
  CalculationStatus,
  "neutral" | "success" | "warning" | "danger"
> = {
  queued: "neutral",
  running: "warning",
  succeeded: "success",
  failed: "danger",
};

export function CalculationsTab({
  tenant,
  caseId,
}: {
  tenant: TenantHeaders;
  caseId: string;
}) {
  const queryClient = useQueryClient();
  const runsKey = ["calculation-runs", tenant, caseId] as const;
  const scenarios = useQuery({
    queryKey: ["scenarios", tenant, caseId],
    queryFn: () => riskApi.scenarios(tenant, caseId),
  });
  const runs = useQuery({
    queryKey: runsKey,
    queryFn: () => riskApi.calculationRuns(tenant, caseId),
    refetchInterval: (query) =>
      query.state.data?.runs.some((run) =>
        (["queued", "running"] as CalculationStatus[]).includes(run.status),
      )
        ? 1000
        : false,
  });
  const [scenarioId, setScenarioId] = useState("");
  const [forecastPeriods, setForecastPeriods] = useState("3");
  const [selectedRunId, setSelectedRunId] = useState("");

  useEffect(() => {
    if (!scenarioId && scenarios.data?.scenarios[0]) {
      setScenarioId(scenarios.data.scenarios[0].id);
    }
  }, [scenarioId, scenarios.data]);

  useEffect(() => {
    if (!selectedRunId && runs.data?.runs[0]) {
      setSelectedRunId(runs.data.runs[0].id);
    }
  }, [runs.data, selectedRunId]);

  const refreshRuns = async (run: CalculationRunRead, message: string) => {
    setSelectedRunId(run.id);
    await queryClient.invalidateQueries({ queryKey: runsKey });
    if (run.status === "failed") toast.error(run.error?.message ?? message);
    else toast.success(message);
  };
  const start = useMutation({
    mutationFn: () =>
      riskApi.startCalculation(tenant, caseId, {
        scenarioId,
        forecastPeriods: Number(forecastPeriods),
      }),
    onSuccess: (run) => refreshRuns(run, "Forecast completed"),
  });
  const rerun = useMutation({
    mutationFn: (runId: string) =>
      riskApi.rerunCalculation(tenant, caseId, runId),
    onSuccess: (run) => refreshRuns(run, "Forecast rerun completed"),
  });

  if (scenarios.isLoading || runs.isLoading)
    return <Skeleton className="h-96" />;
  if (scenarios.isError) return <ErrorPanel error={scenarios.error} />;
  if (runs.isError) return <ErrorPanel error={runs.error} />;

  const availableScenarios = scenarios.data?.scenarios ?? [];
  if (!availableScenarios.length) {
    return (
      <Alert title="No scenarios available">
        Initialize and review scenario assumptions before running a forecast.
      </Alert>
    );
  }
  const selectedRun =
    runs.data?.runs.find((run) => run.id === selectedRunId) ??
    runs.data?.runs[0];
  const latestSuccessful = runs.data?.runs.find(
    (run) => run.id === runs.data?.latestSuccessfulRunId,
  );
  const isSubmitting = start.isPending || rerun.isPending;

  return (
    <div className="@container/calculations space-y-3">
      <Panel>
        <PanelHeader
          title="Balance-sheet forecast"
          meta="Deterministic annual projection using canonical balances and reviewed assumptions"
          actions={
            isSubmitting ? <Loader2 className="size-4 animate-spin" /> : null
          }
        />
        <div className="grid gap-3 p-3 md:grid-cols-[minmax(0,1fr)_140px_auto] md:items-end">
          <div>
            <Label>Scenario</Label>
            <Select
              ariaLabel="Forecast scenario"
              value={scenarioId}
              onValueChange={setScenarioId}
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
            <Label>Annual periods</Label>
            <Input
              aria-label="Forecast periods"
              type="number"
              min={1}
              max={12}
              value={forecastPeriods}
              onChange={(event) => setForecastPeriods(event.target.value)}
            />
          </div>
          <Button
            disabled={
              isSubmitting ||
              !scenarioId ||
              Number(forecastPeriods) < 1 ||
              Number(forecastPeriods) > 12
            }
            onClick={() => start.mutate()}
          >
            {start.isPending ? "Starting…" : "Run forecast"}
          </Button>
        </div>
        {start.isError ? (
          <div className="px-3 pb-3">
            <ErrorPanel error={start.error} />
          </div>
        ) : null}
      </Panel>

      {!selectedRun ? (
        <Alert title="No calculation runs">
          Select a scenario and run the first forecast. Outputs and failures
          will remain here as versioned history.
        </Alert>
      ) : (
        <div className="grid gap-3 @5xl/calculations:grid-cols-[250px_minmax(0,1fr)]">
          <RunHistory
            runs={runs.data?.runs ?? []}
            selectedRunId={selectedRun.id}
            onSelect={setSelectedRunId}
          />
          <RunOutput
            run={selectedRun}
            latestSuccessful={latestSuccessful}
            isRerunning={rerun.isPending}
            onRerun={() => rerun.mutate(selectedRun.id)}
            onShowLatest={() =>
              latestSuccessful && setSelectedRunId(latestSuccessful.id)
            }
          />
        </div>
      )}
      {rerun.isError ? <ErrorPanel error={rerun.error} /> : null}
    </div>
  );
}

function RunHistory({
  runs,
  selectedRunId,
  onSelect,
}: {
  runs: CalculationRunRead[];
  selectedRunId: string;
  onSelect: (id: string) => void;
}) {
  return (
    <Panel>
      <PanelHeader title="Run history" meta={`${runs.length} persisted`} />
      <div className="space-y-2 p-3">
        {runs.map((run) => (
          <button
            key={run.id}
            type="button"
            className={`block w-full rounded-md border p-2 text-left text-xs ${
              selectedRunId === run.id
                ? "border-[rgb(var(--primary))] bg-[rgb(var(--muted))]"
                : "border-[rgb(var(--border))]"
            }`}
            onClick={() => onSelect(run.id)}
          >
            <span className="flex items-center justify-between gap-2">
              <span className="font-mono">{truncateId(run.id)}</span>
              <Badge tone={runTone[run.status]}>{run.status}</Badge>
            </span>
            <span className="mt-1 block text-[rgb(var(--muted-foreground))]">
              {run.createdAt.toLocaleString()} · {run.forecastPeriods} periods
            </span>
          </button>
        ))}
      </div>
    </Panel>
  );
}

function RunOutput({
  run,
  latestSuccessful,
  isRerunning,
  onRerun,
  onShowLatest,
}: {
  run: CalculationRunRead;
  latestSuccessful?: CalculationRunRead;
  isRerunning: boolean;
  onRerun: () => void;
  onShowLatest: () => void;
}) {
  const running = run.status === "queued" || run.status === "running";
  return (
    <Panel>
      <PanelHeader
        title={`Run ${truncateId(run.id)}`}
        meta={`${run.engineVersion} · input ${run.inputHash.slice(0, 12)} · as of ${dateOnly(run.asOfDate)}`}
        actions={
          <Button
            variant="outline"
            size="sm"
            disabled={isRerunning || running}
            onClick={onRerun}
          >
            <RefreshCw
              className={isRerunning ? "size-3 animate-spin" : "size-3"}
            />
            {isRerunning ? "Rerunning…" : "Rerun current inputs"}
          </Button>
        }
      />
      <div className="space-y-3 p-3">
        {running ? (
          <Alert title="Forecast is running" tone="warning">
            Status refreshes automatically while calculation is in progress.
          </Alert>
        ) : null}
        {run.status === "failed" ? (
          <>
            <Alert
              title={run.error?.code ?? "Calculation failed"}
              tone="danger"
            >
              {run.error?.message ?? "The forecast did not complete."}
            </Alert>
            {latestSuccessful && latestSuccessful.id !== run.id ? (
              <Alert title="Prior valid output preserved">
                <button
                  type="button"
                  className="underline"
                  onClick={onShowLatest}
                >
                  Review the latest successful forecast
                </button>
                .
              </Alert>
            ) : null}
          </>
        ) : null}
        {run.status === "succeeded" && run.outputs.length ? (
          <ForecastTable rows={run.outputs} />
        ) : null}
        {run.status === "succeeded" && !run.outputs.length ? (
          <Alert title="No forecast outputs">
            This run completed without projected periods.
          </Alert>
        ) : null}
      </div>
    </Panel>
  );
}

function ForecastTable({ rows }: { rows: ForecastPeriodRead[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] border-collapse text-right text-xs">
        <caption className="sr-only">Projected balance sheet outputs</caption>
        <thead>
          <tr className="border-b border-[rgb(var(--border))] text-[rgb(var(--muted-foreground))]">
            <th className="p-2 text-left">Period end</th>
            <th className="p-2">Assets</th>
            <th className="p-2">Liabilities</th>
            <th className="p-2">Equity</th>
            <th className="p-2">Cash</th>
            <th className="p-2">Inflows</th>
            <th className="p-2">Outflows</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.id}
              className="border-b border-[rgb(var(--border))] last:border-0"
            >
              <td className="p-2 text-left font-medium">
                {dateOnly(row.periodEnd)}
              </td>
              <MoneyCell row={row} value={row.totalAssets} />
              <MoneyCell row={row} value={row.totalLiabilities} />
              <MoneyCell row={row} value={row.totalEquity} />
              <MoneyCell row={row} value={row.cash} />
              <MoneyCell row={row} value={row.projectedInflows} />
              <MoneyCell row={row} value={row.projectedOutflows} />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MoneyCell({ row, value }: { row: ForecastPeriodRead; value: string }) {
  return (
    <td className="p-2 font-mono tabular-nums">
      {new Intl.NumberFormat(undefined, {
        style: "currency",
        currency: row.currency,
        maximumFractionDigits: 2,
      }).format(Number(value))}
    </td>
  );
}

function dateOnly(value: Date) {
  return value.toISOString().slice(0, 10);
}
