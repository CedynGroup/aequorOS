import type {
  CalculationRunRead,
  CalculationRunSummaryRead,
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
  const [runOffset, setRunOffset] = useState(0);
  const runsKey = ["calculation-runs", tenant, caseId, runOffset] as const;
  const scenarios = useQuery({
    queryKey: ["scenarios", tenant, caseId],
    queryFn: () => riskApi.scenarios(tenant, caseId),
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
  const [selectedRunId, setSelectedRunId] = useState("");
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
    setSelectedRunId("");
  }, [caseId, tenant.orgId]);

  useEffect(() => {
    if (!scenarios.data) return;
    setScenarioId((current) =>
      scenarios.data.scenarios.some((scenario) => scenario.id === current)
        ? current
        : (scenarios.data.scenarios[0]?.id ?? ""),
    );
  }, [scenarios.data]);

  useEffect(() => {
    if (!runs.data) return;
    setSelectedRunId((current) =>
      runs.data.runs.some((run) => run.id === current) ||
      current === runs.data.latestSuccessfulRunId
        ? current
        : (runs.data.runs[0]?.id ?? ""),
    );
  }, [runs.data]);

  const parsedForecastPeriods = Number(forecastPeriods);
  const validForecastPeriods =
    Number.isInteger(parsedForecastPeriods) &&
    parsedForecastPeriods >= 1 &&
    parsedForecastPeriods <= 12;
  const activeScenarioId = scenarios.data?.scenarios.some(
    (scenario) => scenario.id === scenarioId,
  )
    ? scenarioId
    : (scenarios.data?.scenarios[0]?.id ?? "");

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
  const hasRunHistory = Boolean(runs.data?.runs.length || selectedRunId);
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
              value={activeScenarioId}
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
              step={1}
              value={forecastPeriods}
              onChange={(event) => setForecastPeriods(event.target.value)}
            />
          </div>
          <Button
            disabled={
              isSubmitting || !activeScenarioId || !validForecastPeriods
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

      {!hasRunHistory ? (
        <Alert title="No calculation runs">
          Select a scenario and run the first forecast. Outputs and failures
          will remain here as versioned history.
        </Alert>
      ) : (
        <div className="grid gap-3 @5xl/calculations:grid-cols-[250px_minmax(0,1fr)]">
          <RunHistory
            runs={runs.data?.runs ?? []}
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
  total,
  selectedRunId,
  onSelect,
  hasMore,
  offset,
  onPrevious,
  onNext,
}: {
  runs: CalculationRunSummaryRead[];
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
              <span className="font-mono">{truncateId(run.id)}</span>
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
  onRerun,
  onShowLatest,
}: {
  run: CalculationRunRead;
  latestSuccessfulRunId?: string | null;
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
    "reporting_period_ids",
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
  const assumptionIds = Array.isArray(record.assumption_ids)
    ? record.assumption_ids.filter(
        (value): value is string => typeof value === "string",
      )
    : [];
  const periodBounds =
    "period_start_date" in record || "period_end_date" in record
      ? `period ${String(record.period_start_date ?? "unbounded")} to ${String(record.period_end_date ?? "unbounded")}`
      : null;
  return [
    identity,
    typeof record.id === "string" ? record.id : null,
    typeof record.cash_flow_date === "string"
      ? `cash-flow date ${record.cash_flow_date}`
      : null,
    periodBounds,
    missingFields.length ? `missing ${missingFields.join(", ")}` : null,
    assumptionIds.length ? `assumptions ${assumptionIds.join(", ")}` : null,
  ]
    .filter(Boolean)
    .join(" — ");
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
      {formatMoney(value, row.currency)}
    </td>
  );
}

function formatMoney(value: string, currency: string) {
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value);
  if (!match) return `${currency} ${value}`;

  let formatter: Intl.NumberFormat;
  try {
    formatter = new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      maximumFractionDigits: 2,
    });
  } catch (error) {
    if (error instanceof RangeError) return `${currency} ${value}`;
    throw error;
  }
  const resolvedOptions = formatter.resolvedOptions();
  const minimumFractionDigits = resolvedOptions.minimumFractionDigits ?? 0;
  const maximumFractionDigits = resolvedOptions.maximumFractionDigits ?? 2;
  const rounded = roundDecimal(match[2], match[3] ?? "", maximumFractionDigits);
  const groupedInteger = new Intl.NumberFormat(undefined, {
    maximumFractionDigits: 0,
  }).format(BigInt(rounded.integer));
  const decimalSeparator =
    formatter.formatToParts(0.1).find((part) => part.type === "decimal")
      ?.value ?? ".";
  const fraction = rounded.fraction.padEnd(minimumFractionDigits, "0");
  const formattedNumber = `${groupedInteger}${fraction ? decimalSeparator + fraction : ""}`;
  const parts = formatter.formatToParts(match[1] ? -0 : 0);
  let insertedNumber = false;

  return parts
    .map((part) => {
      if (["integer", "group", "decimal", "fraction"].includes(part.type)) {
        if (insertedNumber) return "";
        insertedNumber = true;
        return formattedNumber;
      }
      return part.value;
    })
    .join("");
}

function roundDecimal(integer: string, fraction: string, digits: number) {
  const keptFraction = fraction.slice(0, digits).padEnd(digits, "0");
  const shouldRoundUp = Number(fraction[digits] ?? "0") >= 5;
  const scale = 10n ** BigInt(digits);
  const scaled = BigInt(integer) * scale + BigInt(keptFraction || "0");
  const rounded = scaled + (shouldRoundUp ? 1n : 0n);
  const roundedInteger = rounded / scale;
  const roundedFraction =
    digits > 0 ? (rounded % scale).toString().padStart(digits, "0") : "";
  return { integer: roundedInteger.toString(), fraction: roundedFraction };
}

function dateOnly(value: Date) {
  return value.toISOString().slice(0, 10);
}
