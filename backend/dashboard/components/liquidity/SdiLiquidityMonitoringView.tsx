"use client";

import PageContainer from "@/components/ui/PageContainer";
import PageHeader from "@/components/ui/PageHeader";
import { useModuleScope } from "@/components/shell/BankContext";
import KpiStat from "@/components/ui/KpiStat";
import QueryBoundary from "@/components/ui/QueryBoundary";
import SectionCard from "@/components/ui/SectionCard";
import StatusPill from "@/components/ui/StatusPill";
import DataTable, { type Column } from "@/components/ui/DataTable";
import {
  useLiquidityMonitoring,
  useSdiLiquidityPosition,
  type SdiFundingProvider,
  type SdiLiquidityRatio,
  type SdiMaturityBucket,
} from "@/components/basel/sdiHooks";
import { useLiquidityDashboard } from "@/lib/api/hooks";
import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import { seriesColor, useChartTokens } from "@/components/bi/echartsTheme";
import {
  axisTooltip,
  BAR_SERIES_BASE,
  itemTooltip,
} from "@/lib/echartsOptions";
import { num, numOrNull, formatFigure } from "@/lib/api/values";
import { fmtCurrency, fmtCurrencySigned, fmtPct } from "@/lib/format";

function ratioTone(status: SdiLiquidityRatio["status"]) {
  return status === "below_minimum"
    ? "breach"
    : status === "ok"
      ? "compliant"
      : "pending";
}

function metricStatus(value: number | null) {
  return value === null ? "warn" : value < 0 ? "crit" : "ok";
}

const providerColumns: Column<SdiFundingProvider>[] = [
  {
    key: "name",
    header: "Funding provider / connected group",
    render: (row) => row.name,
  },
  {
    key: "deposit",
    header: "Deposit funding",
    numeric: true,
    render: (row) => fmtCurrency(num(row.deposit_ghs)),
  },
  {
    key: "share",
    header: "Share of deposits",
    numeric: true,
    render: (row) =>
      row.pct_total_deposits === null
        ? "—"
        : fmtPct(num(row.pct_total_deposits), 2),
  },
  {
    key: "related",
    header: "Relationship",
    render: (row) =>
      row.related ? (
        <StatusPill tone="amber">Related</StatusPill>
      ) : (
        <span className="text-slate">Independent</span>
      ),
  },
];

const ratioColumns: Column<SdiLiquidityRatio>[] = [
  { key: "label", header: "LMTD ratio", render: (row) => row.label },
  {
    key: "value",
    header: "Actual",
    numeric: true,
    render: (row) =>
      row.value_pct === null ? "—" : fmtPct(num(row.value_pct), 1),
  },
  {
    key: "threshold",
    header: "Minimum",
    numeric: true,
    render: (row) => fmtPct(num(row.threshold_pct), 1),
  },
  {
    key: "buffer",
    header: "Buffer",
    numeric: true,
    render: (row) =>
      row.value_pct === null
        ? "—"
        : `${num(row.value_pct) - num(row.threshold_pct) >= 0 ? "+" : ""}${(
            num(row.value_pct) - num(row.threshold_pct)
          ).toFixed(1)} pp`,
  },
  {
    key: "status",
    header: "Status",
    render: (row) => (
      <StatusPill tone={ratioTone(row.status)}>
        {row.status.replace("_", " ")}
      </StatusPill>
    ),
  },
];

const maturityColumns: Column<SdiMaturityBucket>[] = [
  { key: "label", header: "Maturity bucket", render: (row) => row.label },
  {
    key: "net",
    header: "Net mismatch",
    numeric: true,
    render: (row) => fmtCurrencySigned(num(row.net_mismatch_ghs)),
  },
  {
    key: "cumulative",
    header: "Cumulative mismatch",
    numeric: true,
    render: (row) =>
      row.cumulative_mismatch_ghs === null
        ? "—"
        : fmtCurrencySigned(num(row.cumulative_mismatch_ghs)),
  },
];

export default function SdiLiquidityMonitoringView({
  bankId,
  institutionClass,
}: {
  bankId: string | undefined;
  institutionClass: string | null;
}) {
  const isSdi = institutionClass === "sdi";
  const scope = useModuleScope();
  const monitoring = useLiquidityMonitoring(bankId);
  const sdiLiquidity = useSdiLiquidityPosition(isSdi ? bankId : undefined);
  const baselLiquidity = useLiquidityDashboard(
    !isSdi && scope.liquidityAggregatedView ? bankId : undefined,
  );
  const data = monitoring.data;
  const sdiData = sdiLiquidity.data;
  const baselData = baselLiquidity.data;
  const concentration = data?.funding_concentration;
  const capacity = data?.counterbalancing_capacity;
  // A provider whose share of deposits is not computable is left OUT of the
  // concentration chart rather than plotted at 0% — a zero bar reads as "this
  // provider concentrates nothing", which is the opposite of "we do not know".
  const providerShares = (concentration?.providers ?? []).map((provider) => ({
    name: provider.name,
    share: numOrNull(provider.pct_total_deposits),
  }));
  const providerChart = providerShares.filter(
    (provider): provider is { name: string; share: number } =>
      provider.share !== null,
  );
  const providersWithoutShare = providerShares.length - providerChart.length;
  const ratioBuffers = (sdiData?.ratios ?? [])
    .filter((ratio) => ratio.value_pct !== null)
    .map((ratio) => ({
      ...ratio,
      buffer: num(ratio.value_pct!) - num(ratio.threshold_pct),
    }));
  const worstRatio = ratioBuffers.reduce<(typeof ratioBuffers)[number] | null>(
    (worst, ratio) =>
      worst === null || ratio.buffer < worst.buffer ? ratio : worst,
    null,
  );
  const primaryReserve = sdiData?.reserves.find(
    (reserve) => reserve.code === "primary_liquidity_reserve",
  );
  const primaryReserveBuffer =
    primaryReserve?.value_pct === null || primaryReserve === undefined
      ? null
      : num(primaryReserve.value_pct) - num(primaryReserve.threshold_pct);
  const firstCumulativeDeficit = (data?.maturity_ladder ?? []).find(
    (bucket) =>
      bucket.cumulative_mismatch_ghs !== null &&
      num(bucket.cumulative_mismatch_ghs) < 0,
  );
  // "None" is only an answer when there was something to look at. A ladder with
  // no computable cumulative position must not report a clean bill of health.
  const cumulativeComputable = (data?.maturity_ladder ?? []).some(
    (bucket) => bucket.cumulative_mismatch_ghs !== null,
  );
  const firstDeficitAmount = numOrNull(
    firstCumulativeDeficit?.cumulative_mismatch_ghs,
  );
  // A bucket with no cumulative mismatch is a GAP in the series, not a zero
  // bar: the table beside this chart already renders it "—", and a zero bar
  // reads as a measured "no mismatch" at exactly the bucket a reviewer is
  // checking for one. Recharts omits null points.
  const ladderChart = (data?.maturity_ladder ?? []).map((bucket) => ({
    label: bucket.label,
    net: numOrNull(bucket.net_mismatch_ghs),
    cumulative: numOrNull(bucket.cumulative_mismatch_ghs),
  }));
  const readinessExceptions = (data?.readiness ?? []).filter(
    (item) => item.status !== "ready",
  );

  return (
    <>
      <PageHeader eyebrow="Liquidity" title="Liquidity Monitoring Tools" />
      <QueryBoundary
        isLoading={
          monitoring.isLoading ||
          sdiLiquidity.isLoading ||
          baselLiquidity.isLoading
        }
        error={monitoring.error ?? sdiLiquidity.error ?? baselLiquidity.error}
        onRetry={() => {
          void monitoring.refetch();
          void sdiLiquidity.refetch();
          void baselLiquidity.refetch();
        }}
      >
        <PageContainer className="py-6 space-y-6">
          {data && concentration && capacity ? (
            <>
              <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-5 gap-4">
                {isSdi ? (
                  <>
                    <KpiStat
                      label="Worst LMTD ratio buffer"
                      value={
                        worstRatio
                          ? `${worstRatio.buffer >= 0 ? "+" : ""}${worstRatio.buffer.toFixed(1)} pp`
                          : "—"
                      }
                      status={metricStatus(worstRatio?.buffer ?? null)}
                      hint={worstRatio?.label ?? "No computable LMTD ratios"}
                    />
                    <KpiStat
                      label="Primary reserve buffer"
                      value={
                        primaryReserveBuffer === null
                          ? "—"
                          : `${primaryReserveBuffer >= 0 ? "+" : ""}${primaryReserveBuffer.toFixed(1)} pp`
                      }
                      status={metricStatus(primaryReserveBuffer)}
                      hint={
                        primaryReserve
                          ? `Minimum ${fmtPct(num(primaryReserve.threshold_pct), 1)}`
                          : "Reserve ratio unavailable"
                      }
                    />
                  </>
                ) : (
                  <>
                    <KpiStat
                      label="Liquidity Coverage Ratio"
                      value={
                        baselData
                          ? formatFigure(baselData.metrics.lcrPct, (value) =>
                              fmtPct(value, 1),
                            )
                          : "—"
                      }
                      status={
                        baselData?.metrics.lcrStatus === "red"
                          ? "crit"
                          : baselData?.metrics.lcrStatus === "amber"
                            ? "warn"
                            : baselData?.metrics.lcrStatus === "green"
                              ? "ok"
                              : undefined
                      }
                      hint="Basel 30-day liquidity coverage"
                    />
                    <KpiStat
                      label="Net Stable Funding Ratio"
                      value={
                        baselData
                          ? formatFigure(baselData.metrics.nsfrPct, (value) =>
                              fmtPct(value, 1),
                            )
                          : "—"
                      }
                      status={
                        baselData?.metrics.nsfrStatus === "red"
                          ? "crit"
                          : baselData?.metrics.nsfrStatus === "amber"
                            ? "warn"
                            : baselData?.metrics.nsfrStatus === "green"
                              ? "ok"
                              : undefined
                      }
                      hint="Basel available ÷ required stable funding"
                    />
                  </>
                )}
                <KpiStat
                  label="First maturity deficit"
                  value={
                    firstCumulativeDeficit
                      ? firstCumulativeDeficit.label
                      : cumulativeComputable
                        ? "None"
                        : "Not computable"
                  }
                  status={
                    firstCumulativeDeficit
                      ? "crit"
                      : cumulativeComputable
                        ? "ok"
                        : "warn"
                  }
                  hint={
                    firstDeficitAmount !== null
                      ? `Cumulative ${fmtCurrencySigned(firstDeficitAmount)}`
                      : cumulativeComputable
                        ? "No cumulative contractual deficit"
                        : "No bucket carries a cumulative position — not assessed"
                  }
                />
                <KpiStat
                  label="Top five funding share"
                  value={
                    concentration.top_five_pct === null
                      ? "Not computable"
                      : fmtPct(num(concentration.top_five_pct), 2)
                  }
                  status={
                    concentration.top_five_pct === null
                      ? "warn"
                      : num(concentration.top_five_pct) >= 50
                        ? "warn"
                        : "ok"
                  }
                  hint={
                    concentration.top_five_pct === null
                      ? "Deposit concentration could not be derived — not assessed"
                      : `${fmtCurrency(num(concentration.top_five_deposits_ghs))} of ${fmtCurrency(num(concentration.total_deposits_ghs))} deposits`
                  }
                />
                <KpiStat
                  label="Monetisable capacity"
                  value={fmtCurrency(num(capacity.monetized_value_ghs))}
                  status={capacity.uncalibrated_asset_count > 0 ? "warn" : "ok"}
                  hint="After active liquidity-value haircuts"
                />
              </div>

              <div className="grid gap-6 xl:grid-cols-2">
                <SectionCard
                  title="Contractual maturity ladder"
                  subtitle="Net and cumulative contractual mismatches from the LMTD maturity buckets."
                >
                  <MaturityLadderChart data={ladderChart} />
                </SectionCard>
                {isSdi ? (
                  <SectionCard
                    title="LMTD compliance"
                    subtitle="Table 1 liquidity ratios and buffers for this SDI, against the Liquidity Monitoring Tools Directive exposure draft (not yet in force)."
                    noPadding
                  >
                    <DataTable
                      columns={ratioColumns}
                      rows={sdiData?.ratios ?? []}
                      density="compact"
                      maxHeight={300}
                      stickyHeader
                    />
                  </SectionCard>
                ) : (
                  <SectionCard
                    title="Basel liquidity compliance"
                    subtitle="LCR/NSFR inputs and named validation exceptions from the active Basel calculation."
                  >
                    <dl className="grid grid-cols-2 gap-x-5 gap-y-4 text-caption">
                      <div>
                        <dt className="text-slate">HQLA stock</dt>
                        <dd className="mt-1 text-body font-medium text-navy">
                          {baselData
                            ? formatFigure(
                                baselData.metrics.hqlaTotalGhs,
                                fmtCurrency,
                              )
                            : "—"}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-slate">30-day net outflows</dt>
                        <dd className="mt-1 text-body font-medium text-navy">
                          {baselData
                            ? formatFigure(
                                baselData.metrics.netOutflows30dGhs,
                                fmtCurrency,
                              )
                            : "—"}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-slate">Available stable funding</dt>
                        <dd className="mt-1 text-body font-medium text-navy">
                          {baselData
                            ? formatFigure(
                                baselData.metrics.asfTotalGhs,
                                fmtCurrency,
                              )
                            : "—"}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-slate">Required stable funding</dt>
                        <dd className="mt-1 text-body font-medium text-navy">
                          {baselData
                            ? formatFigure(
                                baselData.metrics.rsfTotalGhs,
                                fmtCurrency,
                              )
                            : "—"}
                        </dd>
                      </div>
                    </dl>
                    {(baselData?.validations ?? [])
                      .filter((item) => !item.passed)
                      .map((item) => (
                        <p
                          key={item.ruleCode}
                          className="mt-4 text-caption text-warning"
                        >
                          {item.message}
                        </p>
                      ))}
                  </SectionCard>
                )}
              </div>

              <div className="grid gap-6 xl:grid-cols-2">
                <SectionCard
                  title="Largest deposit funding providers"
                  subtitle="Top connected providers as a share of current deposit liabilities."
                >
                  {providerChart.length > 0 ? (
                    <DepositProviderChart data={providerChart} />
                  ) : (
                    <p className="text-body text-slate">
                      No attributable deposit providers are available for
                      concentration analysis.
                    </p>
                  )}
                  {providersWithoutShare > 0 ? (
                    <p className="mt-3 text-caption text-warning">
                      {providersWithoutShare} provider
                      {providersWithoutShare === 1 ? "" : "s"} carry no
                      computable share of deposits and{" "}
                      {providersWithoutShare === 1 ? "is" : "are"} excluded from
                      this chart — the concentration shown is therefore
                      incomplete.
                    </p>
                  ) : null}
                </SectionCard>
                <SectionCard
                  title="Counterbalancing capacity"
                  subtitle="Assets available to generate liquidity without selling encumbered positions."
                >
                  <dl className="grid grid-cols-2 gap-x-5 gap-y-4 text-caption">
                    <div>
                      <dt className="text-slate">Gross unencumbered</dt>
                      <dd className="mt-1 text-body font-medium text-navy">
                        {fmtCurrency(num(capacity.gross_unencumbered_ghs))}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-slate">Post-haircut monetisable</dt>
                      <dd className="mt-1 text-body font-medium text-navy">
                        {fmtCurrency(num(capacity.monetized_value_ghs))}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-slate">
                        BoG standing-facility eligible
                      </dt>
                      <dd className="mt-1 text-body font-medium text-navy">
                        {fmtCurrency(num(capacity.bog_eligible_ghs))}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-slate">Uncalibrated assets</dt>
                      <dd className="mt-1 text-body font-medium text-navy">
                        {capacity.uncalibrated_asset_count}
                      </dd>
                    </div>
                  </dl>
                  {capacity.uncalibrated_asset_count > 0 ? (
                    <p className="mt-5 text-caption text-warning">
                      Assets without an adopted haircut retain gross value in
                      the display and require Senior Management calibration
                      before they support a decision-grade monetisation
                      estimate.
                    </p>
                  ) : null}
                </SectionCard>
              </div>

              <div className="grid gap-6 xl:grid-cols-2">
                {isSdi ? (
                  <SectionCard
                    title="Liquidity reserve compliance"
                    subtitle="Primary and secondary reserve coverage against the active control-plane minimums."
                  >
                    <dl className="grid grid-cols-1 gap-4">
                      {(sdiData?.reserves ?? []).map((reserve) => {
                        const value =
                          reserve.value_pct === null
                            ? null
                            : num(reserve.value_pct);
                        const buffer =
                          value === null
                            ? null
                            : value - num(reserve.threshold_pct);
                        return (
                          <div
                            key={reserve.code}
                            className="grid grid-cols-[1fr_auto] gap-4 border-b border-border-light pb-4 last:border-0 last:pb-0"
                          >
                            <div>
                              <p className="text-body font-medium text-navy">
                                {reserve.label}
                              </p>
                              <p className="mt-1 text-caption text-slate">
                                Minimum {fmtPct(num(reserve.threshold_pct), 1)}{" "}
                                · {reserve.confirmation_status}
                              </p>
                            </div>
                            <div className="text-right">
                              <p className="font-mono text-body text-navy">
                                {value === null ? "—" : fmtPct(value, 1)}
                              </p>
                              <StatusPill
                                tone={
                                  reserve.status === "below_minimum"
                                    ? "breach"
                                    : reserve.status === "ok"
                                      ? "compliant"
                                      : "pending"
                                }
                              >
                                {buffer === null
                                  ? "not computable"
                                  : `${buffer >= 0 ? "+" : ""}${buffer.toFixed(1)} pp`}
                              </StatusPill>
                            </div>
                          </div>
                        );
                      })}
                    </dl>
                  </SectionCard>
                ) : (
                  <SectionCard
                    title="HQLA composition"
                    subtitle="Post-haircut assets recognised in the Basel liquidity calculation."
                    noPadding
                  >
                    <DataTable
                      columns={[
                        {
                          key: "description",
                          header: "Asset class",
                          render: (row) => row.description,
                        },
                        {
                          key: "value",
                          header: "Recognised value",
                          numeric: true,
                          render: (row) => fmtCurrency(num(row.weightedAmount)),
                        },
                      ]}
                      rows={baselData?.hqlaComposition ?? []}
                      density="compact"
                    />
                  </SectionCard>
                )}
                <SectionCard
                  title="Source-data readiness"
                  subtitle="Only exceptions are shown; all other liquidity inputs are ready."
                >
                  {readinessExceptions.length > 0 ? (
                    <div className="space-y-4">
                      {readinessExceptions.map((item) => (
                        <div
                          key={item.module}
                          className="border-b border-border-light pb-4 last:border-0 last:pb-0"
                        >
                          <StatusPill
                            tone={
                              item.status === "blocked" ? "breach" : "amber"
                            }
                          >
                            {item.status}
                          </StatusPill>
                          <p className="mt-2 text-body font-medium text-navy">
                            {item.module.replaceAll("_", " ")}
                          </p>
                          {item.reasons.map((reason) => (
                            <p
                              key={reason}
                              className="mt-1 text-caption text-slate"
                            >
                              {reason}
                            </p>
                          ))}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-body text-success">
                      All monitored liquidity input sets are ready for the
                      current canonical-book date.
                    </p>
                  )}
                </SectionCard>
              </div>

              <SectionCard
                title="Maturity ladder detail"
                subtitle="The same contractual positions behind the mismatch chart, retained for review and export."
                noPadding
              >
                <DataTable
                  columns={maturityColumns}
                  rows={data.maturity_ladder}
                  density="compact"
                />
              </SectionCard>

              <SectionCard
                title="Funding concentration detail"
                subtitle="Deposit providers are grouped using the canonical connected-counterparty reference."
                noPadding
              >
                <DataTable
                  columns={providerColumns}
                  rows={concentration.providers}
                  density="compact"
                />
              </SectionCard>

              {num(concentration.unattributed_deposits_ghs) > 0 ? (
                <SectionCard title="Attribution exception">
                  <p className="text-caption text-slate">
                    {fmtCurrency(num(concentration.unattributed_deposits_ghs))}{" "}
                    of deposit funding has no linked counterparty and is
                    included in total deposits but excluded from provider
                    concentration.
                  </p>
                </SectionCard>
              ) : null}
            </>
          ) : null}
        </PageContainer>
      </QueryBoundary>
    </>
  );
}

/**
 * Contractual maturity ladder: net and cumulative mismatch per bucket.
 *
 * Both figures are nullable — `numOrNull` keeps an absent mismatch absent — and a
 * bucket with no figure draws no bar and says "no figure" in the tooltip. The
 * category axis is `inverse` so the shortest bucket stays at the top, where the
 * LMTD table puts it.
 */
function MaturityLadderChart({
  data,
}: {
  data: ReadonlyArray<{
    label: string;
    net: number | null;
    cumulative: number | null;
  }>;
}) {
  const tokens = useChartTokens();
  const labels = data.map((bucket) => bucket.label);
  const option: BiEChartsOption = {
    grid: { left: 8, right: 16, top: 6, bottom: 24, containLabel: true },
    legend: { bottom: 0, type: "scroll" },
    xAxis: {
      type: "value",
      axisLabel: {
        hideOverlap: true,
        formatter: (value: number) => fmtCurrency(value),
      },
    },
    yAxis: {
      type: "category",
      inverse: true,
      data: labels,
      axisLine: { show: false },
      splitLine: { show: false },
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: axisTooltip(labels, (value) => fmtCurrencySigned(value), {
        absent: "no figure for this bucket",
      }),
    },
    series: [
      {
        ...BAR_SERIES_BASE,
        name: "Net mismatch",
        barMaxWidth: 14,
        itemStyle: {
          color: seriesColor(tokens, 1),
          borderRadius: [0, 2, 2, 0],
        },
        data: data.map((bucket) => bucket.net),
      },
      {
        ...BAR_SERIES_BASE,
        name: "Cumulative mismatch",
        barMaxWidth: 14,
        itemStyle: {
          color: seriesColor(tokens, 0),
          borderRadius: [0, 2, 2, 0],
        },
        data: data.map((bucket) => bucket.cumulative),
      },
    ],
  } as BiEChartsOption;

  return (
    <EChart
      option={option}
      height={300}
      ariaLabel={`Contractual net and cumulative maturity mismatch across ${data.length} buckets`}
    />
  );
}

/** Largest deposit funding providers as a share of current deposit liabilities. */
function DepositProviderChart({
  data,
}: {
  data: ReadonlyArray<{ name: string; share: number }>;
}) {
  const tokens = useChartTokens();
  const labels = data.map((provider) => provider.name);
  const option: BiEChartsOption = {
    grid: { left: 8, right: 16, top: 6, bottom: 4, containLabel: true },
    xAxis: {
      type: "value",
      axisLabel: { formatter: (value: number) => `${value.toFixed(0)}%` },
    },
    yAxis: {
      type: "category",
      inverse: true,
      data: labels,
      axisLine: { show: false },
      splitLine: { show: false },
    },
    tooltip: {
      trigger: "item",
      formatter: itemTooltip(labels, (index) => {
        const provider = data[index];
        return provider === undefined
          ? []
          : [
              {
                label: "Deposit share",
                value: `${provider.share.toFixed(2)}%`,
                color: seriesColor(tokens, 0),
              },
            ];
      }),
    },
    series: [
      {
        ...BAR_SERIES_BASE,
        name: "Deposit share",
        barMaxWidth: 20,
        itemStyle: {
          color: seriesColor(tokens, 0),
          borderRadius: [0, 2, 2, 0],
        },
        data: data.map((provider) => provider.share),
      },
    ],
  } as BiEChartsOption;

  return (
    <EChart
      option={option}
      height={260}
      ariaLabel={`Deposit share of the ${data.length} largest connected funding providers`}
    />
  );
}
