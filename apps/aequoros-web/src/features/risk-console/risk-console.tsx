import { CaseSort } from "@aequoros/risk-service-api";
import { useMatch, useNavigate, useRouter } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import {
  DEFAULT_ORG_ID,
  DEFAULT_USER_ID,
  activeTenantOption,
  type ConsoleTab,
  type ReportMode,
  type TenantDirectory,
  isConsoleTab,
  tenantConfiguration,
} from "../../lib/constants";
import { riskApi, type TenantHeaders } from "../../lib/api";
import { usePersistentState } from "../../lib/persistent-state";
import { DEMO_CASE_IDS, mockCase, mockCaseList } from "../demo-data/demo-data";
import { CaseQueuePanel } from "./case-queue-panel";
import { CaseWorkspace } from "./case-workspace";
import { Sidebar, TopBar } from "./shell";
import { pageSize } from "./types";
import type { SearchState } from "../../routes/search";

export function RiskConsoleRoute() {
  const configuration = useMemo(tenantConfiguration, []);

  if (configuration.status === "error") {
    return (
      <main className="flex min-h-screen items-center justify-center bg-[rgb(var(--background))] p-6">
        <section
          role="alert"
          className="w-full max-w-xl rounded-lg border border-red-300 bg-[rgb(var(--surface))] p-6 shadow-sm"
        >
          <h1 className="text-lg font-semibold">Tenant configuration error</h1>
          <p className="mt-2 text-sm text-[rgb(var(--muted-foreground))]">
            {configuration.error} Correct the deployment configuration and
            reload the console.
          </p>
        </section>
      </main>
    );
  }

  return <RiskConsole tenants={configuration.tenants} />;
}

function RiskConsole({ tenants }: { tenants: TenantDirectory }) {
  const router = useRouter();
  const navigate = useNavigate();
  const match = useMatch({ strict: false });
  const params = match.params as { caseId?: string };
  const search = match.search as SearchState;
  const [orgId, setOrgId] = usePersistentState(
    "aequoros.orgId",
    tenants[0]?.orgId ?? DEFAULT_ORG_ID,
  );
  const [userId, setUserId] = usePersistentState(
    "aequoros.userId",
    tenants[0]?.userId ?? DEFAULT_USER_ID,
  );
  const [lastCaseId, setLastCaseId] = usePersistentState("aequoros.caseId", "");
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [mockWorkspace, setMockWorkspace] = useState(false);
  const [queueVisible, setQueueVisible] = useState(() => !params.caseId);
  const activeTenant = activeTenantOption(tenants, orgId);
  const tenant = useMemo<TenantHeaders>(
    () => ({ orgId: activeTenant.orgId, userId: activeTenant.userId }),
    [activeTenant.orgId, activeTenant.userId],
  );

  useEffect(() => {
    if (orgId !== activeTenant.orgId) setOrgId(activeTenant.orgId);
    if (userId !== activeTenant.userId) setUserId(activeTenant.userId);
  }, [activeTenant, orgId, setOrgId, setUserId, userId]);

  const activeTab: ConsoleTab =
    search.tab && isConsoleTab(search.tab) ? search.tab : "overview";
  const reportMode: ReportMode = search.report === "json" ? "json" : "html";
  const page = Math.max(Number(search.page ?? 1), 1);
  const filters = {
    q: search.q ?? "",
    status: search.status ?? "all",
    risk: search.risk ?? "all",
    archived: Boolean(search.archived),
    sort: search.sort ?? CaseSort.UpdatedAtDesc,
  };
  const caseId = params.caseId ?? (lastCaseId || undefined);

  const taxonomyQuery = useQuery({
    queryKey: ["case-taxonomy", tenant],
    queryFn: () => riskApi.caseTaxonomy(tenant),
  });
  const casesQuery = useQuery({
    queryKey: ["cases", tenant, filters, page],
    queryFn: () =>
      riskApi.listCases(tenant, {
        includeArchived: filters.archived,
        status: filters.status === "all" ? undefined : filters.status,
        riskLevel: filters.risk === "all" ? undefined : filters.risk,
        q: filters.q,
        sort: filters.sort,
        limit: pageSize,
        offset: (page - 1) * pageSize,
      }),
  });
  const caseQuery = useQuery({
    queryKey: ["case", tenant, caseId],
    queryFn: () => riskApi.getCase(tenant, caseId ?? ""),
    enabled: Boolean(caseId),
  });
  const demoList = mockWorkspace
    ? mockCaseList(tenant.orgId, filters, page)
    : undefined;
  const workspaceCaseId =
    mockWorkspace && !DEMO_CASE_IDS.some((id) => id === caseId)
      ? DEMO_CASE_IDS[0]
      : caseId;
  const demoCase =
    mockWorkspace && workspaceCaseId
      ? mockCase(tenant.orgId, workspaceCaseId)
      : undefined;

  const updateSearch = (next: Partial<SearchState>) => {
    void navigate({
      to: caseId ? "/cases/$caseId" : "/cases",
      params: caseId ? { caseId } : undefined,
      search: { ...search, ...next },
    });
  };

  const chooseCase = (nextCaseId: string) => {
    setLastCaseId(nextCaseId);
    void navigate({
      to: "/cases/$caseId",
      params: { caseId: nextCaseId },
      search: { ...search, tab: search.tab ?? "overview" },
    });
  };

  const chooseTenant = (nextOrgId: string) => {
    const nextTenant = tenants.find((item) => item.orgId === nextOrgId);
    if (!nextTenant) return;
    setOrgId(nextTenant.orgId);
    setUserId(nextTenant.userId);
    setLastCaseId("");
    setSelected({});
    setMockWorkspace(false);
    setQueueVisible(true);
    void navigate({ to: "/cases", search: { ...search, page: 1 } });
  };

  const refresh = () => {
    void router.invalidate();
    toast.info("Refreshing risk console data");
  };

  const selectedIds = Object.entries(selected)
    .filter((entry) => entry[1])
    .map(([id]) => id);

  return (
    <div className="flex min-h-screen bg-[rgb(var(--background))]">
      <Sidebar activeTab={activeTab} onTab={(tab) => updateSearch({ tab })} />
      <main className="min-w-0 flex-1">
        <TopBar
          orgId={tenant.orgId}
          tenants={tenants}
          chooseTenant={chooseTenant}
          cases={demoList?.items ?? casesQuery.data?.items ?? []}
          caseId={workspaceCaseId}
          chooseCase={chooseCase}
          queueVisible={queueVisible}
          toggleQueue={() => setQueueVisible((visible) => !visible)}
          refresh={refresh}
          seed={() => {
            setMockWorkspace(true);
            toast.success("Frontend mock financial workspace enabled");
          }}
        />
        <div
          data-testid="risk-console-master-detail"
          className={`grid min-h-[calc(100vh-56px)] gap-3 p-3 ${
            queueVisible
              ? "grid-cols-[minmax(360px,0.72fr)_minmax(0,1.28fr)] max-xl:grid-cols-1"
              : "grid-cols-1"
          }`}
        >
          {queueVisible ? (
            <CaseQueuePanel
              query={casesQuery}
              taxonomy={taxonomyQuery.data}
              filters={filters}
              page={page}
              selected={selected}
              setSelected={setSelected}
              chooseCase={chooseCase}
              activeCaseId={workspaceCaseId}
              selectedIds={selectedIds}
              tenant={tenant}
              updateSearch={updateSearch}
              mockList={demoList}
            />
          ) : null}
          <CaseWorkspace
            tenant={tenant}
            caseId={workspaceCaseId}
            activeTab={activeTab}
            reportMode={reportMode}
            updateSearch={updateSearch}
            caseQuery={caseQuery}
            mockCaseData={demoCase}
            mockWorkspace={mockWorkspace}
          />
        </div>
      </main>
    </div>
  );
}
