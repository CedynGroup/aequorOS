import type {
  BankRead,
  BankReportingPeriodRead,
} from "@aequoros/risk-service-api";
import { useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, type ReactNode } from "react";

import { Alert, Panel, PanelHeader, Skeleton } from "../../components/ui";
import type { TenantHeaders } from "../../lib/api";
import { type AlmTab, type ConsoleMode, isAlmTab } from "../../lib/constants";
import { ErrorPanel } from "../../shared/route-ui";
import type { SearchState } from "../../routes/search";
import type { UpdateSearch } from "../risk-console/types";
import { NoBanksPanel, useAlmWorkspace } from "./alm-context";
import { AlmSidebar, AlmTopBar } from "./alm-shell";

const AlmOverviewTab = lazy(() =>
  import("./overview-tab").then((module) => ({
    default: module.AlmOverviewTab,
  })),
);
const LcrTab = lazy(() =>
  import("./lcr-tab").then((module) => ({ default: module.LcrTab })),
);
const NsfrTab = lazy(() =>
  import("./nsfr-tab").then((module) => ({ default: module.NsfrTab })),
);
const LiquidityStressTab = lazy(() =>
  import("./liq-stress-tab").then((module) => ({
    default: module.LiquidityStressTab,
  })),
);
const AlmCapitalTab = lazy(() =>
  import("./capital-tab").then((module) => ({
    default: module.AlmCapitalTab,
  })),
);
const RwaTab = lazy(() =>
  import("./rwa-tab").then((module) => ({ default: module.RwaTab })),
);
const CapitalStructureTab = lazy(() =>
  import("./structure-tab").then((module) => ({
    default: module.CapitalStructureTab,
  })),
);
const CapitalStressTab = lazy(() =>
  import("./capital-stress-tab").then((module) => ({
    default: module.CapitalStressTab,
  })),
);
const SubmissionsTab = lazy(() =>
  import("./submissions-tab").then((module) => ({
    default: module.SubmissionsTab,
  })),
);

export type AlmTabProps = {
  tenant: TenantHeaders;
  bank: BankRead;
  period: BankReportingPeriodRead;
  onTab: (tab: AlmTab) => void;
};

const placeholderTitles: Partial<Record<AlmTab, string>> = {
  cashflow: "Cash Flow Forecast",
  forecast: "Balance Sheet Forecast",
  optimizer: "Strategic Optimizer",
  whatif: "What-If Analysis",
};

export function AlmConsole({
  tenant,
  orgId,
  userId,
  setOrgId,
  setUserId,
  search,
  updateSearch,
  refresh,
}: {
  tenant: TenantHeaders;
  orgId: string;
  userId: string;
  setOrgId: (value: string) => void;
  setUserId: (value: string) => void;
  search: SearchState;
  updateSearch: UpdateSearch;
  refresh: () => void;
}) {
  const queryClient = useQueryClient();
  const activeTab: AlmTab =
    search.almTab && isAlmTab(search.almTab) ? search.almTab : "overview";
  const workspace = useAlmWorkspace(tenant, search.bankId, search.periodId);
  const onTab = (almTab: AlmTab) => updateSearch({ almTab });
  const onMode = (mode: ConsoleMode) => updateSearch({ mode });
  const refreshAlm = () => {
    void queryClient.invalidateQueries({
      predicate: (query) => String(query.queryKey[0]).startsWith("alm-"),
    });
    refresh();
  };

  return (
    <div className="flex min-h-screen bg-[rgb(var(--background))]">
      <AlmSidebar
        activeTab={activeTab}
        onTab={onTab}
        mode="alm"
        onMode={onMode}
      />
      <main className="min-w-0 flex-1">
        <AlmTopBar
          orgId={orgId}
          userId={userId}
          setOrgId={setOrgId}
          setUserId={setUserId}
          workspace={workspace}
          onBank={(bankId) => updateSearch({ bankId, periodId: undefined })}
          onPeriod={(periodId) => updateSearch({ periodId })}
          refresh={refreshAlm}
        />
        <div className="min-h-[calc(100vh-56px)] p-3">
          <AlmWorkspaceBoundary
            tenant={tenant}
            workspace={workspace}
            activeTab={activeTab}
            onTab={onTab}
          />
        </div>
      </main>
    </div>
  );
}

function AlmWorkspaceBoundary({
  tenant,
  workspace,
  activeTab,
  onTab,
}: {
  tenant: TenantHeaders;
  workspace: ReturnType<typeof useAlmWorkspace>;
  activeTab: AlmTab;
  onTab: (tab: AlmTab) => void;
}) {
  if (workspace.banksQuery.isLoading) {
    return (
      <div aria-label="Loading ALM workspace" className="space-y-3">
        <Skeleton className="h-24" />
        <Skeleton className="h-64" />
      </div>
    );
  }
  if (workspace.banksQuery.error) {
    return <ErrorPanel error={workspace.banksQuery.error} />;
  }
  if (!workspace.selectedBank) {
    return <NoBanksPanel tenant={tenant} />;
  }
  if (workspace.periodsQuery.isLoading) {
    return (
      <div aria-label="Loading reporting periods" className="space-y-3">
        <Skeleton className="h-24" />
        <Skeleton className="h-64" />
      </div>
    );
  }
  if (workspace.periodsQuery.error) {
    return <ErrorPanel error={workspace.periodsQuery.error} />;
  }
  if (!workspace.selectedPeriod) {
    return (
      <Alert title="No reporting periods">
        {workspace.selectedBank.name} has no reporting periods yet, so no
        regulatory metrics can be calculated.
      </Alert>
    );
  }

  const props: AlmTabProps = {
    tenant,
    bank: workspace.selectedBank,
    period: workspace.selectedPeriod,
    onTab,
  };
  const placeholderTitle = placeholderTitles[activeTab];

  return (
    <LazyTabBoundary>
      {activeTab === "overview" ? <AlmOverviewTab {...props} /> : null}
      {activeTab === "lcr" ? <LcrTab {...props} /> : null}
      {activeTab === "nsfr" ? <NsfrTab {...props} /> : null}
      {activeTab === "liq-stress" ? <LiquidityStressTab {...props} /> : null}
      {activeTab === "capital" ? <AlmCapitalTab {...props} /> : null}
      {activeTab === "rwa" ? <RwaTab {...props} /> : null}
      {activeTab === "structure" ? <CapitalStructureTab {...props} /> : null}
      {activeTab === "capital-stress" ? <CapitalStressTab {...props} /> : null}
      {activeTab === "submissions" ? <SubmissionsTab {...props} /> : null}
      {placeholderTitle ? (
        <Panel>
          <PanelHeader
            title={placeholderTitle}
            meta={`${workspace.selectedBank.name} · ${workspace.selectedPeriod.label}`}
          />
          <div className="p-4">
            <Alert title="Not yet available">
              This workspace ships with the forecasting build increment.
            </Alert>
          </div>
        </Panel>
      ) : null}
    </LazyTabBoundary>
  );
}

function LazyTabBoundary({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={<Skeleton className="h-96" />}>{children}</Suspense>
  );
}
