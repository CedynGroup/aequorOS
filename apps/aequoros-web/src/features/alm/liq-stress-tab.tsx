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
import {
  formatMoneyCompact,
  formatMoneyFull,
  formatPct,
  statusTone,
} from "./shared/format";
import { metricString } from "./shared/regulatory";
import { RunBadge } from "./shared/run-badge";

const liquidityScenarios = [
  "baseline",
  "idiosyncratic",
  "market_wide",
  "combined",
] as const;

const scenarioLabels: Record<string, string> = {
  baseline: "Baseline",
  idiosyncratic: "Idiosyncratic",
  market_wide: "Market-wide",
  combined: "Combined",
};

export function LiquidityStressTab({ tenant, bank, period }: AlmTabProps) {
  const queryClient = useQueryClient();
  const runsQuery = useQuery({
    queryKey: [
      "alm-regulatory-runs",
      tenant,
      bank.id,
      "liquidity",
      period.id,
    ],
    queryFn: () =>
      riskApi.listRegulatoryRuns(tenant, bank.id, {
        module: "liquidity",
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
  const scenarioRunIds = liquidityScenarios.map(
    (scenario) => latestByScenario.get(scenario)?.id,
  );
  const runQueries = useQueries({
    queries: scenarioRunIds.map((runId) => ({
      queryKey: ["alm-regulatory-run", tenant, bank.id, runId],
      queryFn: () => riskApi.getRegulatoryRun(tenant, bank.id, runId ?? ""),
      enabled: Boolean(runId),
    })),
  });
  const runAll = useMutation({
    mutationFn: () =>
      riskApi.runAllLiquidityScenarios(tenant, bank.id, {
        reportingPeriodId: period.id,
      }),
    onSuccess: (batch) => {
      batch.runs.forEach((run) => {
        const label = scenarioLabels[run.scenarioCode] ?? run.scenarioCode;
        if (run.status === "succeeded") {
          toast.success(`${label} liquidity scenario succeeded`);
        } else {
          toast.error(
            `${label} liquidity scenario ${run.status}${
              run.error ? `: ${run.error.message}` : ""
            }`,
          );
        }
      });
      [
        "alm-regulatory-runs",
        "alm-regulatory-run",
        "alm-liquidity-dashboard",
        "alm-bsd3",
      ].forEach((prefix) => {
        void queryClient.invalidateQueries({ queryKey: [prefix] });
      });
    },
    onError: (error) => {
      toast.error(
        isApiError(error)
          ? error.message
          : "Liquidity stress scenarios could not be started.",
      );
    },
  });

  if (runsQuery.isLoading) {
    return (
      <div aria-label="Loading liquidity stress scenarios" className="space-y-3">
        <Skeleton className="h-24" />
        <Skeleton className="h-64" />
      </div>
    );
  }
  if (runsQuery.error) return <ErrorPanel error={runsQuery.error} />;

  const hasRuns = latestByScenario.size > 0;
  const succeededRuns = new Map<string, RegulatoryRunRead>();
  runQueries.forEach((query, index) => {
    const scenario = liquidityScenarios[index];
    if (query.data?.status === "succeeded") {
      succeededRuns.set(scenario, query.data);
    }
  });

  return (
    <div className="space-y-3">
      <Panel>
        <PanelHeader
          title="Liquidity stress scenarios"
          meta="Deposit run-off, inflow, and HQLA haircut shocks applied to the reporting period"
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
          Each scenario reruns the LCR and NSFR engines with stressed run-off
          rates, inflow multipliers, and HQLA haircuts, persisting an auditable
          run per scenario for {period.label}.
        </div>
      </Panel>
      {!hasRuns ? (
        <Alert title="No stress runs for this period">
          Run all scenarios to calculate baseline, idiosyncratic, market-wide,
          and combined liquidity stress results for {period.label}.
        </Alert>
      ) : (
        <>
          <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-4">
            {liquidityScenarios.map((scenario, index) => (
              <ScenarioCard
                key={scenario}
                scenario={scenario}
                summary={latestByScenario.get(scenario)}
                query={runQueries[index]}
                currency={bank.currency}
              />
            ))}
          </div>
          <Panel>
            <PanelHeader
              title="Scenario comparison"
              meta="Latest run per scenario for this reporting period"
            />
            <div className="p-3">
              <ComparisonTable
                runs={succeededRuns}
                currency={bank.currency}
              />
            </div>
          </Panel>
        </>
      )}
    </div>
  );
}

function ScenarioCard({
  scenario,
  summary,
  query,
  currency,
}: {
  scenario: string;
  summary?: RegulatoryRunSummaryRead;
  query: { data?: RegulatoryRunRead; isLoading: boolean; error: unknown };
  currency: string;
}) {
  const label = scenarioLabels[scenario] ?? labelize(scenario);
  if (!summary) {
    return (
      <Panel>
        <PanelHeader title={label} />
        <div className="p-3 text-xs text-[rgb(var(--muted-foreground))]">
          Not yet run for this period.
        </div>
      </Panel>
    );
  }
  if (query.isLoading) {
    return (
      <Panel>
        <PanelHeader title={label} />
        <div aria-label={`Loading ${label} scenario`} className="p-3">
          <Skeleton className="h-36" />
        </div>
      </Panel>
    );
  }
  if (query.error) {
    return (
      <Panel>
        <PanelHeader title={label} />
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
        <PanelHeader title={label} actions={<RunBadge run={run} />} />
        <div className="p-3">
          <Alert title={`Run ${run.status}`} tone="danger">
            {run.error?.message ?? "The scenario run did not succeed."}
          </Alert>
        </div>
      </Panel>
    );
  }

  const lcr = run.metricResults.find(
    (metric) => metric.metricCode === "lcr_pct",
  );
  const nsfr = run.metricResults.find(
    (metric) => metric.metricCode === "nsfr_pct",
  );

  return (
    <Panel>
      <PanelHeader
        title={label}
        actions={
          lcr ? (
            <Badge tone={statusTone(lcr.status)}>{labelize(lcr.status)}</Badge>
          ) : null
        }
      />
      <div className="space-y-2 p-3">
        <div>
          <div className="text-xs text-[rgb(var(--muted-foreground))]">LCR</div>
          <div className="font-mono text-2xl font-semibold tabular-nums">
            {formatPct(lcr?.metricValue ?? metricString(run.metrics, "lcr_pct"))}
          </div>
        </div>
        <dl className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 text-xs">
          <dt className="text-[rgb(var(--muted-foreground))]">NSFR</dt>
          <dd className="m-0 flex items-center gap-1.5 font-mono tabular-nums">
            {formatPct(nsfr?.metricValue ?? metricString(run.metrics, "nsfr_pct"))}
            {nsfr ? (
              <Badge tone={statusTone(nsfr.status)}>{labelize(nsfr.status)}</Badge>
            ) : null}
          </dd>
          <dt className="text-[rgb(var(--muted-foreground))]">Net outflows (30d)</dt>
          <dd
            className="m-0 font-mono tabular-nums"
            title={formatMoneyFull(
              metricString(run.metrics, "net_outflows_30d_ghs"),
              currency,
            )}
          >
            {formatMoneyCompact(
              metricString(run.metrics, "net_outflows_30d_ghs"),
              currency,
            )}
          </dd>
          <dt className="text-[rgb(var(--muted-foreground))]">HQLA</dt>
          <dd
            className="m-0 font-mono tabular-nums"
            title={formatMoneyFull(
              metricString(run.metrics, "hqla_total_ghs"),
              currency,
            )}
          >
            {formatMoneyCompact(
              metricString(run.metrics, "hqla_total_ghs"),
              currency,
            )}
          </dd>
        </dl>
        <ScenarioAssumptions run={run} />
        <RunBadge run={run} />
      </div>
    </Panel>
  );
}

function ScenarioAssumptions({ run }: { run: RegulatoryRunRead }) {
  const inputs = run.inputs as {
    shocks?: Record<string, string>;
    parameters?: { outflow_runoff_rates_pct?: Record<string, string> };
  };
  const shocks = inputs.shocks ?? {};
  const baseRunoffRates = inputs.parameters?.outflow_runoff_rates_pct ?? {};
  const entries = Object.entries(shocks);
  const runoffOverrides = entries.filter(([key]) => key.startsWith("runoff:"));
  const otherShocks = entries.filter(([key]) => !key.startsWith("runoff:"));

  if (!entries.length) {
    return (
      <div className="text-[11px] text-[rgb(var(--muted-foreground))]">
        No shocks applied — regulatory base assumptions.
      </div>
    );
  }

  return (
    <details className="rounded border border-[rgb(var(--border))] p-2 text-xs">
      <summary className="cursor-pointer font-medium">
        Stress assumptions ({entries.length})
      </summary>
      <div className="mt-2 space-y-2">
        {runoffOverrides.length ? (
          <DenseTable ariaLabel={`${run.scenarioCode} runoff overrides`}>
            <thead>
              <tr>
                <Th>Runoff category</Th>
                <Th align="right">Base %</Th>
                <Th align="right">Stressed %</Th>
              </tr>
            </thead>
            <tbody>
              {runoffOverrides.map(([key, value]) => {
                const category = key.slice("runoff:".length);
                const base = baseRunoffRates[category];
                return (
                  <tr key={key}>
                    <Td>{labelize(category)}</Td>
                    <NumCell value={base ? formatPct(base) : "—"} />
                    <NumCell value={formatPct(value)} emphasis />
                  </tr>
                );
              })}
            </tbody>
          </DenseTable>
        ) : null}
        {otherShocks.length ? (
          <dl className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1">
            {otherShocks.map(([key, value]) => (
              <ShockRow key={key} shockKey={key} value={value} />
            ))}
          </dl>
        ) : null}
      </div>
    </details>
  );
}

function ShockRow({ shockKey, value }: { shockKey: string; value: string }) {
  let label = labelize(shockKey);
  let display = value;
  if (shockKey === "inflow_multiplier") {
    label = "Inflow multiplier";
    display = `×${value}`;
  } else if (shockKey === "hqla_securities_haircut_pct") {
    label = "HQLA securities haircut";
    display = formatPct(value);
  } else if (shockKey.startsWith("asf:")) {
    label = `ASF weight · ${labelize(shockKey.slice("asf:".length))}`;
    display = formatPct(value);
  } else if (shockKey.startsWith("rsf:")) {
    label = `RSF weight · ${labelize(shockKey.slice("rsf:".length))}`;
    display = formatPct(value);
  }
  return (
    <>
      <dt className="text-[rgb(var(--muted-foreground))]">{label}</dt>
      <dd className="m-0 font-mono tabular-nums">{display}</dd>
    </>
  );
}

function ComparisonTable({
  runs,
  currency,
}: {
  runs: Map<string, RegulatoryRunRead>;
  currency: string;
}) {
  const rows: Array<{ label: string; key: string; unit: "ghs" | "pct" }> = [
    { label: "HQLA", key: "hqla_total_ghs", unit: "ghs" },
    { label: "Net outflows (30d)", key: "net_outflows_30d_ghs", unit: "ghs" },
    { label: "ASF", key: "asf_total_ghs", unit: "ghs" },
    { label: "RSF", key: "rsf_total_ghs", unit: "ghs" },
    { label: "LCR", key: "lcr_pct", unit: "pct" },
    { label: "NSFR", key: "nsfr_pct", unit: "pct" },
  ];

  return (
    <DenseTable ariaLabel="Liquidity scenario comparison">
      <thead>
        <tr>
          <Th>Metric</Th>
          {liquidityScenarios.map((scenario) => (
            <Th key={scenario} align="right">
              {scenarioLabels[scenario]}
            </Th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.key}>
            <Td>{row.label}</Td>
            {liquidityScenarios.map((scenario) => {
              const value = metricString(runs.get(scenario)?.metrics, row.key);
              if (value === null) {
                return <NumCell key={scenario} value="—" tone="muted" />;
              }
              return (
                <NumCell
                  key={scenario}
                  value={
                    row.unit === "pct"
                      ? formatPct(value)
                      : formatMoneyCompact(value, currency)
                  }
                  title={
                    row.unit === "ghs"
                      ? formatMoneyFull(value, currency)
                      : undefined
                  }
                  emphasis={row.unit === "pct"}
                />
              );
            })}
          </tr>
        ))}
      </tbody>
    </DenseTable>
  );
}
