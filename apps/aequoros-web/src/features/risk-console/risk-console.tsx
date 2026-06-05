import { CaseSort } from "@aequoros/risk-service-api";
import {
  useMatch,
  useNavigate,
  useRouter,
} from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import {
  DEFAULT_ORG_ID,
  DEFAULT_USER_ID,
  type ConsoleTab,
  type ReportMode,
  isConsoleTab,
} from "../../lib/constants";
import { riskApi, type TenantHeaders } from "../../lib/api";
import { usePersistentState } from "../../lib/persistent-state";
import { mockCase, mockCaseList } from "../demo-data/demo-data";
import { CaseQueuePanel } from "./case-queue-panel";
import { CaseWorkspace } from "./case-workspace";
import { Sidebar, TopBar } from "./shell";
import { pageSize } from "./types";
import type { SearchState } from "../../routes/search";

export function RiskConsoleRoute() {
  const router = useRouter();
  const navigate = useNavigate();
  const match = useMatch({ strict: false });
  const params = match.params as { caseId?: string };
  const search = match.search as SearchState;
  const [orgId, setOrgId] = usePersistentState("aequoros.orgId", DEFAULT_ORG_ID);
  const [userId, setUserId] = usePersistentState("aequoros.userId", DEFAULT_USER_ID);
  const [lastCaseId, setLastCaseId] = usePersistentState("aequoros.caseId", "");
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [mockWorkspace, setMockWorkspace] = useState(false);
  const tenant = useMemo<TenantHeaders>(() => ({ orgId, userId }), [orgId, userId]);

  const activeTab: ConsoleTab =
    search.tab && isConsoleTab(search.tab) ? search.tab : "overview";
  const reportMode: ReportMode = search.report === "html" ? "html" : "json";
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
  const demoList = mockWorkspace ? mockCaseList(tenant.orgId, filters, page) : undefined;
  const demoCase = mockWorkspace && caseId ? mockCase(tenant.orgId, caseId) : undefined;

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
          orgId={orgId}
          userId={userId}
          setOrgId={setOrgId}
          setUserId={setUserId}
          cases={demoList?.items ?? casesQuery.data?.items ?? []}
          caseId={caseId}
          chooseCase={chooseCase}
          refresh={refresh}
          seed={() => {
            setMockWorkspace(true);
            toast.success("Frontend mock financial workspace enabled");
          }}
        />
        <div className="grid min-h-[calc(100vh-56px)] grid-cols-[minmax(420px,0.95fr)_minmax(440px,1.05fr)] gap-3 p-3 max-xl:grid-cols-1">
          <CaseQueuePanel
            query={casesQuery}
            taxonomy={taxonomyQuery.data}
            filters={filters}
            page={page}
            selected={selected}
            setSelected={setSelected}
            chooseCase={chooseCase}
            activeCaseId={caseId}
            selectedIds={selectedIds}
            tenant={tenant}
            updateSearch={updateSearch}
            mockList={demoList}
          />
          <CaseWorkspace
            tenant={tenant}
            caseId={caseId}
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
