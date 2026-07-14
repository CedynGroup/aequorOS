import type { CaseRead } from "@aequoros/risk-service-api";
import { Loader2 } from "lucide-react";
import { lazy, Suspense, type ReactNode } from "react";

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
import type { ConsoleTab, ReportMode } from "../../lib/constants";
import { formatJson, labelize, truncateId } from "../../lib/utils";
import { ErrorPanel } from "../../shared/route-ui";
import { DecisionBadge, RiskBadge, StatusBadge, relative } from "./format";
import type { UpdateSearch } from "./types";

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
  mockCaseData?: CaseRead;
  mockWorkspace: boolean;
}) {
  const selectedCase = mockCaseData ?? (caseQuery.data as CaseRead | undefined);
  const caseRetired = Boolean(
    selectedCase?.archivedAt || selectedCase?.status === "archived",
  );

  return (
    <Panel className="min-h-[640px] overflow-hidden">
      <PanelHeader
        title="Case Detail"
        meta={caseId ? truncateId(caseId) : "Select a case from the queue"}
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
            <TabsContent value="overview" className="m-0 p-3">
              <OverviewTab caseData={selectedCase} />
            </TabsContent>
            <TabsContent value="financial" className="m-0 p-3">
              <LazyTabBoundary>
                <FinancialTab
                  tenant={tenant}
                  caseId={caseId}
                  mockWorkspace={mockWorkspace}
                />
              </LazyTabBoundary>
            </TabsContent>
            <TabsContent value="scenarios" className="m-0 p-3">
              <LazyTabBoundary>
                <ScenariosTab tenant={tenant} caseId={caseId} />
              </LazyTabBoundary>
            </TabsContent>
            <TabsContent value="calculations" className="m-0 p-3">
              <LazyTabBoundary>
                <CalculationsTab tenant={tenant} caseId={caseId} />
              </LazyTabBoundary>
            </TabsContent>
            <TabsContent value="capital" className="m-0 p-3">
              <LazyTabBoundary>
                <CapitalTab
                  tenant={tenant}
                  caseId={caseId}
                  mutationDisabled={mockWorkspace || caseRetired}
                  mutationDisabledReason={caseRetired ? "retired-case" : "demo"}
                />
              </LazyTabBoundary>
            </TabsContent>
            <TabsContent value="liquidity" className="m-0 p-3">
              <LazyTabBoundary>
                <LiquidityTab
                  tenant={tenant}
                  caseId={caseId}
                  mutationDisabled={mockWorkspace || caseRetired}
                  mutationDisabledReason={caseRetired ? "retired-case" : "demo"}
                />
              </LazyTabBoundary>
            </TabsContent>
            <TabsContent value="findings" className="m-0 p-3">
              <LazyTabBoundary>
                <FindingsTab
                  tenant={tenant}
                  caseId={caseId}
                  mutationDisabled={mockWorkspace || caseRetired}
                  mutationDisabledReason={caseRetired ? "retired-case" : "demo"}
                />
              </LazyTabBoundary>
            </TabsContent>
            <TabsContent value="decisions" className="m-0 p-3">
              <LazyTabBoundary>
                <DecisionsTab tenant={tenant} caseId={caseId} />
              </LazyTabBoundary>
            </TabsContent>
            <TabsContent value="documents" className="m-0 p-3">
              <LazyTabBoundary>
                <DocumentsTab tenant={tenant} caseId={caseId} />
              </LazyTabBoundary>
            </TabsContent>
            <TabsContent value="report" className="m-0 p-3">
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
        <span>{truncateId(data.assignedToUserId)}</span>
        <span>Created</span>
        <span>{relative(data.createdAt)}</span>
        <span>Updated</span>
        <span>{relative(data.updatedAt)}</span>
      </div>
    </div>
  );
}

function OverviewTab({ caseData }: { caseData?: CaseRead }) {
  if (!caseData) return <Skeleton className="h-52" />;
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
