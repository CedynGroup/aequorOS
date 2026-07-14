import type {
  CalculationRunListRead,
  CalculationStatus,
  CaseDecision,
  FinancialDataWorkspaceRead,
  FindingRead,
  ScenarioWorkspaceRead,
} from "@aequoros/risk-service-api";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";
import type { ReactNode } from "react";

import { Skeleton } from "../../components/ui";
import { riskApi, type TenantHeaders } from "../../lib/api";
import type { ConsoleTab } from "../../lib/constants";
import { cn, labelize } from "../../lib/utils";
import type { MockCaseHealthData } from "../demo-data/demo-data";

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
  const financial = useQuery({
    queryKey: ["financial-workspace", tenant, caseId],
    queryFn: () => riskApi.financialWorkspace(tenant, caseId),
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

  return (
    <section
      aria-label="Case health"
      data-testid="case-health-header"
      className="grid min-w-0 grid-cols-2 border-b border-[rgb(var(--border))] bg-[rgb(var(--surface-2))] sm:grid-cols-3 xl:grid-cols-6"
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
      <HealthLink
        label="Latest forecast"
        tab="calculations"
        onNavigate={onNavigate}
      >
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
      className="group min-w-0 border-b border-r border-[rgb(var(--border))] px-3 py-2 text-left outline-none last:border-r-0 hover:bg-[rgb(var(--muted))] focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[rgb(var(--focus))] xl:border-b-0"
      onClick={() => onNavigate(tab)}
    >
      <span className="flex min-w-0 items-center justify-between gap-1 text-[10px] font-medium uppercase tracking-[0.06em] text-[rgb(var(--muted-foreground))]">
        <span className="truncate">{label}</span>
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
        "block truncate text-sm font-semibold",
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
  const { error, warning, info, total } = workspace.validationSummary;
  if ([error, warning, info, total].every((value) => value === undefined))
    return <HealthValue tone="muted">Unknown</HealthValue>;
  if ((error ?? 0) > 0)
    return (
      <HealthValue tone="danger">
        <span className="tabular-nums">{error}</span> errors
      </HealthValue>
    );
  if ((warning ?? 0) > 0)
    return (
      <HealthValue tone="warning">
        <span className="tabular-nums">{warning}</span> warnings
      </HealthValue>
    );
  return (
    <HealthValue tone="success">
      Validated{(info ?? 0) > 0 ? ` · ${info} info` : ""}
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
  const counts = countSeverities(findings);
  const summary = (["critical", "high", "medium", "low"] as const)
    .map((severity) => `${counts[severity]} ${severity}`)
    .join(", ");
  return (
    <span
      aria-label={summary}
      className="flex min-w-0 flex-wrap gap-x-2 gap-y-0.5 text-xs font-semibold tabular-nums"
    >
      <SeverityCount severity="critical" count={counts.critical} />
      <SeverityCount severity="high" count={counts.high} />
      <SeverityCount severity="medium" count={counts.medium} />
      <SeverityCount severity="low" count={counts.low} />
    </span>
  );
}

function SeverityCount({
  severity,
  count,
}: {
  severity: "critical" | "high" | "low" | "medium";
  count: number;
}) {
  return (
    <span
      title={`${count} ${severity}`}
      className={cn(
        (severity === "critical" || severity === "high") &&
          "text-[rgb(var(--danger))]",
        severity === "medium" && "text-[rgb(var(--warning))]",
        severity === "low" && "text-[rgb(var(--success))]",
      )}
    >
      {severity.slice(0, 1).toUpperCase()}
      {count}
    </span>
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
