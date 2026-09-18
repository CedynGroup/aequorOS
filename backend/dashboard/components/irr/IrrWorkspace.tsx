"use client";

/**
 * Shared frame for every IRRBB workspace tab: page header (freshness badge,
 * latest-run audit chip, run-all action), query boundary, and the dashboard
 * payload handed to the tab body via render prop. Keeps the five tabs'
 * data wiring identical so each page is purely presentational.
 */

import PageContainer from "@/components/ui/PageContainer";
import { useState, type ReactNode } from "react";
import { Play } from "lucide-react";
import type {
  IrrDashboardRead,
  IrrMetricsRead,
  RegulatoryRunRead,
} from "@aequoros/risk-service-api";
import PageHeader from "@/components/ui/PageHeader";
import { DisabledWithReason } from "@/components/ui/DisabledWithReason";
import QueryBoundary from "@/components/ui/QueryBoundary";
import { useBankContext } from "@/components/shell/BankContext";
import LiveEngineNote from "@/components/live/LiveEngineNote";
import {
  useIrrDashboard,
  useRegulatoryRun,
  useRunAllIrrScenarios,
} from "@/lib/api/hooks";
import { fmtDateUTC } from "@/lib/api/values";
import { IRRBB_CONFIDENTIAL_RUN_REASON } from "@/lib/modules";

export type IrrTabContext = {
  data: IrrDashboardRead;
  metrics: IrrMetricsRead;
  /** Latest stored regulatory run backing this dashboard, when available. */
  latestRun: RegulatoryRunRead | undefined;
  /** Timestamp for SectionCard footers: live recompute, else stored run. */
  computedAt: Date | undefined;
  bankId: string | undefined;
  periodId: string | undefined;
};

export default function IrrWorkspace({
  children,
}: {
  children: (ctx: IrrTabContext) => ReactNode;
}) {
  const { bank, period, moduleScope } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;
  const canRun = moduleScope.irrbbRun === true;
  const runAll = useRunAllIrrScenarios(bankId);
  const [runError, setRunError] = useState<string | null>(null);

  const dashboard = useIrrDashboard(bankId);
  const latestRun = useRegulatoryRun(bankId, dashboard.data?.latestRunId);

  const data = dashboard.data;
  const m = data?.metrics;

  const runScenarios = async () => {
    if (!periodId || !canRun) return;
    setRunError(null);
    try {
      await runAll.mutateAsync({ reportingPeriodId: periodId });
    } catch {
      setRunError("The IRRBB scenarios could not be run.");
    }
  };
  const runButton = (descriptionId?: string) => (
    <button
      type="button"
      className="btn-primary inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium disabled:cursor-not-allowed disabled:opacity-50"
      disabled={!canRun || !periodId || runAll.isPending}
      aria-describedby={descriptionId}
      onClick={() => void runScenarios()}
    >
      <Play size={14} aria-hidden />
      {runAll.isPending ? "Running…" : "Run IRRBB scenarios"}
    </button>
  );

  return (
    <>
      <PageHeader
        eyebrow="IRRBB"
        title="Interest Rate Risk"
        action={
          data ? (
            <div className="flex items-center gap-2">
              <LiveEngineNote live={data.live} stored={data.stored} />
              {canRun ? (
                runButton()
              ) : (
                <DisabledWithReason reason={IRRBB_CONFIDENTIAL_RUN_REASON}>
                  {(descriptionId) => runButton(descriptionId)}
                </DisabledWithReason>
              )}
            </div>
          ) : undefined
        }
      />

      {runError ? (
        <p className="mx-8 mt-4 text-caption text-critical" role="alert">
          {runError}
        </p>
      ) : null}

      <QueryBoundary
        isLoading={dashboard.isLoading}
        error={dashboard.error}
        onRetry={() => dashboard.refetch()}
      >
        {data && m && (
          <PageContainer className="py-6 space-y-6">
            {children({
              data,
              metrics: m,
              latestRun: latestRun.data,
              computedAt: data.live?.computedAt ?? latestRun.data?.createdAt,
              bankId,
              periodId: data.period.id,
            })}
          </PageContainer>
        )}
      </QueryBoundary>
    </>
  );
}
