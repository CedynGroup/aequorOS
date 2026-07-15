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
import { formatDecimal } from "../../lib/money";
import { ErrorPanel } from "../../shared/route-ui";
import type { AlmTabProps } from "./alm-console";
import { CompositionBar } from "./shared/composition-bar";
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

export function AlmCapitalTab({ tenant, bank, period }: AlmTabProps) {
  const dashboard = useQuery({
    queryKey: ["alm-capital-dashboard", tenant, bank.id, period.id],
    queryFn: () => riskApi.getCapitalDashboard(tenant, bank.id, period.id),
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
  const runBaseline = useRunBaseline(tenant, bank.id, period.id, "capital");

  if (dashboard.isLoading) {
    return (
      <div aria-label="Loading capital dashboard" className="space-y-3">
        <Skeleton className="h-32" />
        <Skeleton className="h-52" />
      </div>
    );
  }
  if (dashboard.error) return <ErrorPanel error={dashboard.error} />;
  if (!dashboard.data) return null;

  const data = dashboard.data;
  const structure = data.capitalStructure;

  return (
    <div className="space-y-3">
      <Panel>
        <PanelHeader
          title="Basel capital dashboard"
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
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
            <RatioCard
              label="CAR"
              value={data.metrics.carPct}
              status={data.metrics.carStatus}
              thresholds={[
                { label: "Min", valuePct: data.buffers.carMinPct },
                {
                  label: "Early warning",
                  valuePct: data.buffers.carEarlyWarningPct,
                },
                { label: "Critical", valuePct: data.buffers.carCriticalPct },
              ]}
            />
            <RatioCard
              label="Tier 1"
              value={data.metrics.tier1RatioPct}
              status={data.metrics.tier1Status}
              thresholds={[
                {
                  label: "Min",
                  valuePct: metricThreshold(
                    latestRun.data,
                    "tier1_ratio_pct",
                    bogDisplayThresholds.tier1MinPct,
                  ),
                },
              ]}
            />
            <RatioCard
              label="CET1"
              value={data.metrics.cet1RatioPct}
              status={data.metrics.cet1Status}
              thresholds={[
                {
                  label: "Min",
                  valuePct: metricThreshold(
                    latestRun.data,
                    "cet1_ratio_pct",
                    bogDisplayThresholds.cet1MinPct,
                  ),
                },
              ]}
            />
            <RatioCard
              label="Leverage"
              value={data.metrics.leverageRatioPct}
              status={data.metrics.leverageStatus}
              thresholds={[
                {
                  label: "Min",
                  valuePct: metricThreshold(
                    latestRun.data,
                    "leverage_ratio_pct",
                    bogDisplayThresholds.leverageMinPct,
                  ),
                },
              ]}
            />
          </div>
        </div>
      </Panel>
      <div className="grid gap-3 xl:grid-cols-2">
        <Panel>
          <PanelHeader
            title="CAR buffers"
            meta={data.buffers.carEarlyWarningLabel}
          />
          <div className="p-3">
            <dl className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-1.5 text-xs">
              <BufferRow label="Regulatory minimum" value={formatPct(data.buffers.carMinPct)} />
              <BufferRow
                label="Early warning"
                value={formatPct(data.buffers.carEarlyWarningPct)}
              />
              <BufferRow label="Critical" value={formatPct(data.buffers.carCriticalPct)} />
              <BufferRow label="Current CAR" value={formatPct(data.buffers.currentCarPct)} />
              <BufferRow
                label="Headroom above minimum"
                value={`${formatDecimal(data.buffers.headroomPp, 1)} pp`}
              />
            </dl>
          </div>
        </Panel>
        <Panel>
          <PanelHeader
            title="RWA composition"
            meta={
              <span title={formatMoneyFull(data.rwaComposition.totalRwaGhs, bank.currency)}>
                Total RWA{" "}
                {formatMoneyCompact(data.rwaComposition.totalRwaGhs, bank.currency)}
              </span>
            }
          />
          <div className="p-3">
            <CompositionBar
              ariaLabel="RWA composition"
              currency={bank.currency}
              segments={[
                { label: "Credit", value: data.rwaComposition.creditRwaGhs },
                { label: "Market", value: data.rwaComposition.marketRwaGhs },
                {
                  label: "Operational",
                  value: data.rwaComposition.operationalRwaGhs,
                },
              ]}
            />
          </div>
        </Panel>
      </div>
      <Panel>
        <PanelHeader title="Capital structure" meta="Qualifying capital by tier" />
        <div className="grid gap-2 p-3 sm:grid-cols-2 xl:grid-cols-4">
          <StructureTile
            label="CET1 capital"
            value={structure.cet1CapitalGhs}
            currency={bank.currency}
          />
          <StructureTile
            label="AT1 capital"
            value={structure.at1CapitalGhs}
            currency={bank.currency}
          />
          <StructureTile
            label="Tier 2 capital"
            value={structure.tier2CapitalGhs}
            currency={bank.currency}
          />
          <StructureTile
            label="Total capital"
            value={structure.totalCapitalGhs}
            currency={bank.currency}
          />
        </div>
      </Panel>
      <Panel>
        <PanelHeader title="CAR trend" meta="Capital adequacy ratio by reporting period" />
        <div className="p-3">
          <TrendChart
            seriesLabel="CAR"
            points={data.trend.map((point) => ({
              label: point.label,
              value: point.carPct,
              stored: point.stored,
            }))}
            referenceLines={[
              {
                value: Number(data.buffers.carMinPct),
                label: "Min",
                tone: "danger",
              },
              {
                value: Number(data.buffers.carEarlyWarningPct),
                label: "Early warning",
                tone: "warning",
              },
            ]}
          />
        </div>
      </Panel>
      <Panel>
        <PanelHeader title="Validations" meta="Regulatory rule evaluation for this period" />
        <div className="p-3">
          <ValidationList validations={data.validations} />
        </div>
      </Panel>
    </div>
  );
}

function BufferRow({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="text-[rgb(var(--muted-foreground))]">{label}</dt>
      <dd className="m-0 text-right font-mono tabular-nums">{value}</dd>
    </>
  );
}

function StructureTile({
  label,
  value,
  currency,
}: {
  label: string;
  value: string;
  currency: string;
}) {
  return (
    <div className="min-w-0 rounded-md border border-[rgb(var(--border))] p-3">
      <div className="text-xs text-[rgb(var(--muted-foreground))]">{label}</div>
      <div
        className="mt-1 truncate font-mono text-lg font-semibold tabular-nums"
        title={formatMoneyFull(value, currency)}
      >
        {formatMoneyCompact(value, currency)}
      </div>
    </div>
  );
}
