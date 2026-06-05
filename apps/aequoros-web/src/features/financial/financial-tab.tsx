import { useQuery } from "@tanstack/react-query";

import { Alert, Skeleton } from "../../components/ui";
import { riskApi, type TenantHeaders } from "../../lib/api";
import { ErrorPanel } from "../../shared/route-ui";
import { emptyWorkspace } from "../demo-data/demo-data";
import { FinancialSections } from "./financial-sections";

export function FinancialTab({
  tenant,
  caseId,
  mockWorkspace,
}: {
  tenant: TenantHeaders;
  caseId: string;
  mockWorkspace: boolean;
}) {
  const query = useQuery({
    queryKey: ["financial-workspace", tenant, caseId],
    queryFn: () => riskApi.financialWorkspace(tenant, caseId),
  });
  const workspace = query.data ?? (mockWorkspace ? emptyWorkspace(tenant.orgId, caseId) : undefined);

  return (
    <div className="space-y-3">
      {mockWorkspace ? (
        <Alert title="Mocked demo seed data" tone="warning">
          Financial workspace data is frontend-only until backend write APIs can seed canonical records.
        </Alert>
      ) : null}
      {query.isError && !workspace ? <ErrorPanel error={query.error} /> : null}
      {!workspace ? <Skeleton className="h-96" /> : <FinancialSections workspace={workspace} mocked={mockWorkspace && !query.data} />}
    </div>
  );
}
