"use client";

/**
 * Shared chrome for every FTP workspace tab: page header,
 * freshness / run badges, the run-all-scenarios action, the not-yet-stored
 * banner, and the query boundary. Sub-pages receive the loaded dashboard via
 * a render prop so the payload is fetched (and cached) once per query key.
 */

<<<<<<< HEAD
import PageContainer from '@/components/ui/PageContainer';
import type { ReactNode } from 'react';
=======
import { useState, type ReactNode } from "react";
import { Play } from "lucide-react";
>>>>>>> a9fb0b01 (feat(risk-service): enforce scoped FTP authorization)
import type {
  FtpDashboardRead,
  FtpMetricsRead,
  RegulatoryRunRead,
} from "@aequoros/risk-service-api";
import PageHeader from "@/components/ui/PageHeader";
import { DisabledWithReason } from "@/components/ui/DisabledWithReason";
import QueryBoundary from "@/components/ui/QueryBoundary";
import { useBankContext } from "@/components/shell/BankContext";
import LiveEngineNote from "@/components/live/LiveEngineNote";
import {
  useFtpDashboard,
  useRegulatoryRun,
  useRunAllFtpScenarios,
} from "@/lib/api/hooks";
import { FTP_CONFIDENTIAL_RUN_REASON } from "@/lib/modules";

export type FtpFrameContext = {
  data: FtpDashboardRead;
  metrics: FtpMetricsRead;
  /** Latest stored baseline run (audit trail + parameter snapshot), if any. */
  run: RegulatoryRunRead | undefined;
  bankId: string | undefined;
  periodId: string | undefined;
};

export default function FtpModuleFrame({
  title,
  children,
}: {
  title: string;
  children: (ctx: FtpFrameContext) => ReactNode;
}) {
  const { bank, period, moduleScope } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;
  const canRun = moduleScope.ftpRun === true;
  const runAll = useRunAllFtpScenarios(bankId);
  const [runError, setRunError] = useState<string | null>(null);

  const dashboard = useFtpDashboard(
    moduleScope.ftpAggregatedView ? bankId : undefined,
    periodId,
  );
  const latestRun = useRegulatoryRun(
    moduleScope.ftpConfidentialView ? bankId : undefined,
    dashboard.data?.latestRunId,
  );

  const data = dashboard.data;

  const runScenarios = async () => {
    if (!periodId || !canRun) return;
    setRunError(null);
    try {
      await runAll.mutateAsync({ reportingPeriodId: periodId });
    } catch {
      setRunError("The FTP scenarios could not be run.");
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
      {runAll.isPending ? "Running…" : "Run FTP scenarios"}
    </button>
  );

  return (
    <>
      <PageHeader
<<<<<<< HEAD
        eyebrow="FTP"
        title={title}
        action={data ? <LiveEngineNote live={data.live} stored={data.stored} /> : undefined}
=======
        breadcrumbs={[
          { label: "Modules", href: "/" },
          { label: "Funds Transfer Pricing" },
          { label: crumb },
        ]}
        title={title}
        subtitle={subtitle}
        action={
          data ? (
            <div className="flex items-center gap-2">
              <LiveEngineNote live={data.live} stored={data.stored} />
              {canRun ? (
                runButton()
              ) : (
                <DisabledWithReason reason={FTP_CONFIDENTIAL_RUN_REASON}>
                  {(descriptionId) => runButton(descriptionId)}
                </DisabledWithReason>
              )}
            </div>
          ) : undefined
        }
>>>>>>> a9fb0b01 (feat(risk-service): enforce scoped FTP authorization)
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
        {data && (
          <PageContainer className="py-6 space-y-6">
            {children({
              data,
              metrics: data.metrics,
              run: latestRun.data,
              bankId,
              periodId: data.period.id,
            })}
          </PageContainer>
        )}
      </QueryBoundary>
    </>
  );
}
