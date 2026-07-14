import type {
  CalculationRunRead,
  CalculationRunSummaryRead,
  CalculationStatus,
  ForecastPeriodRead,
} from "@aequoros/risk-service-api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
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
import { formatMoney } from "../../lib/money";
import {
  focusWorkspaceTarget,
  workspaceHash,
} from "../../lib/workspace-deep-link";
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
  mutationDisabled = false,
  mutationDisabledReason = "demo",
}: {
  tenant: TenantHeaders;
  caseId: string;
  mutationDisabled?: boolean;
  mutationDisabledReason?: "demo" | "retired-case";
}) {
  const queryClient = useQueryClient();
  const deepLink = calculationDeepLink();
  const focusedDeepLinks = useRef(new Set<string>());
  const [runOffset, setRunOffset] = useState(0);
  const runsKey = ["calculation-runs", tenant, caseId, runOffset] as const;
  const scenarios = useQuery({
    queryKey: ["scenarios", tenant, caseId, true],
    queryFn: () => riskApi.scenarios(tenant, caseId, true),
  });
  const runs = useQuery({
    queryKey: runsKey,
    queryFn: () =>
      riskApi.calculationRuns(tenant, caseId, undefined, 25, runOffset),
    refetchInterval: (query) =>
      query.state.data?.runs.some((run) =>
        (["queued", "running"] as CalculationStatus[]).includes(run.status),
      )
        ? 1000
        : false,
  });
  const [scenarioId, setScenarioId] = useState("");
  const [forecastPeriods, setForecastPeriods] = useState("3");
  const [selectedRunId, setSelectedRunId] = useState(
    () => deepLink?.runId ?? "",
  );
  const selectedRun = useQuery({
    queryKey: ["calculation-run", tenant, caseId, selectedRunId],
    queryFn: () => riskApi.calculationRun(tenant, caseId, selectedRunId),
    enabled: Boolean(selectedRunId),
    refetchInterval: (query) =>
      query.state.data &&
      (["queued", "running"] as CalculationStatus[]).includes(
        query.state.data.status,
      )
        ? 1000
        : false,
  });

  useEffect(() => {
    setRunOffset(0);
    setScenarioId("");
    setSelectedRunId(deepLink?.runId ?? "");
  }, [caseId, deepLink?.runId, tenant.orgId]);

  useEffect(() => {
    if (!scenarios.data) return;
    setScenarioId((current) =>
      scenarios.data.scenarios.some(
        (scenario) => scenario.id === current && !scenario.archivedAt,
      )
        ? current
        : (scenarios.data.scenarios.find((scenario) => !scenario.archivedAt)
            ?.id ?? ""),
    );
  }, [scenarios.data]);

  useEffect(() => {
    if (!runs.data) return;
    setSelectedRunId((current) =>
      runs.data.runs.some((run) => run.id === current) ||
      current === runs.data.latestSuccessfulRunId ||
      current === deepLink?.runId
        ? current
        : (runs.data.runs[0]?.id ?? ""),
    );
  }, [deepLink?.runId, runs.data]);

  useEffect(() => {
    if (
      selectedRun.data &&
      deepLink?.targetId &&
      !focusedDeepLinks.current.has(deepLink.targetId) &&
      focusWorkspaceTarget(deepLink.targetId)
    ) {
      focusedDeepLinks.current.add(deepLink.targetId);
    }
  }, [deepLink?.targetId, selectedRun.data]);

  const parsedForecastPeriods = Number(forecastPeriods);
  const validForecastPeriods =
    Number.isInteger(parsedForecastPeriods) &&
    parsedForecastPeriods >= 1 &&
    parsedForecastPeriods <= 12;
  const activeScenarioId = scenarios.data?.scenarios.some(
    (scenario) => scenario.id === scenarioId && !scenario.archivedAt,
  )
    ? scenarioId
    : (scenarios.data?.scenarios.find((scenario) => !scenario.archivedAt)?.id ??
      "");

  const refreshRuns = async (run: CalculationRunRead, message: string) => {
    setRunOffset(0);
    setSelectedRunId(run.id);
    queryClient.setQueryData(["calculation-run", tenant, caseId, run.id], run);
    await queryClient.invalidateQueries({
      queryKey: ["calculation-runs", tenant, caseId],
    });
    if (run.status === "failed") toast.error(run.error?.message ?? message);
    else toast.success(message);
  };
  const start = useMutation({
    mutationFn: () =>
      riskApi.startCalculation(tenant, caseId, {
        scenarioId: activeScenarioId,
        forecastPeriods: parsedForecastPeriods,
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
  const selectedSummary =
    runs.data?.runs.find((run) => run.id === selectedRunId) ?? selectedRun.data;
  const selectedScenario = availableScenarios.find(
    (scenario) =>
      scenario.id ===
      (selectedRun.data?.scenarioId ?? selectedSummary?.scenarioId),
  );
  const activeScenarios = availableScenarios.filter(
    (scenario) => !scenario.archivedAt,
  );
  const archivedAudit = Boolean(selectedScenario?.archivedAt);
  const hasRunHistory = Boolean(runs.data?.runs.length || selectedRunId);
  const isSubmitting = start.isPending || rerun.isPending;

  return (
    <div className="@container/calculations space-y-3">
      {activeScenarios.length ? (
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
                value={activeScenarioId}
                onValueChange={setScenarioId}
                placeholder="Choose a scenario"
                disabled={mutationDisabled}
              >
                {activeScenarios.map((scenario) => (
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
                step={1}
                value={forecastPeriods}
                disabled={mutationDisabled}
                onChange={(event) => setForecastPeriods(event.target.value)}
              />
            </div>
            <Button
              disabled={
                mutationDisabled ||
                isSubmitting ||
                !activeScenarioId ||
                !validForecastPeriods
              }
              onClick={() => start.mutate()}
            >
              {start.isPending ? "Starting…" : "Run forecast"}
            </Button>
          </div>
          {mutationDisabled ? (
            <div className="px-3 pb-3">
              {mutationDisabledReason === "retired-case" ? (
                <Alert title="Forecast mutations unavailable for retired case">
                  Historical forecasts remain available for review, but archived
                  cases cannot start or rerun calculations.
                </Alert>
              ) : (
                <Alert title="Mutation unavailable in demo mode">
                  Switch to live API data to start or rerun a forecast.
                </Alert>
              )}
            </div>
          ) : null}
          {start.isError ? (
            <div className="px-3 pb-3">
              <ErrorPanel error={start.error} />
            </div>
          ) : null}
        </Panel>
      ) : null}

      {archivedAudit ? (
        <Panel>
          <PanelHeader
            title="Archived forecast audit"
            meta="Historical calculation evidence is preserved read only"
            actions={<Badge tone="warning">Archived</Badge>}
          />
          <div className="p-3 text-sm">
            {selectedScenario?.name} · rerun controls are unavailable in audit
            mode.
          </div>
        </Panel>
      ) : null}

      {!hasRunHistory ? (
        <Alert title="No calculation runs">
          Select a scenario and run the first forecast. Outputs and failures
          will remain here as versioned history.
        </Alert>
      ) : (
        <div className="grid gap-3 @5xl/calculations:grid-cols-[250px_minmax(0,1fr)]">
          <RunHistory
            runs={runs.data?.runs ?? []}
            scenarioNames={
              new Map(
                availableScenarios.map((scenario) => [
                  scenario.id,
                  scenario.name,
                ]),
              )
            }
            total={runs.data?.total ?? 0}
            selectedRunId={selectedSummary?.id ?? selectedRunId}
            onSelect={setSelectedRunId}
            hasMore={runs.data?.hasMore ?? false}
            offset={runs.data?.offset ?? 0}
            onPrevious={() =>
              setRunOffset((current) => Math.max(0, current - 25))
            }
            onNext={() => setRunOffset((current) => current + 25)}
          />
          {selectedRun.isLoading ? (
            <Skeleton className="h-72" />
          ) : selectedRun.isError ? (
            <ErrorPanel error={selectedRun.error} />
          ) : selectedRun.data ? (
            <RunOutput
              run={selectedRun.data}
              latestSuccessfulRunId={runs.data?.latestSuccessfulRunId}
              isRerunning={rerun.isPending}
              readOnlyReason={
                mutationDisabled
                  ? mutationDisabledReason
                  : archivedAudit
                    ? "archived-scenario"
                    : null
              }
              onRerun={() => rerun.mutate(selectedRun.data.id)}
              onShowLatest={() =>
                runs.data?.latestSuccessfulRunId &&
                setSelectedRunId(runs.data.latestSuccessfulRunId)
              }
            />
          ) : null}
        </div>
      )}
      {rerun.isError ? <ErrorPanel error={rerun.error} /> : null}
    </div>
  );
}

function RunHistory({
  runs,
  scenarioNames,
  total,
  selectedRunId,
  onSelect,
  hasMore,
  offset,
  onPrevious,
  onNext,
}: {
  runs: CalculationRunSummaryRead[];
  scenarioNames: ReadonlyMap<string, string>;
  total: number;
  selectedRunId: string;
  onSelect: (id: string) => void;
  hasMore: boolean;
  offset: number;
  onPrevious: () => void;
  onNext: () => void;
}) {
  return (
    <Panel>
      <PanelHeader title="Run history" meta={`${total} persisted`} />
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
              <span className="font-medium">
                {scenarioNames.get(run.scenarioId) ?? "Forecast"}
              </span>
              <Badge tone={runTone[run.status]}>{run.status}</Badge>
            </span>
            <span className="mt-1 block text-[rgb(var(--muted-foreground))]">
              {run.createdAt.toLocaleString()} · {run.forecastPeriods} periods
            </span>
          </button>
        ))}
        {offset > 0 || hasMore ? (
          <div className="flex justify-between gap-2 pt-1">
            <Button
              size="sm"
              variant="outline"
              disabled={offset === 0}
              onClick={onPrevious}
            >
              Previous
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={!hasMore}
              onClick={onNext}
            >
              Next
            </Button>
          </div>
        ) : null}
      </div>
    </Panel>
  );
}

function RunOutput({
  run,
  latestSuccessfulRunId,
  isRerunning,
  readOnlyReason,
  onRerun,
  onShowLatest,
}: {
  run: CalculationRunRead;
  latestSuccessfulRunId?: string | null;
  isRerunning: boolean;
  readOnlyReason: "archived-scenario" | "demo" | "retired-case" | null;
  onRerun: () => void;
  onShowLatest: () => void;
}) {
  const running = run.status === "queued" || run.status === "running";
  const readOnly = readOnlyReason !== null;
  return (
    <Panel>
      <PanelHeader
        title="Forecast result"
        meta={`${run.engineVersion} · as of ${dateOnly(run.asOfDate)}`}
        actions={
          readOnly ? null : (
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
          )
        }
      />
      <div className="space-y-3 p-3">
        {readOnlyReason === "archived-scenario" ? (
          <Alert title="Archived scenario · read only" tone="warning">
            This exact historical forecast is available for audit. Rerun and
            mutation controls are unavailable.
          </Alert>
        ) : readOnlyReason === "retired-case" ? (
          <Alert title="Retired case · read only" tone="warning">
            This historical forecast remains available for review. Archived
            cases cannot rerun calculations.
          </Alert>
        ) : readOnlyReason === "demo" ? (
          <Alert title="Demo mode · read only" tone="warning">
            Switch to live API data to rerun this forecast.
          </Alert>
        ) : null}
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
            <DiagnosticDetails details={run.error?.details} />
            {latestSuccessfulRunId && latestSuccessfulRunId !== run.id ? (
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
          <ForecastTable runId={run.id} rows={run.outputs} />
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

function DiagnosticDetails({
  details,
}: {
  details?: Record<string, unknown> | null;
}) {
  if (!details) return null;
  const correctiveAction =
    typeof details.corrective_action === "string"
      ? details.corrective_action
      : null;
  const records = [
    "obligations",
    "balances",
    "cash_flows",
    "inputs",
    "ambiguous_categories",
    "missing_values",
  ]
    .flatMap((key) => (Array.isArray(details[key]) ? details[key] : []))
    .filter((item): item is Record<string, unknown> =>
      Boolean(item && typeof item === "object"),
    );
  const missing = [
    "missing_categories",
    "unreviewed_assumptions",
    "reporting_periods",
  ]
    .flatMap((key) => (Array.isArray(details[key]) ? details[key] : []))
    .filter((item): item is string => typeof item === "string");
  if (!correctiveAction && !records.length && !missing.length) return null;
  return (
    <Alert title="How to resolve this run" tone="warning">
      {correctiveAction ? <p>{correctiveAction}</p> : null}
      {records.length ? (
        <ul className="mt-2 list-disc space-y-1 pl-4 font-mono text-xs">
          {records.map((record, index) => (
            <li
              key={`${String(record.id ?? record.category ?? index)}-${index}`}
            >
              {diagnosticRecord(record)}
            </li>
          ))}
        </ul>
      ) : null}
      {missing.length ? (
        <p className="mt-2">Required: {missing.join(", ")}</p>
      ) : null}
    </Alert>
  );
}

function diagnosticRecord(record: Record<string, unknown>) {
  const identity = [
    record.label,
    record.type,
    record.balance_type,
    record.obligation_type,
    record.category,
    record.currency,
    record.key,
  ]
    .filter((value): value is string => typeof value === "string")
    .join(" · ");
  const missingFields = Array.isArray(record.missing_fields)
    ? record.missing_fields.filter(
        (value): value is string => typeof value === "string",
      )
    : [];
  const assumptions = Array.isArray(record.assumptions)
    ? record.assumptions.filter(
        (value): value is string => typeof value === "string",
      )
    : [];
  const periodBounds =
    "period_start_date" in record || "period_end_date" in record
      ? `period ${String(record.period_start_date ?? "unbounded")} to ${String(record.period_end_date ?? "unbounded")}`
      : null;
  return [
    identity,
    typeof record.cash_flow_date === "string"
      ? `cash-flow date ${record.cash_flow_date}`
      : null,
    periodBounds,
    missingFields.length ? `missing ${missingFields.join(", ")}` : null,
    assumptions.length ? `assumptions ${assumptions.join(", ")}` : null,
  ]
    .filter(Boolean)
    .join(" — ");
}

function ForecastTable({
  runId,
  rows,
}: {
  runId: string;
  rows: ForecastPeriodRead[];
}) {
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
              id={`calculation-run-${runId}-forecast-period-${row.periodNumber}`}
              tabIndex={-1}
              className="border-b border-[rgb(var(--border))] outline-none last:border-0 focus:bg-amber-100"
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

function calculationDeepLink() {
  const targetId = workspaceHash();
  const prefix = "calculation-run-";
  const separator = "-forecast-period-";
  if (!targetId.startsWith(prefix) || !targetId.includes(separator))
    return null;
  const runId = targetId.slice(prefix.length, targetId.indexOf(separator));
  const period = targetId.slice(targetId.indexOf(separator) + separator.length);
  const periodNumber = Number(period);
  return isUuid(runId) &&
    /^\d+$/.test(period) &&
    Number.isSafeInteger(periodNumber) &&
    periodNumber > 0
    ? { runId, targetId }
    : null;
}

function isUuid(value: string) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
    value,
  );
}

function MoneyCell({ row, value }: { row: ForecastPeriodRead; value: string }) {
  return (
    <td className="p-2 font-mono tabular-nums">
      {formatMoney(value, row.currency)}
    </td>
  );
}

function dateOnly(value: Date) {
  return value.toISOString().slice(0, 10);
}
