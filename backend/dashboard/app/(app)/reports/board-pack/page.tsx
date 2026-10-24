"use client";

/** All financial figures and evidence come from one live-summary response. */
import "@/app/print.css";
import { useBankRules } from "@/lib/api/regulatoryRules";
import Link from "next/link";
import { Printer } from "lucide-react";
import type { LiveModule, LiveStatus } from "@aequoros/risk-service-api";
import PageContainer from "@/components/ui/PageContainer";
import PageHeader from "@/components/ui/PageHeader";
import KpiStat, { type KpiStatus } from "@/components/ui/KpiStat";
import EmptyState from "@/components/ui/EmptyState";
import QueryBoundary from "@/components/ui/QueryBoundary";
import { useBankContext, useModuleScope } from "@/components/shell/BankContext";
import { isHrefVisible } from "@/lib/modules";
import {
  LIVE_MODULE_HREFS,
  LIVE_MODULE_LABELS,
  livePrimaryMetricIsAdvisory,
} from "@/components/live/moduleDisplay";
import { BoardPage, ModuleBrief } from "@/components/reports/BoardPackSections";
import {
  boardTimestamp,
  liveBoardRows,
  liveBoardHeadline,
  liveEvidenceProblem,
} from "@/components/reports/liveBoardPack";
import { useLiveSummary } from "@/lib/api/hooks";
import { fmtDateUTC, labelize, statusTone } from "@/lib/api/values";

const LIVE_KPI_STATUS: Record<LiveStatus, KpiStatus | undefined> = {
  green: "ok",
  amber: "warn",
  red: "crit",
  na: undefined,
};
const BRIEF_MODULES: LiveModule[] = [
  "liquidity",
  "capital",
  "credit",
  "irr",
  "fx",
  "ftp",
];

export default function BoardPackPage() {
  const { bank } = useBankContext();
  const scope = useModuleScope();
  const source = useLiveSummary(bank?.id);
  const rules = useBankRules(bank?.id);
  const live = {
    ...source,
    data: source.data && {
      ...source.data,
      modules: source.data.modules.filter((row) =>
        rules.isModuleInForce(row.module),
      ),
    },
  };
  const briefModules = BRIEF_MODULES.filter(
    (module) =>
      rules.isModuleInForce(module) &&
      (module === "liquidity"
        ? scope.liquidityAggregatedView
        : isHrefVisible(LIVE_MODULE_HREFS[module], scope)),
  );
  const modulesWithBriefs = [
    ...new Set([
      ...briefModules,
      ...(live.data?.modules.map((view) => view.module) ?? []),
    ]),
  ];
  const staleProblem = live.data?.isStale
    ? "Live evidence is stale after an input change or withdrawal — figures withheld until recomputation completes."
    : null;
  const canPrint =
    Boolean(live.data?.modules.length) && !live.isLoading && !live.error;

  return (
    <>
      <div className="no-print">
        <PageHeader
          eyebrow="Reports"
          title="Board Pack"
          action={
            <div className="flex items-center gap-2">
              <Link href="/reports" className="btn-secondary">
                Reports library
              </Link>
              <button
                type="button"
                disabled={!canPrint}
                onClick={() => window.print()}
                className="btn-primary inline-flex items-center gap-2"
              >
                <Printer size={14} aria-hidden />
                Print / Save as PDF
              </button>
            </div>
          }
        />
      </div>
      <PageContainer className="board-pack py-6 space-y-6">
        <QueryBoundary
          isLoading={live.isLoading}
          error={live.error}
          onRetry={() => live.refetch()}
        >
          <BoardPage>
            <div className="card px-8 py-12 space-y-6">
              <p className="text-micro uppercase text-slate">
                aequorOS · Regulatory Command
              </p>
              <h1 className="text-display text-navy">
                Board Risk &amp; Regulatory Pack
              </h1>
              <dl className="grid grid-cols-2 gap-5 text-body">
                <div>
                  <dt>Institution</dt>
                  <dd className="text-h3">{bank?.name ?? "—"}</dd>
                </div>
                <div>
                  <dt>Reporting currency</dt>
                  <dd>{bank?.currency ?? "—"}</dd>
                </div>
                <div>
                  <dt>Report basis</dt>
                  <dd>Current live generations · Baseline</dd>
                </div>
                <div>
                  <dt>Latest computation</dt>
                  <dd>
                    {live.data?.computedAt
                      ? boardTimestamp(live.data.computedAt)
                      : "Unavailable"}
                  </dd>
                </div>
              </dl>
              <p className="text-body">
                The reporting-period selector does not apply to this pack. This
                is a current live view, not a historical snapshot. Each module
                declares its own source date and computation time; modules may
                have different generations and dates.
              </p>
              <p className="text-caption">
                Headlines, brief tables, hashes and engine versions come from
                the same live-summary payload. Baseline analyses may include
                internal shocks, identified in their tables. Official runs,
                stress-run history and current alerts are not evidence for these
                figures. No official run is attested by this pack; official
                evidence may be absent, failed or withdrawn.
              </p>
              <p className="text-caption">
                Only metrics published by the live engine are included. A
                missing metric is unavailable; validation counts and additional
                dashboard detail are not included in this live basis.
              </p>
              {staleProblem && (
                <p role="alert" className="text-critical">
                  {staleProblem}
                </p>
              )}
              {live.data?.reconciliation?.blocksFiling && (
                <p role="alert" className="text-critical">
                  Balance-sheet control blocks filing. Figures are withheld.{" "}
                  {live.data.reconciliation.message}
                </p>
              )}
              {live.data && !live.data.modules.length && (
                <EmptyState
                  title="No live evidence"
                  description="No computed modules are available for your access. Upload data through the Data Engine and wait for computation."
                />
              )}
            </div>
          </BoardPage>
          <BoardPage>
            <h2 className="text-h2 mb-2">Executive summary</h2>
            <p className="text-caption mb-4">
              Current baseline position. Source dates and producing generations
              are stated below.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {(live.data?.modules ?? []).map((view) => {
                const problem =
                  staleProblem ??
                  (live.data?.reconciliation?.blocksFiling
                    ? "Balance-sheet control blocks filing — figures withheld."
                    : liveEvidenceProblem(view));
                const metric = problem ? null : liveBoardHeadline(view);
                return (
                  <div key={view.module} className="bp-avoid-break">
                    <KpiStat
                      label={LIVE_MODULE_LABELS[view.module]}
                      value={metric?.value ?? "Unavailable"}
                      hint={
                        problem ??
                        `${metric?.label ?? "Metric unavailable"}${livePrimaryMetricIsAdvisory(view.module) ? " · Advisory / monitoring" : ""}`
                      }
                      status={
                        problem || !metric
                          ? undefined
                          : LIVE_KPI_STATUS[view.status]
                      }
                    />
                    <p className="text-caption mt-1">
                      Source{" "}
                      {view.sourceAsOfDate &&
                      Number.isFinite(view.sourceAsOfDate.getTime())
                        ? fmtDateUTC(view.sourceAsOfDate)
                        : "Unavailable"}{" "}
                      · Generation {view.calculationGeneration} · Computed{" "}
                      {view.computedAt &&
                      Number.isFinite(view.computedAt.getTime())
                        ? boardTimestamp(view.computedAt)
                        : "Unavailable"}
                    </p>
                  </div>
                );
              })}
            </div>
          </BoardPage>
          {modulesWithBriefs.map((module) => {
            const view = live.data?.modules.find(
              (item) => item.module === module,
            );
            const problem =
              staleProblem ??
              (live.data?.reconciliation?.blocksFiling
                ? "Balance-sheet control blocks filing — figures withheld."
                : liveEvidenceProblem(view));
            return (
              <BoardPage key={module}>
                {problem || !view ? (
                  <div className="card p-5">
                    <h2 className="text-h2">{LIVE_MODULE_LABELS[module]}</h2>
                    <p className="text-body mt-2">{problem}</p>
                  </div>
                ) : (
                  <ModuleBrief
                    name={LIVE_MODULE_LABELS[module]}
                    statusTone={statusTone(view.status)}
                    statusLabel={labelize(view.status)}
                    live={view}
                    rows={liveBoardRows(view, bank?.currency ?? "")}
                  >
                    {livePrimaryMetricIsAdvisory(module) && (
                      <p className="px-5 py-2 text-caption">
                        Advisory / monitoring figures.
                      </p>
                    )}
                  </ModuleBrief>
                )}
              </BoardPage>
            );
          })}
        </QueryBoundary>
      </PageContainer>
    </>
  );
}
