import { useQuery } from "@tanstack/react-query";

import { Label, Panel, PanelHeader, Skeleton } from "../../components/ui";
import { riskApi } from "../../lib/api";
import { ErrorPanel } from "../../shared/route-ui";
import type { AlmTabProps } from "./alm-console";
import { formatDate, formatMoneyCompact, formatMoneyFull } from "./shared/format";
import { RatioCard } from "./shared/ratio-card";
import { bogDisplayThresholds, metricThreshold } from "./shared/regulatory";
import { TrendChart } from "./shared/trend-chart";
import { ValidationList } from "./shared/validation-list";

export function AlmOverviewTab({ tenant, bank, period, onTab }: AlmTabProps) {
  const liquidity = useQuery({
    queryKey: ["alm-liquidity-dashboard", tenant, bank.id, period.id],
    queryFn: () => riskApi.getLiquidityDashboard(tenant, bank.id, period.id),
  });
  const capital = useQuery({
    queryKey: ["alm-capital-dashboard", tenant, bank.id, period.id],
    queryFn: () => riskApi.getCapitalDashboard(tenant, bank.id, period.id),
  });
  const liquidityRun = useQuery({
    queryKey: [
      "alm-regulatory-run",
      tenant,
      bank.id,
      liquidity.data?.latestRunId,
    ],
    queryFn: () =>
      riskApi.getRegulatoryRun(
        tenant,
        bank.id,
        liquidity.data?.latestRunId ?? "",
      ),
    enabled: Boolean(liquidity.data?.latestRunId),
  });
  const capitalRun = useQuery({
    queryKey: [
      "alm-regulatory-run",
      tenant,
      bank.id,
      capital.data?.latestRunId,
    ],
    queryFn: () =>
      riskApi.getRegulatoryRun(
        tenant,
        bank.id,
        capital.data?.latestRunId ?? "",
      ),
    enabled: Boolean(capital.data?.latestRunId),
  });

  if (liquidity.isLoading || capital.isLoading) {
    return (
      <div aria-label="Loading ALM overview" className="space-y-3">
        <Skeleton className="h-10" />
        <Skeleton className="h-32" />
        <Skeleton className="h-52" />
      </div>
    );
  }
  if (liquidity.error) return <ErrorPanel error={liquidity.error} />;
  if (capital.error) return <ErrorPanel error={capital.error} />;
  if (!liquidity.data || !capital.data) return null;

  const liq = liquidity.data;
  const cap = capital.data;
  const openValidations = [
    ...liq.validations.map((validation) => ({
      ...validation,
      ruleCode: `liquidity · ${validation.ruleCode}`,
    })),
    ...cap.validations.map((validation) => ({
      ...validation,
      ruleCode: `capital · ${validation.ruleCode}`,
    })),
  ].filter(
    (validation) =>
      !validation.passed &&
      (validation.severity === "error" || validation.severity === "warning"),
  );

  return (
    <div className="space-y-3">
      <Panel>
        <div className="flex flex-wrap items-center gap-x-6 gap-y-1 px-3 py-2 text-xs">
          <span className="text-sm font-semibold">{bank.name}</span>
          <span className="text-[rgb(var(--muted-foreground))]">
            {bank.licenseType}
          </span>
          <span className="text-[rgb(var(--muted-foreground))]">
            {period.label} · ends {formatDate(period.periodEnd)}
          </span>
          <span className="text-[rgb(var(--muted-foreground))]">
            {bank.currency}
          </span>
          <span className="text-[rgb(var(--muted-foreground))]">
            Regulator: Bank of Ghana
          </span>
        </div>
      </Panel>
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        <RatioCard
          label="LCR"
          value={liq.metrics.lcrPct}
          status={liq.metrics.lcrStatus}
          thresholds={[
            {
              label: "Min",
              valuePct: metricThreshold(
                liquidityRun.data,
                "lcr_pct",
                bogDisplayThresholds.lcrMinPct,
              ),
            },
          ]}
          meta={
            <span title={formatMoneyFull(liq.metrics.hqlaTotalGhs, bank.currency)}>
              HQLA {formatMoneyCompact(liq.metrics.hqlaTotalGhs, bank.currency)}
            </span>
          }
          onOpen={() => onTab("lcr")}
          openLabel="Open LCR dashboard"
        />
        <RatioCard
          label="NSFR"
          value={liq.metrics.nsfrPct}
          status={liq.metrics.nsfrStatus}
          thresholds={[
            {
              label: "Min",
              valuePct: metricThreshold(
                liquidityRun.data,
                "nsfr_pct",
                bogDisplayThresholds.nsfrMinPct,
              ),
            },
          ]}
          meta={
            <span title={formatMoneyFull(liq.metrics.asfTotalGhs, bank.currency)}>
              ASF {formatMoneyCompact(liq.metrics.asfTotalGhs, bank.currency)}
            </span>
          }
          onOpen={() => onTab("nsfr")}
          openLabel="Open NSFR dashboard"
        />
        <RatioCard
          label="CAR"
          value={cap.metrics.carPct}
          status={cap.metrics.carStatus}
          thresholds={[{ label: "Min", valuePct: cap.buffers.carMinPct }]}
          meta={
            <span title={formatMoneyFull(cap.metrics.totalRwaGhs, bank.currency)}>
              RWA {formatMoneyCompact(cap.metrics.totalRwaGhs, bank.currency)}
            </span>
          }
          onOpen={() => onTab("capital")}
          openLabel="Open capital dashboard"
        />
        <RatioCard
          label="Tier 1"
          value={cap.metrics.tier1RatioPct}
          status={cap.metrics.tier1Status}
          thresholds={[
            {
              label: "Min",
              valuePct: metricThreshold(
                capitalRun.data,
                "tier1_ratio_pct",
                bogDisplayThresholds.tier1MinPct,
              ),
            },
          ]}
          meta={
            <span
              title={formatMoneyFull(
                cap.capitalStructure.tier1CapitalGhs,
                bank.currency,
              )}
            >
              Tier 1{" "}
              {formatMoneyCompact(
                cap.capitalStructure.tier1CapitalGhs,
                bank.currency,
              )}
            </span>
          }
          onOpen={() => onTab("capital")}
          openLabel="Open capital dashboard"
        />
      </div>
      <div className="grid gap-3 xl:grid-cols-2">
        <Panel>
          <PanelHeader title="LCR trend" meta="Liquidity coverage ratio by reporting period" />
          <div className="p-3">
            <TrendChart
              seriesLabel="LCR"
              points={liq.trend.map((point) => ({
                label: point.label,
                value: point.lcrPct,
                stored: point.stored,
              }))}
              referenceLines={[
                {
                  value: Number(
                    metricThreshold(
                      liquidityRun.data,
                      "lcr_pct",
                      bogDisplayThresholds.lcrMinPct,
                    ),
                  ),
                  label: "Min",
                  tone: "danger",
                },
              ]}
            />
          </div>
        </Panel>
        <Panel>
          <PanelHeader title="CAR trend" meta="Capital adequacy ratio by reporting period" />
          <div className="p-3">
            <TrendChart
              seriesLabel="CAR"
              points={cap.trend.map((point) => ({
                label: point.label,
                value: point.carPct,
                stored: point.stored,
              }))}
              referenceLines={[
                {
                  value: Number(cap.buffers.carMinPct),
                  label: "Min",
                  tone: "danger",
                },
                {
                  value: Number(cap.buffers.carEarlyWarningPct),
                  label: "Early warning",
                  tone: "warning",
                },
              ]}
            />
          </div>
        </Panel>
      </div>
      <Panel>
        <PanelHeader
          title="Open validations"
          meta="Failed error and warning rules across liquidity and capital"
        />
        <div className="p-3">
          <Label className="sr-only">Open validation rules</Label>
          <ValidationList
            validations={openValidations}
            empty="All liquidity and capital validation rules pass for this period."
          />
        </div>
      </Panel>
    </div>
  );
}
