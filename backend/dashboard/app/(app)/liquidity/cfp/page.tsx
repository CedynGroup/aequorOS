"use client";

import Link from "next/link";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import PageHeader from "@/components/ui/PageHeader";
import KpiStat from "@/components/ui/KpiStat";
import SectionCard from "@/components/ui/SectionCard";
import StatusPill, { type StatusTone } from "@/components/ui/StatusPill";
import QueryBoundary from "@/components/ui/QueryBoundary";
import DataTable, { type Column } from "@/components/ui/DataTable";
import { useBankContext } from "@/components/shell/BankContext";
import { useCfpEvents, useCfpSummary, useEwiDashboard } from "@/lib/api/hooks";
import { fmtDateUTC } from "@/lib/api/values";
import { regShort } from "@/lib/format";
import {
  axisProps,
  CHART_GRID,
  chartTooltipProps,
  seriesColor,
} from "@/lib/chartTheme";
import { isHrefVisible } from "@/lib/modules";
import type { EwiEvaluationRead } from "@aequoros/risk-service-api";

// Server-side EWI states (LRMD ¶28(e)–(f)): values, Board trigger levels and
// RAG classifications all come from the engine — nothing is derived here.
const STATUS_TONE: Record<string, StatusTone> = {
  normal: "success",
  watch: "amber",
  action: "critical",
  unconfigured: "slate",
  no_data: "slate",
};

const STATUS_LABEL: Record<string, string> = {
  normal: "Normal",
  watch: "Watch",
  action: "Action",
  unconfigured: "Unconfigured",
  no_data: "No data",
};

const ESCALATION_COPY: Record<
  string,
  { label: string; status: "ok" | "warn" | "crit" }
> = {
  normal: { label: "Business as usual", status: "ok" },
  heightened_monitoring: { label: "Heightened monitoring", status: "warn" },
  escalation: { label: "Escalation — action trigger breached", status: "crit" },
  cfp_active: { label: "CFP ACTIVE", status: "crit" },
};

const CFP_HORIZONS = [
  "intraday",
  "up_to_1m",
  "1_to_3m",
  "3_to_12m",
  "over_12m",
];

function fmtValue(indicator: EwiEvaluationRead): string {
  if (indicator.value === null || indicator.value === undefined) return "—";
  const value = Number(indicator.value);
  if (indicator.unit === "count") return String(Math.round(value));
  if (indicator.unit === "days") return `${value.toFixed(0)} days`;
  return `${value.toFixed(2)}%`;
}

function fmtThreshold(indicator: EwiEvaluationRead, level: unknown): string {
  if (level === null || level === undefined) return "Not set";
  const op = indicator.direction === "below" ? "<" : "≥";
  const value = Number(level);
  const unit =
    indicator.unit === "count" ? "" : indicator.unit === "days" ? " days" : "%";
  return `${op} ${value}${unit}`;
}

const indicatorColumns: Column<EwiEvaluationRead>[] = [
  {
    key: "name",
    header: "Early-warning indicator",
    width: "32%",
    render: (r) => (
      <div>
        <p className="font-medium text-navy">{r.name}</p>
        <p className="text-caption text-slate">{r.metricBasis}</p>
        {r.recoveryPlanReference ? (
          <p className="text-caption text-slate/80">
            Recovery plan: {String(r.recoveryPlanReference)}
          </p>
        ) : null}
      </div>
    ),
  },
  {
    key: "current",
    header: "Current",
    numeric: true,
    render: (r) => fmtValue(r),
  },
  {
    key: "prior",
    header: "Prior period",
    numeric: true,
    render: (r) =>
      r.priorValue === null || r.priorValue === undefined
        ? "—"
        : fmtValue({ ...r, value: r.priorValue }),
  },
  {
    key: "watch",
    header: "Watch trigger",
    numeric: true,
    render: (r) => fmtThreshold(r, r.watchThreshold),
  },
  {
    key: "action",
    header: "Action trigger",
    numeric: true,
    render: (r) => fmtThreshold(r, r.actionThreshold),
  },
  {
    key: "status",
    header: "Status",
    align: "right",
    render: (r) => (
      <div className="flex flex-col items-end gap-1">
        <StatusPill tone={STATUS_TONE[r.status] ?? "slate"}>
          {STATUS_LABEL[r.status] ?? r.status}
        </StatusPill>
        {r.detail ? (
          <span className="text-caption text-slate text-right max-w-[220px]">
            {r.detail}
          </span>
        ) : null}
      </div>
    ),
  },
];

export default function ContingencyFundingPlan() {
  const { bank, moduleScope } = useBankContext();
  const bankId = bank?.id;

  const ewis = useEwiDashboard(bankId);
  const cfp = useCfpSummary(bankId);
  const events = useCfpEvents(bankId);

  const dashboard = ewis.data;
  const approved = cfp.data?.approved ?? null;
  const fundingOptions = approved?.content.fundingOptions ?? [];
  const actionPlans = approved?.content.actionPlans ?? [];
  const behavioralScenarios =
    approved?.content.behavioralLiquidityScenarios ?? [];
  const indicators = dashboard?.indicators ?? [];
  const actionIndicators = indicators.filter(
    (entry) => entry.status === "action",
  );
  const watchIndicators = indicators.filter(
    (entry) => entry.status === "watch",
  );
  const unconfiguredIndicators = indicators.filter(
    (entry) => entry.status === "unconfigured",
  );
  const noDataIndicators = indicators.filter(
    (entry) => entry.status === "no_data",
  );
  const fundingHorizons = new Set(
    fundingOptions.map((option) => option.horizon),
  );
  const actionTimelineGaps = actionPlans.filter(
    (plan) => !plan.timeline,
  ).length;
  const ewiDistribution = [
    {
      status: "Normal",
      count: indicators.filter((entry) => entry.status === "normal").length,
    },
    { status: "Watch", count: watchIndicators.length },
    { status: "Action", count: actionIndicators.length },
    { status: "Unconfigured", count: unconfiguredIndicators.length },
    { status: "No data", count: noDataIndicators.length },
  ];
  const escalation = dashboard
    ? (ESCALATION_COPY[dashboard.escalationState] ?? ESCALATION_COPY.normal)
    : ESCALATION_COPY.normal;

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: "Modules", href: "/" },
          { label: "Liquidity Risk", href: "/liquidity" },
          { label: "CFP" },
        ]}
        title="Contingency Funding Plan"
        subtitle={`Server-side EWI framework (LRMD ¶28) · CFP lifecycle with ${regShort()} ¶74 notification`}
      />

      <QueryBoundary
        isLoading={ewis.isLoading}
        error={ewis.error}
        onRetry={() => ewis.refetch()}
      >
        {dashboard && (
          <div className="px-8 py-6 space-y-6">
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-6 gap-4">
              <KpiStat
                label="Escalation state"
                value={escalation.label}
                status={escalation.status}
                hint="Computed server-side from indicator states"
              />
              <KpiStat
                label="Indicators at action"
                value={String(
                  dashboard.indicators.filter(
                    (entry) => entry.status === "action",
                  ).length,
                )}
                status={
                  dashboard.indicators.some(
                    (entry) => entry.status === "action",
                  )
                    ? "crit"
                    : "ok"
                }
                hint={`${dashboard.indicators.filter((entry) => entry.status === "watch").length} at watch`}
              />
              <KpiStat
                label="Board-approved CFP"
                value={approved ? `v${approved.version}` : "None"}
                // No approved plan is an absence-of-data state, not a breach —
                // neutral card, no status edge.
                status={
                  approved
                    ? approved.approvalOverdue
                      ? "warn"
                      : "ok"
                    : undefined
                }
                hint={
                  approved
                    ? approved.approvalOverdue
                      ? "Annual re-approval overdue (¶71)"
                      : `Approval valid to ${approved.approvalExpiresAt ? fmtDateUTC(new Date(String(approved.approvalExpiresAt))) : "—"}`
                    : "Approve a plan before activation is possible"
                }
              />
              <KpiStat
                label="CFP activation"
                value={dashboard.cfpActive ? "ACTIVE" : "Not active"}
                status={dashboard.cfpActive ? "crit" : "ok"}
                hint={`Activation notifies ${regShort()} (LRMD ¶74)`}
              />
              <KpiStat
                label="Funding horizons covered"
                value={`${fundingHorizons.size} / ${CFP_HORIZONS.length}`}
                status={
                  fundingHorizons.size === CFP_HORIZONS.length ? "ok" : "warn"
                }
                hint="Intraday through structural funding horizons"
              />
              <KpiStat
                label="EWI evidence gaps"
                value={String(
                  unconfiguredIndicators.length + noDataIndicators.length,
                )}
                status={
                  unconfiguredIndicators.length + noDataIndicators.length > 0
                    ? "warn"
                    : "ok"
                }
                hint={`${unconfiguredIndicators.length} unconfigured · ${noDataIndicators.length} without data`}
              />
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
              <SectionCard
                className="xl:col-span-2"
                title="Early-warning distribution"
                subtitle="Server-calculated indicator states; unconfigured and no-data signals remain visible control gaps."
              >
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart
                    data={ewiDistribution}
                    margin={{ top: 8, right: 12, bottom: 4, left: 0 }}
                  >
                    <CartesianGrid
                      vertical={false}
                      stroke={CHART_GRID}
                      strokeDasharray="3 3"
                    />
                    <XAxis dataKey="status" {...axisProps} />
                    <YAxis
                      allowDecimals={false}
                      axisLine={false}
                      tickLine={false}
                      tick={axisProps.tick}
                      width={28}
                    />
                    <Tooltip
                      {...chartTooltipProps}
                      formatter={(value: number) => [value, "Indicators"]}
                    />
                    <Bar
                      dataKey="count"
                      name="Indicators"
                      fill={seriesColor(0)}
                      maxBarSize={42}
                      radius={[2, 2, 0, 0]}
                    />
                  </BarChart>
                </ResponsiveContainer>
              </SectionCard>
              <SectionCard
                title="Plan execution readiness"
                subtitle="Completeness is a decision-control issue, not a document cosmetic."
              >
                <dl className="space-y-3 text-caption">
                  <div className="flex items-center justify-between gap-3 border-b border-border-light pb-3">
                    <dt className="text-slate">Funding options</dt>
                    <dd className="font-mono text-navy">
                      {fundingOptions.length}
                    </dd>
                  </div>
                  <div className="flex items-center justify-between gap-3 border-b border-border-light pb-3">
                    <dt className="text-slate">Actions without timing</dt>
                    <dd
                      className={
                        actionTimelineGaps > 0
                          ? "font-mono text-warning"
                          : "font-mono text-success"
                      }
                    >
                      {actionTimelineGaps}
                    </dd>
                  </div>
                  <div className="flex items-center justify-between gap-3 border-b border-border-light pb-3">
                    <dt className="text-slate">Behavioral overlays</dt>
                    <dd className="font-mono text-navy">
                      {behavioralScenarios.length}
                    </dd>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <dt className="text-slate">Action triggers now</dt>
                    <dd
                      className={
                        actionIndicators.length > 0
                          ? "font-mono text-critical"
                          : "font-mono text-success"
                      }
                    >
                      {actionIndicators.length}
                    </dd>
                  </div>
                </dl>
              </SectionCard>
            </div>

            <SectionCard
              title="Early-warning indicators"
              subtitle="The eight directive starter indicators plus any Board additions — values, trigger levels and states computed by the engine"
              noPadding
              footer={
                <span>
                  Trigger levels are Board configuration with approval evidence;
                  an indicator without levels shows Unconfigured rather than an
                  invented classification.{" "}
                  {isHrefVisible("/liquidity/monitoring", moduleScope) ? (
                    <Link
                      href="/liquidity/monitoring"
                      className="text-action hover:underline"
                    >
                      Monitoring tools & threshold register
                    </Link>
                  ) : (
                    <span>Monitoring tools & threshold register</span>
                  )}
                </span>
              }
            >
              <DataTable
                columns={indicatorColumns}
                rows={dashboard.indicators}
              />
            </SectionCard>

            <SectionCard
              title="Activation log"
              subtitle="¶74 events — each carries the EWI snapshot at event time and the regulator-notification evidence"
              noPadding
              footer={
                <span>
                  Activation and de-escalation are approver-gated Board acts
                  recorded append-only; the plan document itself (¶72(a)–(g)
                  contents) is maintained through the CFP API workspace.
                </span>
              }
            >
              {events.data && events.data.events.length > 0 ? (
                <ul className="divide-y divide-border-light">
                  {events.data.events.map((event) => (
                    <li
                      key={event.id}
                      className="px-5 py-4 flex items-start gap-4"
                    >
                      <StatusPill
                        tone={
                          event.eventType === "activated"
                            ? "critical"
                            : "success"
                        }
                      >
                        {event.eventType === "activated"
                          ? "Activated"
                          : "De-escalated"}
                      </StatusPill>
                      <div className="min-w-0 flex-1">
                        <p className="text-body text-navy">{event.reason}</p>
                        <p className="mt-1 text-caption text-slate">
                          CFP v{event.cfpVersion} ·{" "}
                          {fmtDateUTC(event.createdAt)}
                          {event.approvalOverdue
                            ? " · Board approval was overdue at event time (¶71)"
                            : ""}
                          {event.regulatorNotificationId
                            ? " · regulator-notification recorded"
                            : ""}
                        </p>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="px-5 py-6 text-body text-slate">
                  No activation events. The CFP has never been triggered for
                  this institution.
                </p>
              )}
            </SectionCard>

            <div className="grid gap-6 xl:grid-cols-2">
              <SectionCard
                title="Funding action inventory"
                subtitle="Board-approved funding options by activation horizon, capacity, and lead time."
                noPadding
              >
                {fundingOptions.length > 0 ? (
                  <DataTable
                    columns={[
                      {
                        key: "horizon",
                        header: "Horizon",
                        render: (row) => row.horizon.replaceAll("_", " "),
                      },
                      {
                        key: "source",
                        header: "Funding source",
                        render: (row) => row.source,
                      },
                      {
                        key: "capacity",
                        header: "Estimated capacity",
                        render: (row) =>
                          row.estimatedCapacity ?? "Not evidenced",
                      },
                      {
                        key: "lead",
                        header: "Lead time",
                        render: (row) => row.leadTime ?? "Not evidenced",
                      },
                    ]}
                    rows={fundingOptions}
                    density="compact"
                  />
                ) : (
                  <p className="px-5 py-6 text-body text-slate">
                    No Board-approved funding inventory is available.
                  </p>
                )}
              </SectionCard>

              <SectionCard
                title="Action ownership and readiness"
                subtitle="Asset and liability actions, with the accountable owner and stated execution timeline."
                noPadding
              >
                {actionPlans.length > 0 ? (
                  <DataTable
                    columns={[
                      {
                        key: "side",
                        header: "Side",
                        render: (row) =>
                          row.side === "asset" ? "Asset" : "Liability",
                      },
                      {
                        key: "action",
                        header: "Action",
                        render: (row) => row.action,
                      },
                      {
                        key: "owner",
                        header: "Owner",
                        render: (row) => row.owner,
                      },
                      {
                        key: "timeline",
                        header: "Execution timing",
                        render: (row) => row.timeline ?? "Not evidenced",
                      },
                    ]}
                    rows={actionPlans}
                    density="compact"
                  />
                ) : (
                  <p className="px-5 py-6 text-body text-slate">
                    No Board-approved action plan is available.
                  </p>
                )}
              </SectionCard>
            </div>

            <SectionCard
              title="Behavioral liquidity scenarios"
              subtitle="Board-owned deposit runoff and funding-cost overlays, each linked to a documented CFP action."
              noPadding
            >
              {behavioralScenarios.length > 0 ? (
                <DataTable
                  columns={[
                    {
                      key: "name",
                      header: "Scenario",
                      render: (row) => row.name,
                    },
                    {
                      key: "horizon",
                      header: "Activation horizon",
                      render: (row) =>
                        row.activationHorizon.replaceAll("_", " "),
                    },
                    {
                      key: "runoff",
                      header: "Runoff uplift",
                      numeric: true,
                      render: (row) =>
                        `${Number(row.depositRunoffUpliftPct).toFixed(1)}%`,
                    },
                    {
                      key: "cost",
                      header: "Funding-cost uplift",
                      numeric: true,
                      render: (row) =>
                        `${Number(row.fundingCostUpliftBps).toFixed(0)} bps`,
                    },
                    {
                      key: "action",
                      header: "Linked action",
                      render: (row) => row.linkedAction,
                    },
                  ]}
                  rows={behavioralScenarios}
                  density="compact"
                />
              ) : (
                <p className="px-5 py-6 text-body text-slate">
                  No approved behavioral liquidity scenarios are linked to this
                  CFP. Add them through the audited CFP draft alongside their
                  action-plan references.
                </p>
              )}
            </SectionCard>

            <SectionCard
              title="Exercise evidence"
              subtitle="This release keeps exercise readiness honest: drills and test evidence require a recorded CFP exercise artifact before they can be represented as tested."
            >
              <p className="text-caption text-slate">
                No CFP exercise-evidence register is implemented yet. The
                approved plan, action inventory, ownership, and
                activation/de-escalation log above are available now; drill
                frequency, test outcome, and evidence attachments need a
                dedicated immutable exercise record.
              </p>
            </SectionCard>
          </div>
        )}
      </QueryBoundary>
    </>
  );
}
