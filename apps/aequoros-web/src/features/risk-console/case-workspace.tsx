import type { CaseRead } from "@aequoros/risk-service-api";
import { Loader2 } from "lucide-react";
import {
  lazy,
  Suspense,
  type ReactNode,
  useEffect,
  useMemo,
  useState,
} from "react";
import { useQuery } from "@tanstack/react-query";

import {
  Alert,
  Panel,
  PanelHeader,
  Skeleton,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "../../components/ui";
import type { TenantHeaders } from "../../lib/api";
import { riskApi } from "../../lib/api";
import type { ConsoleTab, ReportMode } from "../../lib/constants";
import { formatJson, labelize } from "../../lib/utils";
import { ErrorPanel } from "../../shared/route-ui";
import { focusWorkspaceTarget } from "../../lib/workspace-deep-link";
import { CaseHealthHeader } from "./case-health-header";
import { mockCaseHealth } from "../demo-data/demo-data";
import { DecisionBadge, RiskBadge, StatusBadge, relative } from "./format";
import type { UpdateSearch } from "./types";
import type { MockCaseRead } from "../demo-data/demo-data";

const FinancialTab = lazy(() =>
  import("../financial/financial-tab").then((module) => ({
    default: module.FinancialTab,
  })),
);
const ScenariosTab = lazy(() =>
  import("../scenarios/scenarios-tab").then((module) => ({
    default: module.ScenariosTab,
  })),
);
const CalculationsTab = lazy(() =>
  import("../calculations/calculations-tab").then((module) => ({
    default: module.CalculationsTab,
  })),
);
const CapitalTab = lazy(() =>
  import("../capital/capital-tab").then((module) => ({
    default: module.CapitalTab,
  })),
);
const FindingsTab = lazy(() =>
  import("../findings/findings-tab").then((module) => ({
    default: module.FindingsTab,
  })),
);
const LiquidityTab = lazy(() =>
  import("../liquidity/liquidity-tab").then((module) => ({
    default: module.LiquidityTab,
  })),
);
const DecisionsTab = lazy(() =>
  import("../decisions/decisions-tab").then((module) => ({
    default: module.DecisionsTab,
  })),
);
const DocumentsTab = lazy(() =>
  import("../documents/documents-tab").then((module) => ({
    default: module.DocumentsTab,
  })),
);
const ReportTab = lazy(() =>
  import("../reports/report-tab").then((module) => ({
    default: module.ReportTab,
  })),
);

export function CaseWorkspace({
  tenant,
  caseId,
  activeTab,
  reportMode,
  updateSearch,
  caseQuery,
  mockCaseData,
  mockWorkspace,
}: {
  tenant: TenantHeaders;
  caseId?: string;
  activeTab: ConsoleTab;
  reportMode: ReportMode;
  updateSearch: UpdateSearch;
  caseQuery: {
    data?: unknown;
    error: unknown;
    isError: boolean;
    isFetching: boolean;
  };
  mockCaseData?: MockCaseRead;
  mockWorkspace: boolean;
}) {
  const selectedCase = mockCaseData ?? (caseQuery.data as CaseRead | undefined);
  const caseRetired = Boolean(
    selectedCase?.archivedAt || selectedCase?.status === "archived",
  );
  const [pendingHealthTab, setPendingHealthTab] = useState<ConsoleTab | null>(
    null,
  );
  const demoData = useMemo(
    () =>
      mockWorkspace && caseId
        ? mockCaseHealth(tenant.orgId, caseId)
        : undefined,
    [caseId, mockWorkspace, tenant.orgId],
  );

  useEffect(() => {
    if (pendingHealthTab !== activeTab) return;
    const frame = window.requestAnimationFrame(() => {
      if (focusWorkspaceTarget(`case-health-target-${activeTab}`)) {
        setPendingHealthTab(null);
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, [activeTab, pendingHealthTab]);

  return (
    <Panel className="min-h-[640px] overflow-hidden">
      <PanelHeader
        title="Case Detail"
        meta={caseId ? "Borrower risk review" : "Select a case from the queue"}
        actions={
          caseQuery.isFetching ? (
            <Loader2 className="size-4 animate-spin" />
          ) : null
        }
      />
      {!caseId ? (
        <div className="p-4">
          <Alert title="No case selected">
            Select a case from the queue or current case selector.
          </Alert>
        </div>
      ) : caseQuery.isError && !mockCaseData ? (
        <div className="p-4">
          <ErrorPanel error={caseQuery.error} />
        </div>
      ) : (
        <>
          <CaseSummary data={selectedCase} />
          <CaseHealthHeader
            tenant={tenant}
            caseId={caseId}
            decision={selectedCase?.decision}
            demoData={demoData}
            onNavigate={(tab) => {
              setPendingHealthTab(tab);
              updateSearch({ tab });
            }}
          />
          <Tabs
            value={activeTab}
            onValueChange={(tab) => updateSearch({ tab: tab as ConsoleTab })}
          >
            <div className="border-b border-[rgb(var(--border))] bg-[rgb(var(--surface-2))] px-3 py-2">
              <TabsList className="flex flex-wrap gap-1">
                <TabsTrigger value="overview">Overview</TabsTrigger>
                <TabsTrigger value="financial">Financial Workspace</TabsTrigger>
                <TabsTrigger value="scenarios">Scenarios</TabsTrigger>
                <TabsTrigger value="calculations">Forecast</TabsTrigger>
                <TabsTrigger value="capital">Capital</TabsTrigger>
                <TabsTrigger value="liquidity">Liquidity</TabsTrigger>
                <TabsTrigger value="findings">Findings</TabsTrigger>
                <TabsTrigger value="decisions">Decisions</TabsTrigger>
                <TabsTrigger value="documents">Documents</TabsTrigger>
                <TabsTrigger value="report">Report</TabsTrigger>
              </TabsList>
            </div>
            <TabsContent
              id="case-health-target-overview"
              tabIndex={-1}
              value="overview"
              className="m-0 p-3 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[rgb(var(--focus))]"
            >
              <OverviewTab
                tenant={tenant}
                caseId={caseId}
                caseData={selectedCase}
                loadScores={!mockCaseData}
                scoreRunReference={mockCaseData?.scoreRunReference}
              />
            </TabsContent>
            <TabsContent
              id="case-health-target-financial"
              tabIndex={-1}
              value="financial"
              className="m-0 p-3 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[rgb(var(--focus))]"
            >
              <LazyTabBoundary>
                <FinancialTab
                  tenant={tenant}
                  caseId={caseId}
                  mockWorkspace={mockWorkspace}
                  demoWorkspace={demoData?.financial}
                />
              </LazyTabBoundary>
            </TabsContent>
            <TabsContent
              id="case-health-target-scenarios"
              tabIndex={-1}
              value="scenarios"
              className="m-0 p-3 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[rgb(var(--focus))]"
            >
              <LazyTabBoundary>
                <ScenariosTab
                  tenant={tenant}
                  caseId={caseId}
                  mutationDisabled={mockWorkspace || caseRetired}
                  demoData={demoData}
                />
              </LazyTabBoundary>
            </TabsContent>
            <TabsContent
              id="case-health-target-calculations"
              tabIndex={-1}
              value="calculations"
              className="m-0 p-3 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[rgb(var(--focus))]"
            >
              <LazyTabBoundary>
                <CalculationsTab
                  tenant={tenant}
                  caseId={caseId}
                  mutationDisabled={mockWorkspace || caseRetired}
                  mutationDisabledReason={caseRetired ? "retired-case" : "demo"}
                  demoData={demoData}
                />
              </LazyTabBoundary>
            </TabsContent>
            <TabsContent
              id="case-health-target-capital"
              tabIndex={-1}
              value="capital"
              className="m-0 p-3 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[rgb(var(--focus))]"
            >
              <LazyTabBoundary>
                <CapitalTab
                  tenant={tenant}
                  caseId={caseId}
                  mutationDisabled={mockWorkspace || caseRetired}
                  mutationDisabledReason={caseRetired ? "retired-case" : "demo"}
                />
              </LazyTabBoundary>
            </TabsContent>
            <TabsContent
              id="case-health-target-liquidity"
              tabIndex={-1}
              value="liquidity"
              className="m-0 p-3 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[rgb(var(--focus))]"
            >
              <LazyTabBoundary>
                <LiquidityTab
                  tenant={tenant}
                  caseId={caseId}
                  mutationDisabled={mockWorkspace || caseRetired}
                  mutationDisabledReason={caseRetired ? "retired-case" : "demo"}
                />
              </LazyTabBoundary>
            </TabsContent>
            <TabsContent
              id="case-health-target-findings"
              tabIndex={-1}
              value="findings"
              className="m-0 p-3 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[rgb(var(--focus))]"
            >
              <LazyTabBoundary>
                <FindingsTab
                  tenant={tenant}
                  caseId={caseId}
                  mutationDisabled={mockWorkspace || caseRetired}
                  mutationDisabledReason={caseRetired ? "retired-case" : "demo"}
                  demoFindings={demoData?.findings}
                />
              </LazyTabBoundary>
            </TabsContent>
            <TabsContent
              id="case-health-target-decisions"
              tabIndex={-1}
              value="decisions"
              className="m-0 p-3 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[rgb(var(--focus))]"
            >
              <LazyTabBoundary>
                <DecisionsTab
                  tenant={tenant}
                  caseId={caseId}
                  mutationDisabled={mockWorkspace || caseRetired}
                  mutationDisabledReason={caseRetired ? "retired-case" : "demo"}
                  demoDecisions={demoData?.decisions}
                />
              </LazyTabBoundary>
            </TabsContent>
            <TabsContent
              id="case-health-target-documents"
              tabIndex={-1}
              value="documents"
              className="m-0 p-3 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[rgb(var(--focus))]"
            >
              <LazyTabBoundary>
                <DocumentsTab
                  tenant={tenant}
                  caseId={caseId}
                  mutationDisabled={mockWorkspace || caseRetired}
                />
              </LazyTabBoundary>
            </TabsContent>
            <TabsContent
              id="case-health-target-report"
              tabIndex={-1}
              value="report"
              className="m-0 p-3 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[rgb(var(--focus))]"
            >
              <LazyTabBoundary>
                <ReportTab
                  tenant={tenant}
                  caseId={caseId}
                  mode={reportMode}
                  setMode={(report) => updateSearch({ report })}
                />
              </LazyTabBoundary>
            </TabsContent>
          </Tabs>
        </>
      )}
    </Panel>
  );
}

function LazyTabBoundary({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={<Skeleton className="h-96" />}>{children}</Suspense>
  );
}

function CaseSummary({ data }: { data?: CaseRead }) {
  if (!data) {
    return (
      <div className="space-y-2 p-3">
        <Skeleton className="h-12" />
        <Skeleton className="h-8" />
      </div>
    );
  }
  return (
    <div className="grid gap-3 border-b border-[rgb(var(--border))] p-3 md:grid-cols-[1fr_auto]">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="truncate text-lg font-semibold">{data.title}</h1>
          <StatusBadge value={data.status} />
          <RiskBadge value={data.riskLevel} />
          <DecisionBadge value={data.decision} />
        </div>
        <p className="mt-1 line-clamp-2 text-sm text-[rgb(var(--muted-foreground))]">
          {data.description ?? "No case description provided."}
        </p>
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-[rgb(var(--muted-foreground))]">
        <span>Assignee</span>
        <span>
          {data.assignedToUserId
            ? data.assigneeDisplayName?.trim() || "Unknown reviewer"
            : "Unassigned"}
        </span>
        <span>Created</span>
        <span>{relative(data.createdAt)}</span>
        <span>Updated</span>
        <span>{relative(data.updatedAt)}</span>
      </div>
    </div>
  );
}

function OverviewTab({
  tenant,
  caseId,
  caseData,
  loadScores,
  scoreRunReference,
}: {
  tenant: TenantHeaders;
  caseId: string;
  caseData?: CaseRead;
  loadScores: boolean;
  scoreRunReference?: string;
}) {
  const scoresQuery = useQuery({
    queryKey: ["case-scores", tenant, caseId],
    queryFn: () => riskApi.scores(tenant, caseId),
    enabled: loadScores && caseData?.riskScore != null,
  });
  if (!caseData) return <Skeleton className="h-52" />;
  const latestScore = scoresQuery.data?.[0];
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <InfoBlock title="Workflow">
        <KeyValue label="Case type" value={caseData.caseType} />
        <KeyValue label="Subject" value={caseData.subjectName ?? "None"} />
        <KeyValue label="Subject type" value={caseData.subjectType ?? "None"} />
        <KeyValue
          label="Archive action"
          value="Unavailable in this UI unless exposed by the new API contract"
        />
      </InfoBlock>
      <InfoBlock title="Risk">
        <KeyValue
          label="Risk score"
          value={caseData.riskScore?.toString() ?? "Not scored"}
        />
        <KeyValue label="Risk level" value={labelize(caseData.riskLevel)} />
        <KeyValue
          label="Scoring version"
          value={caseData.scoringVersion ?? "None"}
        />
        {caseData.riskScore != null ? (
          <KeyValue
            label="Assessment run"
            value={
              latestScore?.runReference ??
              scoreRunReference ??
              "Reference unavailable"
            }
          />
        ) : null}
        <KeyValue label="Decision" value={labelize(caseData.decision)} />
      </InfoBlock>
      <InfoBlock title="Metadata">
        <pre className="max-h-52 overflow-auto rounded bg-[rgb(var(--surface-2))] p-2 text-xs">
          {formatJson(caseData.metadata)}
        </pre>
      </InfoBlock>
      <InfoBlock title="Controls">
        <Alert title="Assign, unassign, and archive">
          Use the Case Queue bulk action dialog. Single-case actions are left
          disabled to avoid calling deprecated routes.
        </Alert>
      </InfoBlock>
    </div>
  );
}

function InfoBlock({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="rounded-md border border-[rgb(var(--border))] p-3">
      <div className="mb-2 text-xs font-semibold uppercase tracking-[0.04em] text-[rgb(var(--muted-foreground))]">
        {title}
      </div>
      <div className="space-y-2">{children}</div>
    </div>
  );
}

function KeyValue({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[120px_1fr] gap-2 text-xs">
      <div className="text-[rgb(var(--muted-foreground))]">{label}</div>
      <div>{value}</div>
    </div>
  );
}
