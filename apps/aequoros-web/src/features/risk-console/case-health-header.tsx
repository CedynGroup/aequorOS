import type {
  CalculationRunListRead,
  CalculationStatus,
  CaseDecision,
  FinancialDataWorkspaceRead,
  FindingRead,
  ScenarioWorkspaceRead,
} from "@aequoros/risk-service-api";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";
import { type ReactNode, useEffect, useRef } from "react";

import { Skeleton, Tooltip } from "../../components/ui";
import { riskApi, type TenantHeaders } from "../../lib/api";
import type { ConsoleTab } from "../../lib/constants";
import { cn, labelize } from "../../lib/utils";
import type { MockCaseHealthData } from "../demo-data/demo-data";
import { financialReviewClient } from "../financial/financial-client";

type HealthTone = "danger" | "muted" | "success" | "warning";

export function CaseHealthHeader({
  tenant,
  caseId,
  decision,
  demoData,
  onNavigate,
}: {
  tenant: TenantHeaders;
  caseId: string;
  decision: CaseDecision | null | undefined;
  demoData?: MockCaseHealthData;
  onNavigate: (tab: ConsoleTab) => void;
}) {
  const queryClient = useQueryClient();
  const runScope = `${tenant.orgId}:${tenant.userId}:${caseId}`;
  const previousRunStatuses = useRef<{
    scope: string;
    statuses: ReadonlyMap<string, CalculationStatus>;
  }>({ scope: runScope, statuses: new Map() });
  const financial = useQuery({
    queryKey: ["financial-workspace", tenant, caseId],
    queryFn: () => financialReviewClient.workspace(tenant, caseId),
    enabled: !demoData,
  });
  const scenarios = useQuery({
    queryKey: ["scenarios", tenant, caseId, false],
    queryFn: () => riskApi.scenarios(tenant, caseId, false),
    enabled: !demoData,
  });
  const runs = useQuery({
    queryKey: ["calculation-runs", tenant, caseId, 0],
    queryFn: () => riskApi.calculationRuns(tenant, caseId),
    enabled: !demoData,
    refetchInterval: (query) =>
      query.state.data?.runs.some((run) =>
        (["queued", "running"] as CalculationStatus[]).includes(run.status),
      )
        ? 1000
        : false,
  });
  const findings = useQuery({
    queryKey: ["findings", tenant, caseId],
    queryFn: () => riskApi.findings(tenant, caseId),
    enabled: !demoData,
  });
  const runList = runs.data?.runs;

  useEffect(() => {
    const previous = previousRunStatuses.current;
    const statuses = new Map(
      runList?.map((run) => [run.id, run.status] as const) ?? [],
    );
    previousRunStatuses.current = { scope: runScope, statuses };
    const runCompleted =
      previous.scope === runScope &&
      [...statuses].some(([runId, status]) => {
        const previousStatus = previous.statuses.get(runId);
        return (
          previousStatus && isActiveRun(previousStatus) && !isActiveRun(status)
        );
      });
    if (runCompleted) {
      void queryClient.invalidateQueries({
        queryKey: ["findings", tenant, caseId],
      });
    }
  }, [caseId, queryClient, runList, runScope, tenant]);

  return (
    <section
      aria-label="Case health"
      data-testid="case-health-header"
      className="grid min-w-0 grid-cols-2 border-b border-[rgb(var(--border))] bg-[rgb(var(--surface-2))] sm:grid-cols-3"
    >
      <HealthLink label="Validation" tab="financial" onNavigate={onNavigate}>
        <QueryState
          query={financial}
          data={demoData?.financial}
          render={validationState}
        />
      </HealthLink>
      <HealthLink label="Scenarios" tab="scenarios" onNavigate={onNavigate}>
        <QueryState
          query={scenarios}
          data={demoData?.scenarios}
          render={scenarioState}
        />
      </HealthLink>
      <HealthLink label="Forecast" tab="calculations" onNavigate={onNavigate}>
        <QueryState query={runs} data={demoData?.runs} render={runState} />
      </HealthLink>
      <HealthLink label="Findings" tab="findings" onNavigate={onNavigate}>
        <QueryState
          query={findings}
          data={demoData?.findings}
          render={findingState}
        />
      </HealthLink>
      <HealthLink label="Covenants" tab="financial" onNavigate={onNavigate}>
        <QueryState
          query={financial}
          data={demoData?.financial}
          render={covenantState}
        />
      </HealthLink>
      <HealthLink label="Decision" tab="decisions" onNavigate={onNavigate}>
        {decision === undefined ? (
          <HealthSkeleton />
        ) : decision === null ? (
          <HealthValue tone="muted">No decision</HealthValue>
        ) : (
          <HealthValue tone={decisionTone(decision)}>
            {labelize(decision)}
          </HealthValue>
        )}
      </HealthLink>
    </section>
  );
}

function HealthLink({
  label,
  tab,
  onNavigate,
  children,
}: {
  label: string;
  tab: ConsoleTab;
  onNavigate: (tab: ConsoleTab) => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      className="group min-w-0 border-b border-r border-[rgb(var(--border))] px-3 py-2 text-left outline-none last:border-r-0 hover:bg-[rgb(var(--muted))] focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[rgb(var(--focus))]"
      onClick={() => onNavigate(tab)}
    >
      <span className="flex min-w-0 items-center justify-between gap-1 text-[10px] font-medium uppercase tracking-[0.06em] text-[rgb(var(--muted-foreground))]">
        <span data-health-label className="whitespace-normal">
          {label}
        </span>
        <ChevronRight
          aria-hidden="true"
          className="size-3 shrink-0 transition-transform group-hover:translate-x-0.5"
        />
      </span>
      <span className="mt-1 block min-w-0">{children}</span>
    </button>
  );
}

function QueryState<T>({
  query,
  data,
  render,
}: {
  query: { data?: T; isError: boolean; isLoading: boolean };
  data?: T;
  render: (data: T) => ReactNode;
}) {
  if (data) return render(data);
  if (query.isLoading) return <HealthSkeleton />;
  if (query.isError || !query.data)
    return <HealthValue tone="muted">Unknown</HealthValue>;
  return render(query.data);
}

function HealthSkeleton() {
  return (
    <output aria-label="Loading status">
      <Skeleton className="h-5 w-24 max-w-full" />
    </output>
  );
}

function HealthValue({
  tone,
  children,
  title,
}: {
  tone: HealthTone;
  children: ReactNode;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cn(
        "block min-w-0 whitespace-normal break-words text-sm font-semibold",
        tone === "danger" && "text-[rgb(var(--danger))]",
        tone === "warning" && "text-[rgb(var(--warning))]",
        tone === "success" && "text-[rgb(var(--success))]",
        tone === "muted" && "text-[rgb(var(--muted-foreground))]",
      )}
    >
      {children}
    </span>
  );
}

function validationState(workspace: FinancialDataWorkspaceRead) {
  if (!hasCanonicalFinancialData(workspace))
    return <HealthValue tone="muted">No financial data</HealthValue>;
  const summary = { error: 0, warning: 0, info: 0 };
  for (const issue of workspace.validationIssues) {
    if (issue.status !== "open") continue;
    if (issue.severity === "error") summary.error += 1;
    if (issue.severity === "warning") summary.warning += 1;
    if (issue.severity === "info") summary.info += 1;
  }
  if (summary.error > 0)
    return (
      <HealthValue tone="danger">
        <span className="tabular-nums">{summary.error}</span> errors
      </HealthValue>
    );
  if (summary.warning > 0)
    return (
      <HealthValue tone="warning">
        <span className="tabular-nums">{summary.warning}</span> warnings
      </HealthValue>
    );
  return (
    <HealthValue tone="success">
      Validated{summary.info > 0 ? ` · ${summary.info} info` : ""}
    </HealthValue>
  );
}

function scenarioState(workspace: ScenarioWorkspaceRead) {
  const { completeScenarioCount, ready, scenarioCount } = workspace.readiness;
  if (scenarioCount === 0)
    return <HealthValue tone="muted">No scenarios</HealthValue>;
  if (ready) return <HealthValue tone="success">Ready</HealthValue>;
  return (
    <HealthValue tone="warning">
      <span className="tabular-nums">
        {completeScenarioCount}/{scenarioCount}
      </span>{" "}
      ready
    </HealthValue>
  );
}

function runState(list: CalculationRunListRead) {
  const latest = list.runs[0];
  if (!latest) return <HealthValue tone="muted">No forecasts</HealthValue>;
  const reference = `Forecast #${list.total}`;
  return (
    <HealthValue
      tone={
        latest.status === "succeeded"
          ? "success"
          : latest.status === "failed"
            ? "danger"
            : "warning"
      }
      title={`${reference} · ${labelize(latest.status)}`}
    >
      <span className="tabular-nums">{reference}</span> ·{" "}
      {labelize(latest.status)}
    </HealthValue>
  );
}

function findingState(findings: FindingRead[]) {
  if (!findings.length)
    return <HealthValue tone="muted">No findings</HealthValue>;
  const activeFindings = findings.filter(
    (finding) => finding.status === "open" || finding.status === "needs_review",
  );
  const resolvedCount = findings.filter(
    (finding) => finding.status === "resolved",
  ).length;
  const otherHistoryCount =
    findings.length - activeFindings.length - resolvedCount;
  const historySummary = [
    resolvedCount ? `${resolvedCount} resolved` : null,
    otherHistoryCount ? `${otherHistoryCount} other historical` : null,
  ]
    .filter(Boolean)
    .join(", ");
  if (!activeFindings.length)
    return (
      <span
        aria-label={`No active findings, ${historySummary}`}
        className="flex min-w-0 flex-wrap gap-x-2 gap-y-0.5 text-xs font-semibold text-[rgb(var(--muted-foreground))] tabular-nums"
      >
        <span>No active</span>
        <FindingHistory
          resolvedCount={resolvedCount}
          otherHistoryCount={otherHistoryCount}
        />
      </span>
    );
  const counts = countSeverities(activeFindings);
  const summary = (["critical", "high", "medium", "low"] as const)
    .map((severity) => `${counts[severity]} ${severity}`)
    .join(", ")
    .concat(historySummary ? `, ${historySummary}` : "");
  return (
    <span
      aria-label={summary}
      className="flex min-w-0 flex-wrap gap-x-2 gap-y-0.5 text-xs font-semibold tabular-nums"
    >
      <SeverityCount severity="critical" count={counts.critical} />
      <SeverityCount severity="high" count={counts.high} />
      <SeverityCount severity="medium" count={counts.medium} />
      <SeverityCount severity="low" count={counts.low} />
      <FindingHistory
        resolvedCount={resolvedCount}
        otherHistoryCount={otherHistoryCount}
      />
    </span>
  );
}

function FindingHistory({
  resolvedCount,
  otherHistoryCount,
}: {
  resolvedCount: number;
  otherHistoryCount: number;
}) {
  return (
    <>
      {resolvedCount ? (
        <span className="text-[rgb(var(--muted-foreground))]">
          +{resolvedCount} resolved
        </span>
      ) : null}
      {otherHistoryCount ? (
        <span className="text-[rgb(var(--muted-foreground))]">
          +{otherHistoryCount} other history
        </span>
      ) : null}
    </>
  );
}

function isActiveRun(status: CalculationStatus) {
  return status === "queued" || status === "running";
}

function SeverityCount({
  severity,
  count,
}: {
  severity: "critical" | "high" | "low" | "medium";
  count: number;
}) {
  const label = `${labelize(severity)}: ${count} active findings`;
  return (
    <Tooltip label={label}>
      <span
        aria-label={label}
        className="inline-flex items-center gap-1 rounded-full border border-[rgb(var(--border))] bg-[rgb(var(--surface))] px-1.5 py-0.5 text-[rgb(var(--foreground))]"
      >
        <span
          aria-hidden="true"
          className={cn(
            "size-1.5 shrink-0 rounded-full",
            (severity === "critical" || severity === "high") &&
              "bg-[rgb(var(--danger))]",
            severity === "medium" && "bg-[rgb(var(--warning))]",
            severity === "low" && "bg-[rgb(var(--success))]",
          )}
        />
        <span>{count}</span>
      </span>
    </Tooltip>
  );
}

function covenantState(workspace: FinancialDataWorkspaceRead) {
  if (!workspace.covenants.length)
    return <HealthValue tone="muted">No covenants</HealthValue>;
  if (
    workspace.covenants.some(
      (covenant) => covenant.complianceStatus === "non_compliant",
    )
  )
    return <HealthValue tone="danger">Non-compliant</HealthValue>;
  if (
    workspace.covenants.some(
      (covenant) => covenant.complianceStatus === "unknown",
    )
  )
    return <HealthValue tone="muted">Unknown</HealthValue>;
  return <HealthValue tone="success">Compliant</HealthValue>;
}

function hasCanonicalFinancialData(workspace: FinancialDataWorkspaceRead) {
  return [
    workspace.institutions,
    workspace.accounts,
    workspace.reportingPeriods,
    workspace.balances,
    workspace.cashFlows,
    workspace.obligations,
    workspace.covenants,
  ].some((records) => records.length > 0);
}

function countSeverities(findings: FindingRead[]) {
  const counts = { critical: 0, high: 0, medium: 0, low: 0 };
  for (const finding of findings) {
    if (finding.severity in counts)
      counts[finding.severity as keyof typeof counts] += 1;
  }
  return counts;
}

function decisionTone(decision: CaseDecision): HealthTone {
  if (decision === "approved") return "success";
  if (decision === "rejected") return "danger";
  return "warning";
}
