"use client";

import PageContainer from "@/components/ui/PageContainer";
import { ArrowUpRight } from "lucide-react";
import { PermissionLink } from "@/components/ui/DisabledWithReason";
import type { LiquidityDashboardLineRead } from "@aequoros/risk-service-api";
import PageHeader from "@/components/ui/PageHeader";
import RatioGauge from "@/components/ui/RatioGauge";
import KpiStat from "@/components/ui/KpiStat";
import LimitBar from "@/components/ui/LimitBar";
import ChartFrame from "@/components/ui/ChartFrame";
import SectionCard from "@/components/ui/SectionCard";
import StatusPill from "@/components/ui/StatusPill";
import Sparkline from "@/components/ui/Sparkline";
import ValidationList from "@/components/ui/ValidationList";
import QueryBoundary from "@/components/ui/QueryBoundary";
import DataTable, { type Column } from "@/components/ui/DataTable";
import RatioTrendChart from "@/components/liquidity/charts/RatioTrendChart";
import NetOutflowChart from "@/components/liquidity/charts/NetOutflowChart";
import SdiLiquidityView from "@/components/liquidity/SdiLiquidityView";
import {
  LandingInsightStrip,
  useKpiExplain,
} from "@/components/bi/InsightStrip";
import { runComputedAt, runThresholds } from "@/components/liquidity/runData";
import { useBankContext } from "@/components/shell/BankContext";
import LiveEngineNote from "@/components/live/LiveEngineNote";
import {
  useCfpSummary,
  useEwiDashboard,
  useLiquidityDashboard,
  useRegulatoryRun,
} from "@/lib/api/hooks";
import { num, statusTone, numOrNull, formatFigure } from "@/lib/api/values";
import {
  currencyCode,
  fmtCurrency,
  fmtPct,
  regShort,
  centralBankName,
} from "@/lib/format";
import { hrefAccess } from "@/lib/modules";

type LineRow = {
  item: string;
  balanceGHS: number | null;
  ratePct: number | null;
  weightedGHS: number;
  isTotal?: boolean;
};

function toRow(line: LiquidityDashboardLineRead): LineRow {
  return {
    item: line.description,
    balanceGHS: line.exposureAmount === null ? null : num(line.exposureAmount),
    ratePct: line.ratePct === null ? null : num(line.ratePct),
    weightedGHS: num(line.weightedAmount),
  };
}

function lineColumns(
  rateHeader: string,
  weightedHeader: string,
): Column<LineRow>[] {
  return [
    { key: "item", header: "Category", render: (r) => r.item, width: "46%" },
    {
      key: "balance",
      header: `Balance (${currencyCode()})`,
      numeric: true,
      render: (r) => (r.balanceGHS === null ? "—" : fmtCurrency(r.balanceGHS)),
    },
    {
      key: "rate",
      header: rateHeader,
      numeric: true,
      render: (r) => (r.ratePct === null ? "—" : `${r.ratePct.toFixed(0)}%`),
    },
    {
      key: "weighted",
      header: weightedHeader,
      numeric: true,
      render: (r) => fmtCurrency(r.weightedGHS),
    },
  ];
}

function escalationTone(
  escalation: string | undefined,
): "success" | "amber" | "critical" | "slate" {
  if (escalation === "cfp_active" || escalation === "escalation")
    return "critical";
  if (escalation === "heightened_monitoring") return "amber";
  return escalation ? "success" : "slate";
}

function escalationLabel(escalation: string | undefined): string {
  if (escalation === "cfp_active") return "CFP active";
  if (escalation === "escalation") return "Escalation";
  if (escalation === "heightened_monitoring") return "Heightened monitoring";
  return escalation === "normal" ? "Business as usual" : "Awaiting EWI";
}

export default function LiquidityCockpit() {
  const { bank, moduleScope } = useBankContext();
  const bankId = bank?.id;
  const isSdi = moduleScope.institutionClass === "sdi";
  const aggregatedBankId = moduleScope.liquidityAggregatedView
    ? bankId
    : undefined;
  const confidentialBankId = moduleScope.liquidityConfidentialView
    ? bankId
    : undefined;

  const dashboard = useLiquidityDashboard(isSdi ? undefined : aggregatedBankId);
  const latestRun = useRegulatoryRun(
    confidentialBankId,
    dashboard.data?.latestRunId,
  );
  const ewis = useEwiDashboard(isSdi ? undefined : confidentialBankId);
  const cfp = useCfpSummary(isSdi ? undefined : confidentialBankId);

  const data = dashboard.data;
  const run = latestRun.data;
  // The reporting date every BI surface on this page speaks about: the period
  // the figures on screen were computed for, never today's date.
  const asOf = data?.period.periodEnd ?? null;
  const explain = useKpiExplain(bankId, asOf);

  const thresholds = runThresholds(
    moduleScope.liquidityConfidentialView ? run : undefined,
  );
  const lcrMin = thresholds["lcr_min"] ?? null;
  const lcrRedFloor = thresholds["lcr_amber_floor"];
  const nsfrMin = thresholds["nsfr_min"] ?? null;
  const nsfrRedFloor = thresholds["nsfr_amber_floor"] ?? nsfrMin;

  const outflowRows = (data?.outflows ?? []).map(toRow);
  const inflowRows = (data?.inflows ?? []).map(toRow);
  const lcrValue = numOrNull(data?.metrics.lcrPct);
  const nsfrValue = numOrNull(data?.metrics.nsfrPct);
  const netOutflows = numOrNull(data?.metrics.netOutflows30dGhs);
  const asfTotal = numOrNull(data?.metrics.asfTotalGhs);
  const rsfTotal = numOrNull(data?.metrics.rsfTotalGhs);
  const totalOutflows =
    netOutflows === null
      ? null
      : outflowRows.reduce((s, r) => s + r.weightedGHS, 0);
  // Identity: net outflows = total outflows − capped inflows.
  const cappedInflows =
    totalOutflows === null || netOutflows === null
      ? null
      : totalOutflows - netOutflows;
  const capNote = data?.validations.find(
    (v) => v.ruleCode === "inflow_cap_applied",
  );
  const hasInlineTrendPoints = (data?.trend ?? []).some((p) => !p.stored);

  const hqlaTotal = numOrNull(data?.metrics.hqlaTotalGhs);
  const lcrTrend = (data?.trend ?? []).map((p) => numOrNull(p.lcrPct));
  const nsfrTrend = (data?.trend ?? []).map((p) => numOrNull(p.nsfrPct));
  const periodDelta = (series: (number | null)[]): number | undefined => {
    const current = series.at(-1);
    const previous = series.at(-2);
    return current == null || previous == null ? undefined : current - previous;
  };
  const lcrDelta = periodDelta(lcrTrend);
  const nsfrDelta = periodDelta(nsfrTrend);
  const hqlaRows = data?.hqlaComposition ?? [];
  const largestHqla = hqlaRows.reduce<LiquidityDashboardLineRead | null>(
    (largest, line) =>
      largest === null || num(line.weightedAmount) > num(largest.weightedAmount)
        ? line
        : largest,
    null,
  );
  const largestHqlaShare =
    largestHqla && hqlaTotal !== null && hqlaTotal > 0
      ? (num(largestHqla.weightedAmount) / hqlaTotal) * 100
      : null;

  const nsfrSurplus =
    asfTotal === null || rsfTotal === null ? null : asfTotal - rsfTotal;
  const ewi = ewis.data;
  const actionIndicators =
    ewi?.indicators.filter((item) => item.status === "action") ?? [];
  const watchIndicators =
    ewi?.indicators.filter((item) => item.status === "watch") ?? [];
  const approvedCfp = cfp.data?.approved ?? null;
  const fundingOptions = approvedCfp?.content.fundingOptions ?? [];
  const actionPlans = approvedCfp?.content.actionPlans ?? [];

  const computedAt = runComputedAt(run);
  const provenance = data ? (
    <span>Computed from current positions and the active parameter set</span>
  ) : undefined;

  if (isSdi) {
    return <SdiLiquidityView bankId={bankId} />;
  }

  return (
    <>
      <PageHeader
        eyebrow="Liquidity"
        title="Liquidity Cockpit"
        action={
          data ? (
            <LiveEngineNote live={data.live} stored={data.stored} />
          ) : undefined
        }
      />

      <QueryBoundary
        isLoading={dashboard.isLoading}
        error={dashboard.error}
        onRetry={() => dashboard.refetch()}
      >
        {data && (
          <PageContainer className="py-6 space-y-6">
            <LandingInsightStrip bankId={bankId} asOf={asOf} />

            <SectionCard
              title="Liquidity posture"
              subtitle="Basel reference ratios against governed monitoring thresholds, buffer concentration, early-warning state, and contingency readiness."
              computedAt={computedAt}
              footer={provenance}
            >
              <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-5 gap-4">
                {lcrMin !== null && (
                  <KpiStat
                    label="LCR headroom"
                    value={formatFigure(
                      lcrValue,
                      (value) => `${(value - lcrMin).toFixed(1)} pp`,
                    )}
                    status={
                      data.metrics.lcrStatus === "red"
                        ? "crit"
                        : data.metrics.lcrStatus === "amber"
                          ? "warn"
                          : data.metrics.lcrStatus === "green"
                            ? "ok"
                            : undefined
                    }
                    hint={
                      hqlaTotal === null || netOutflows === null
                        ? "Unavailable"
                        : `${fmtCurrency(hqlaTotal - netOutflows * (lcrMin / 100))} above monitoring threshold`
                    }
                  />
                )}
                <KpiStat
                  label="NSFR funding surplus"
                  value={formatFigure(nsfrSurplus, fmtCurrency)}
                  status={
                    nsfrSurplus === null
                      ? undefined
                      : nsfrSurplus < 0
                        ? "crit"
                        : "ok"
                  }
                  hint={
                    nsfrMin !== null && nsfrValue !== null
                      ? `${(nsfrValue - nsfrMin).toFixed(1)} pp above monitoring threshold`
                      : undefined
                  }
                />
                <KpiStat
                  label="Largest HQLA concentration"
                  value={formatFigure(hqlaTotal, () =>
                    largestHqlaShare === null
                      ? "—"
                      : fmtPct(largestHqlaShare, 1),
                  )}
                  status={
                    largestHqlaShare === null
                      ? undefined
                      : largestHqlaShare >= 75
                        ? "warn"
                        : "ok"
                  }
                  hint={
                    hqlaTotal === null
                      ? "Unavailable"
                      : (largestHqla?.description ?? "No HQLA instruments")
                  }
                />
                {moduleScope.liquidityConfidentialView && ewi && (
                  <KpiStat
                    label="Early-warning posture"
                    value={
                      actionIndicators.length > 0
                        ? `${actionIndicators.length} action`
                        : watchIndicators.length > 0
                          ? `${watchIndicators.length} watch`
                          : "Normal"
                    }
                    status={
                      actionIndicators.length > 0
                        ? "crit"
                        : watchIndicators.length > 0
                          ? "warn"
                          : "ok"
                    }
                    hint={
                      ewi
                        ? escalationLabel(ewi.escalationState)
                        : "EWI view is not available yet"
                    }
                  />
                )}
                {moduleScope.liquidityConfidentialView && cfp.data && (
                  <KpiStat
                    label="CFP readiness"
                    value={
                      approvedCfp
                        ? `v${approvedCfp.version}`
                        : "No approved plan"
                    }
                    status={
                      approvedCfp
                        ? approvedCfp.approvalOverdue
                          ? "warn"
                          : "ok"
                        : "warn"
                    }
                    hint={
                      approvedCfp
                        ? `${fundingOptions.length} funding options · ${actionPlans.length} actions`
                        : "Approval is required before activation"
                    }
                  />
                )}
              </div>
            </SectionCard>

            <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
              {moduleScope.liquidityConfidentialView && ewi && cfp.data && (
                <SectionCard
                  className="xl:col-span-2"
                  title="Escalation and contingency readiness"
                  subtitle="EWI classifications are calculated server-side; the CFP remains a Board-owned activation control."
                >
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                    <div className="border-r-0 md:border-r md:border-border-light md:pr-5">
                      <div className="flex items-center justify-between gap-3">
                        <p className="text-body font-medium text-navy">
                          Early-warning indicators
                        </p>
                        <StatusPill tone={escalationTone(ewi?.escalationState)}>
                          {escalationLabel(ewi?.escalationState)}
                        </StatusPill>
                      </div>
                      <p className="mt-2 text-caption text-slate leading-relaxed">
                        {actionIndicators.length} action trigger
                        {actionIndicators.length === 1 ? "" : "s"} ·{" "}
                        {watchIndicators.length} watch trigger
                        {watchIndicators.length === 1 ? "" : "s"}.
                      </p>
                    </div>
                    <div>
                      <div className="flex items-center justify-between gap-3">
                        <p className="text-body font-medium text-navy">
                          Contingency Funding Plan
                        </p>
                        <StatusPill
                          tone={
                            approvedCfp
                              ? approvedCfp.approvalOverdue
                                ? "amber"
                                : "success"
                              : "slate"
                          }
                        >
                          {approvedCfp
                            ? approvedCfp.approvalOverdue
                              ? "review overdue"
                              : "approved"
                            : "not approved"}
                        </StatusPill>
                      </div>
                      <p className="mt-2 text-caption text-slate leading-relaxed">
                        {approvedCfp
                          ? `Plan v${approvedCfp.version} has ${fundingOptions.length} documented funding option${fundingOptions.length === 1 ? "" : "s"} and ${actionPlans.length} action${actionPlans.length === 1 ? "" : "s"}.`
                          : "No Board-approved plan is available for activation."}
                      </p>
                    </div>
                  </div>
                </SectionCard>
              )}
              <SectionCard
                title="Control workspace"
                subtitle="Move from current posture to the relevant control without losing context."
              >
                <div className="space-y-2">
                  {[
                    {
                      href: "/liquidity/buffer",
                      label: "Buffer concentration and haircuts",
                      className: "border-b border-border-light pb-2",
                    },
                    {
                      href: "/liquidity/monitoring",
                      label: "Thresholds and maturity monitoring",
                      className: "border-b border-border-light py-2",
                    },
                    {
                      href: "/liquidity/cfp",
                      label: "CFP actions and activation log",
                      className: "pt-2",
                    },
                  ].map((control) => {
                    const access = hrefAccess(control.href, moduleScope);
                    if (access.state === "hidden") return null;
                    return (
                      <PermissionLink
                        key={control.href}
                        href={control.href}
                        reason={
                          access.state === "disabled"
                            ? access.reason
                            : undefined
                        }
                        wrapperClassName="w-full"
                        className={`flex w-full items-center justify-between gap-3 text-body text-navy hover:text-action ${control.className}`}
                        disabledClassName="text-slate-light hover:text-slate-light"
                      >
                        {control.label}
                        <ArrowUpRight size={14} aria-hidden />
                      </PermissionLink>
                    );
                  })}
                </div>
              </SectionCard>
            </div>

            {/* Headline gauges + component KPIs */}
            <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
              <div className="lg:col-span-2">
                {lcrMin !== null ? (
                  <RatioGauge
                    label="Liquidity Coverage Ratio"
                    value={lcrValue}
                    threshold={lcrMin}
                    internalBuffer={lcrRedFloor}
                    bufferLabel="Red floor"
                    status={statusTone(data.metrics.lcrStatus)}
                    decimals={2}
                  />
                ) : (
                  <KpiStat
                    label="Liquidity Coverage Ratio"
                    value={formatFigure(data.metrics.lcrPct, (value) =>
                      fmtPct(value, 2),
                    )}
                    explain={explain.explainFor("lcr_pct")}
                  />
                )}
              </div>
              <KpiStat
                label="HQLA stock"
                value={formatFigure(hqlaTotal, fmtCurrency)}
                status={
                  data.metrics.lcrStatus === "red"
                    ? "crit"
                    : data.metrics.lcrStatus === "amber"
                      ? "warn"
                      : data.metrics.lcrStatus === "green"
                        ? "ok"
                        : undefined
                }
                delta={lcrDelta}
                deltaSuffix=" pts LCR"
                hint="Post-haircut weighted"
                explain={explain.explainFor("hqla_total_ghs")}
              />
              <KpiStat
                label="30-day net outflows"
                value={formatFigure(
                  data.metrics.netOutflows30dGhs,
                  fmtCurrency,
                )}
                hint="Outflows − capped inflows"
                explain={explain.explainFor("net_outflows_30d_ghs")}
              />
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
              <div className="lg:col-span-2">
                {nsfrMin !== null ? (
                  <RatioGauge
                    label="Net Stable Funding Ratio"
                    value={nsfrValue}
                    threshold={nsfrMin}
                    status={statusTone(data.metrics.nsfrStatus)}
                    decimals={2}
                  />
                ) : (
                  <KpiStat
                    label="Net Stable Funding Ratio"
                    value={formatFigure(data.metrics.nsfrPct, (value) =>
                      fmtPct(value, 2),
                    )}
                    explain={explain.explainFor("nsfr_pct")}
                  />
                )}
              </div>
              <KpiStat
                label="Available stable funding"
                value={formatFigure(data.metrics.asfTotalGhs, fmtCurrency)}
                delta={nsfrDelta}
                deltaSuffix=" pts NSFR"
                hint="Liability-side weighting"
                explain={explain.explainFor("asf_total_ghs")}
              />
              <KpiStat
                label="Required stable funding"
                value={formatFigure(data.metrics.rsfTotalGhs, fmtCurrency)}
                hint="Asset-side weighting"
                explain={explain.explainFor("rsf_total_ghs")}
              />
            </div>

            {/* Regulatory floors — LCR & NSFR are floor limits (direction above) */}
            {lcrMin !== null &&
              lcrRedFloor !== undefined &&
              nsfrMin !== null &&
              nsfrRedFloor !== null && (
                <SectionCard
                  title="Liquidity floors"
                  subtitle={`Basel LCR/NSFR minimums from the active parameter set — ${centralBankName()} has published no LCR requirement and none on NSFR (green ≥ minimum, amber down to the red floor)`}
                  computedAt={computedAt}
                  footer={provenance}
                >
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-x-10 gap-y-5">
                    <LimitBar
                      label={
                        <span className="inline-flex items-center gap-2">
                          LCR
                          <Sparkline data={lcrTrend} width={64} height={16} />
                        </span>
                      }
                      value={lcrValue}
                      limit={lcrRedFloor}
                      warnAt={lcrMin}
                      direction="above"
                      unit="%"
                      limitLabel="Red floor"
                      warnLabel="Basel minimum"
                      format={(v) => v.toFixed(1)}
                    />
                    <LimitBar
                      label={
                        <span className="inline-flex items-center gap-2">
                          NSFR
                          <Sparkline data={nsfrTrend} width={64} height={16} />
                        </span>
                      }
                      value={nsfrValue}
                      limit={nsfrRedFloor}
                      warnAt={nsfrMin}
                      direction="above"
                      unit="%"
                      limitLabel={
                        nsfrRedFloor === nsfrMin ? "Basel minimum" : "Red floor"
                      }
                      warnLabel="Basel minimum"
                      format={(v) => v.toFixed(1)}
                    />
                  </div>
                </SectionCard>
              )}

            {/* Trend + net-outflow decomposition */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              <ChartFrame
                className="lg:col-span-2"
                title="LCR & NSFR — reporting-period trend"
                subtitle={`Ratios across ${data.trend.length} reporting periods`}
                height={260}
                actions={
                  lcrMin !== null ? (
                    <StatusPill tone="success">
                      LCR above threshold{" "}
                      {
                        data.trend.filter((p) => {
                          const value = numOrNull(p.lcrPct);
                          return value !== null && value >= lcrMin;
                        }).length
                      }{" "}
                      of {data.trend.length}
                    </StatusPill>
                  ) : undefined
                }
                footer={
                  hasInlineTrendPoints ? (
                    <span>
                      Hollow points are live computations — they solidify once
                      those periods’ results are stored.
                    </span>
                  ) : (
                    <span>All trend points come from stored results.</span>
                  )
                }
              >
                <RatioTrendChart
                  data={data.trend.map((p) => ({
                    label: p.label,
                    primary: numOrNull(p.lcrPct),
                    secondary: numOrNull(p.nsfrPct),
                    stored: p.stored,
                  }))}
                  threshold={lcrMin}
                  thresholdLabel="Min"
                  redFloor={lcrRedFloor}
                  redFloorLabel="Red floor"
                  primaryLabel="LCR"
                  secondaryLabel="NSFR"
                  height={260}
                />
              </ChartFrame>

              <ChartFrame
                title="Net-outflow decomposition"
                subtitle="Weighted 30-day outflows by category vs capped inflows"
                height={260}
                footer={capNote ? <span>{capNote.message}</span> : undefined}
              >
                <NetOutflowChart
                  outflows={outflowRows.map((r) => ({
                    name: r.item,
                    weighted: r.weightedGHS,
                  }))}
                  cappedInflows={cappedInflows}
                  netOutflows={netOutflows}
                  height={260}
                />
              </ChartFrame>
            </div>

            {/* Outflow & inflow tables */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <SectionCard
                title="Cash outflows"
                subtitle="30-day stressed runoff per Basel III run-off weights (BCBS 238) — no run-off table is prescribed locally"
                noPadding
                computedAt={computedAt}
                footer={provenance}
              >
                <DataTable
                  columns={lineColumns("Runoff %", "Stressed outflow")}
                  rows={[
                    ...outflowRows,
                    ...(totalOutflows === null
                      ? []
                      : [
                          {
                            item: "TOTAL CASH OUTFLOWS",
                            balanceGHS: outflowRows.reduce(
                              (s, r) => s + (r.balanceGHS ?? 0),
                              0,
                            ),
                            ratePct: null,
                            weightedGHS: totalOutflows,
                            isTotal: true,
                          },
                        ]),
                  ]}
                  totalsRowMatcher={(r) => Boolean(r.isTotal)}
                />
              </SectionCard>

              <SectionCard
                title="Cash inflows"
                subtitle="Capped at 75% of outflows per Basel III"
                noPadding
                computedAt={computedAt}
                footer={capNote ? <span>{capNote.message}</span> : provenance}
              >
                <DataTable
                  columns={lineColumns("Inflow %", "Weighted inflow")}
                  rows={[
                    ...inflowRows,
                    ...(netOutflows === null
                      ? []
                      : [
                          {
                            item: "GROSS INFLOWS",
                            balanceGHS: inflowRows.reduce(
                              (s, r) => s + (r.balanceGHS ?? 0),
                              0,
                            ),
                            ratePct: null,
                            weightedGHS: inflowRows.reduce(
                              (s, r) => s + r.weightedGHS,
                              0,
                            ),
                            isTotal: true,
                          },
                        ]),
                    ...(cappedInflows === null
                      ? []
                      : [
                          {
                            item: "CAPPED INFLOWS (min of gross, 75% of outflows)",
                            balanceGHS: null,
                            ratePct: null,
                            weightedGHS: cappedInflows,
                            isTotal: true,
                          },
                        ]),
                  ]}
                  totalsRowMatcher={(r) => Boolean(r.isTotal)}
                />
              </SectionCard>
            </div>

            {/* Validations */}
            <SectionCard
              title="Validations"
              subtitle="Basel reference ratios assessed against governed monitoring thresholds"
              noPadding
              computedAt={computedAt}
              footer={provenance}
            >
              <ValidationList validations={data.validations} />
            </SectionCard>

            {/* Compliance summary line */}
            <p className="text-caption text-slate flex items-center gap-2 flex-wrap">
              Net outflows = Outflows{" "}
              <span className="font-mono text-navy">
                {formatFigure(totalOutflows, fmtCurrency)}
              </span>{" "}
              − min(Gross inflows, 75% × Outflows){" "}
              <span className="font-mono text-navy">
                {formatFigure(cappedInflows, fmtCurrency)}
              </span>{" "}
              ={" "}
              <span className="font-mono font-medium text-navy">
                {formatFigure(data.metrics.netOutflows30dGhs, fmtCurrency)}
              </span>
              . LCR = HQLA{" "}
              <span className="font-mono text-navy">
                {formatFigure(hqlaTotal, fmtCurrency)}
              </span>{" "}
              / Net outflows ={" "}
              <span className="font-mono font-medium text-navy">
                {formatFigure(data.metrics.lcrPct, (value) => fmtPct(value, 2))}
              </span>
              .
            </p>

            {explain.drawer}
          </PageContainer>
        )}
      </QueryBoundary>
    </>
  );
}
