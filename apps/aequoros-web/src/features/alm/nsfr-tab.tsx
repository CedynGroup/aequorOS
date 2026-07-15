import { useQuery } from "@tanstack/react-query";

import { Alert, Panel, PanelHeader, Skeleton } from "../../components/ui";
import { riskApi } from "../../lib/api";
import { ErrorPanel } from "../../shared/route-ui";
import type { AlmTabProps } from "./alm-console";
import { LineItemTable } from "./lcr-tab";
import { formatMoneyCompact, formatMoneyFull } from "./shared/format";
import { RatioCard } from "./shared/ratio-card";
import { bogDisplayThresholds, metricThreshold } from "./shared/regulatory";
import { TrendChart } from "./shared/trend-chart";
import { ValidationList } from "./shared/validation-list";

export function NsfrTab({ tenant, bank, period }: AlmTabProps) {
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

  if (dashboard.isLoading) {
    return (
      <div aria-label="Loading NSFR dashboard" className="space-y-3">
        <Skeleton className="h-32" />
        <Skeleton className="h-52" />
      </div>
    );
  }
  if (dashboard.error) return <ErrorPanel error={dashboard.error} />;
  if (!dashboard.data) return null;

  const data = dashboard.data;
  const nsfrMin = metricThreshold(
    latestRun.data,
    "nsfr_pct",
    bogDisplayThresholds.nsfrMinPct,
  );
  const asfLines =
    latestRun.data?.lineItems.filter((item) => item.section === "asf") ?? [];
  const rsfLines =
    latestRun.data?.lineItems.filter((item) => item.section === "rsf") ?? [];

  return (
    <div className="space-y-3">
      <Panel>
        <PanelHeader title="NSFR dashboard" meta={`${bank.name} · ${period.label}`} />
        <div className="space-y-3 p-3">
          <div className="grid gap-2 sm:grid-cols-2">
            <RatioCard
              label="NSFR"
              value={data.metrics.nsfrPct}
              status={data.metrics.nsfrStatus}
              thresholds={[{ label: "Min", valuePct: nsfrMin }]}
            />
            <div className="min-w-0 rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--surface))] p-3">
              <div className="text-xs font-medium uppercase tracking-[0.04em] text-[rgb(var(--muted-foreground))]">
                ASF vs RSF totals
              </div>
              <TotalsBar
                asf={data.metrics.asfTotalGhs}
                rsf={data.metrics.rsfTotalGhs}
                currency={bank.currency}
              />
            </div>
          </div>
        </div>
      </Panel>
      {latestRun.data ? (
        <div className="grid gap-3 xl:grid-cols-2">
          <Panel>
            <PanelHeader
              title="Available stable funding"
              meta="ASF weighted balances from the latest stored baseline run"
            />
            <div className="p-3">
              <LineItemTable
                ariaLabel="ASF line items"
                lines={asfLines}
                currency={bank.currency}
                rateHeader="Weight %"
              />
            </div>
          </Panel>
          <Panel>
            <PanelHeader
              title="Required stable funding"
              meta="RSF weighted balances from the latest stored baseline run"
            />
            <div className="p-3">
              <LineItemTable
                ariaLabel="RSF line items"
                lines={rsfLines}
                currency={bank.currency}
                rateHeader="Weight %"
              />
            </div>
          </Panel>
        </div>
      ) : (
        <Alert title="ASF/RSF line detail requires a stored run">
          Run a baseline liquidity calculation from the LCR dashboard to
          persist ASF and RSF line items; totals above are computed inline.
        </Alert>
      )}
      <Panel>
        <PanelHeader title="NSFR trend" meta="Net stable funding ratio by reporting period" />
        <div className="p-3">
          <TrendChart
            seriesLabel="NSFR"
            points={data.trend.map((point) => ({
              label: point.label,
              value: point.nsfrPct,
              stored: point.stored,
            }))}
            referenceLines={[
              { value: Number(nsfrMin), label: "Min", tone: "danger" },
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

function TotalsBar({
  asf,
  rsf,
  currency,
}: {
  asf: string;
  rsf: string;
  currency: string;
}) {
  const max = Math.max(Number(asf), Number(rsf), 1);
  const rows = [
    { label: "ASF", value: asf, shade: "rgb(var(--primary))" },
    { label: "RSF", value: rsf, shade: "rgba(var(--primary), 0.4)" },
  ];
  return (
    <div className="mt-2 space-y-1.5">
      {rows.map((row) => (
        <div key={row.label} className="flex items-center gap-2 text-[11px]">
          <span className="w-8 text-[rgb(var(--muted-foreground))]">
            {row.label}
          </span>
          <div className="h-3 flex-1 overflow-hidden rounded-sm border border-[rgb(var(--border))]">
            <div
              className="h-full"
              style={{
                width: `${(Math.max(Number(row.value), 0) / max) * 100}%`,
                background: row.shade,
              }}
            />
          </div>
          <span
            className="w-24 text-right font-mono tabular-nums"
            title={formatMoneyFull(row.value, currency)}
          >
            {formatMoneyCompact(row.value, currency)}
          </span>
        </div>
      ))}
    </div>
  );
}
