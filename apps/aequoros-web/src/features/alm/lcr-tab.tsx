import type { LiquidityDashboardLineRead } from "@aequoros/risk-service-api";
import { useQuery } from "@tanstack/react-query";
import { Loader2, PlayCircle } from "lucide-react";

import {
  Alert,
  Button,
  Panel,
  PanelHeader,
  Skeleton,
} from "../../components/ui";
import { riskApi } from "../../lib/api";
import { ErrorPanel } from "../../shared/route-ui";
import type { AlmTabProps } from "./alm-console";
import { CompositionBar } from "./shared/composition-bar";
import { DenseTable, NumCell, Td, Th } from "./shared/dense-table";
import {
  formatMoneyCompact,
  formatMoneyFull,
  formatPct,
} from "./shared/format";
import { RatioCard } from "./shared/ratio-card";
import {
  bogDisplayThresholds,
  metricThreshold,
  useRunBaseline,
} from "./shared/regulatory";
import { RunBadge } from "./shared/run-badge";
import { TrendChart } from "./shared/trend-chart";
import { ValidationList } from "./shared/validation-list";

export function LcrTab({ tenant, bank, period }: AlmTabProps) {
  const dashboard = useQuery({
    queryKey: ["alm-liquidity-dashboard", tenant, bank.id, period.id],
    queryFn: () => riskApi.getLiquidityDashboard(tenant, bank.id, period.id),
  });
  const latestRun = useQuery({
    queryKey: [
      "alm-regulatory-run",
      tenant,
      bank.id,
      dashboard.data?.latestRunId,
    ],
    queryFn: () =>
      riskApi.getRegulatoryRun(
        tenant,
        bank.id,
        dashboard.data?.latestRunId ?? "",
      ),
    enabled: Boolean(dashboard.data?.latestRunId),
  });
  const runBaseline = useRunBaseline(tenant, bank.id, period.id, "liquidity");

  if (dashboard.isLoading) {
    return (
      <div aria-label="Loading LCR dashboard" className="space-y-3">
        <Skeleton className="h-32" />
        <Skeleton className="h-52" />
      </div>
    );
  }
  if (dashboard.error) return <ErrorPanel error={dashboard.error} />;
  if (!dashboard.data) return null;

  const data = dashboard.data;
  const lcrMin = metricThreshold(
    latestRun.data,
    "lcr_pct",
    bogDisplayThresholds.lcrMinPct,
  );
  const capNote = data.validations.find(
    (validation) => validation.ruleCode === "inflow_cap_applied",
  );

  return (
    <div className="space-y-3">
      <Panel>
        <PanelHeader
          title="LCR dashboard"
          meta={`${bank.name} · ${period.label}`}
          actions={
            <>
              {latestRun.data ? <RunBadge run={latestRun.data} /> : null}
              <Button
                size="sm"
                disabled={runBaseline.isPending}
                onClick={() => runBaseline.mutate()}
              >
                {runBaseline.isPending ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <PlayCircle className="size-3.5" />
                )}
                Run baseline
              </Button>
            </>
          }
        />
        <div className="space-y-3 p-3">
          {!data.stored ? (
            <Alert title="Live computation" tone="warning">
              Showing live computation — run baseline to persist an auditable
              run.
            </Alert>
          ) : null}
          <div className="grid gap-2 sm:grid-cols-3">
            <RatioCard
              label="LCR"
              value={data.metrics.lcrPct}
              status={data.metrics.lcrStatus}
              thresholds={[{ label: "Min", valuePct: lcrMin }]}
            />
            <MetricTile
              label="HQLA total"
              value={data.metrics.hqlaTotalGhs}
              currency={bank.currency}
            />
            <MetricTile
              label="Net outflows (30d)"
              value={data.metrics.netOutflows30dGhs}
              currency={bank.currency}
            />
          </div>
        </div>
      </Panel>
      <div className="grid gap-3 xl:grid-cols-2">
        <Panel>
          <PanelHeader
            title="HQLA composition"
            meta="Weighted high quality liquid assets"
          />
          <div className="space-y-3 p-3">
            <CompositionBar
              ariaLabel="HQLA composition"
              currency={bank.currency}
              segments={data.hqlaComposition.map((line) => ({
                label: line.description,
                value: line.weightedAmount,
              }))}
            />
            <LineItemTable
              ariaLabel="HQLA line items"
              lines={data.hqlaComposition}
              currency={bank.currency}
              rateHeader="Factor %"
            />
          </div>
        </Panel>
        <Panel>
          <PanelHeader title="12-period LCR trend" meta="Stored runs vs inline computations" />
          <div className="p-3">
            <TrendChart
              seriesLabel="LCR"
              points={data.trend.map((point) => ({
                label: point.label,
                value: point.lcrPct,
                stored: point.stored,
              }))}
              referenceLines={[
                { value: Number(lcrMin), label: "Min", tone: "danger" },
              ]}
            />
          </div>
        </Panel>
      </div>
      <div className="grid gap-3 xl:grid-cols-2">
        <Panel>
          <PanelHeader title="Cash outflows (30 days)" meta="Run-off weighted balances" />
          <div className="p-3">
            <LineItemTable
              ariaLabel="LCR outflows"
              lines={data.outflows}
              currency={bank.currency}
              rateHeader="Runoff %"
            />
          </div>
        </Panel>
        <Panel>
          <PanelHeader title="Cash inflows (30 days)" meta="Inflow-rate weighted balances" />
          <div className="space-y-2 p-3">
            <LineItemTable
              ariaLabel="LCR inflows"
              lines={data.inflows}
              currency={bank.currency}
              rateHeader="Inflow %"
            />
            {capNote ? (
              <div className="rounded border border-[rgb(var(--border))] bg-[rgb(var(--surface-2))] px-2 py-1.5 text-[11px] text-[rgb(var(--muted-foreground))]">
                {capNote.message}
              </div>
            ) : null}
          </div>
        </Panel>
      </div>
      <Panel>
        <PanelHeader title="Validations" meta="Regulatory rule evaluation for this period" />
        <div className="p-3">
          <ValidationList validations={data.validations} />
        </div>
      </Panel>
    </div>
  );
}

function MetricTile({
  label,
  value,
  currency,
}: {
  label: string;
  value: string;
  currency: string;
}) {
  return (
    <div className="min-w-0 rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--surface))] p-3">
      <div className="text-xs font-medium uppercase tracking-[0.04em] text-[rgb(var(--muted-foreground))]">
        {label}
      </div>
      <div
        className="mt-1 truncate font-mono text-2xl font-semibold tabular-nums"
        title={formatMoneyFull(value, currency)}
      >
        {formatMoneyCompact(value, currency)}
      </div>
    </div>
  );
}

export function LineItemTable({
  ariaLabel,
  lines,
  currency,
  rateHeader,
}: {
  ariaLabel: string;
  lines: LiquidityDashboardLineRead[];
  currency: string;
  rateHeader: string;
}) {
  return (
    <DenseTable ariaLabel={ariaLabel}>
      <thead>
        <tr>
          <Th>Line</Th>
          <Th>Category</Th>
          <Th align="right">Balance</Th>
          <Th align="right">{rateHeader}</Th>
          <Th align="right">Weighted</Th>
        </tr>
      </thead>
      <tbody>
        {lines.map((line) => (
          <tr key={line.lineCode}>
            <Td mono tone="muted">
              {line.lineCode}
            </Td>
            <Td>{line.description}</Td>
            <NumCell
              value={
                line.exposureAmount === null
                  ? "—"
                  : formatMoneyCompact(line.exposureAmount, currency)
              }
              title={
                line.exposureAmount === null
                  ? undefined
                  : formatMoneyFull(line.exposureAmount, currency)
              }
            />
            <NumCell
              value={line.ratePct === null ? "—" : formatPct(line.ratePct)}
            />
            <NumCell
              value={formatMoneyCompact(line.weightedAmount, currency)}
              title={formatMoneyFull(line.weightedAmount, currency)}
            />
          </tr>
        ))}
        {!lines.length ? (
          <tr>
            <Td colSpan={5} tone="muted">
              No line items for this period.
            </Td>
          </tr>
        ) : null}
      </tbody>
    </DenseTable>
  );
}
