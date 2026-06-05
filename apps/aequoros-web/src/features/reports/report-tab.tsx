import { useQuery } from "@tanstack/react-query";

import { Button, Skeleton } from "../../components/ui";
import { riskApi, type TenantHeaders } from "../../lib/api";
import type { ReportMode } from "../../lib/constants";
import { formatJson } from "../../lib/utils";
import { ErrorPanel } from "../../shared/route-ui";

export function ReportTab({
  tenant,
  caseId,
  mode,
  setMode,
}: {
  tenant: TenantHeaders;
  caseId: string;
  mode: ReportMode;
  setMode: (mode: ReportMode) => void;
}) {
  const jsonQuery = useQuery({
    queryKey: ["report-json", tenant, caseId],
    queryFn: () => riskApi.reportJson(tenant, caseId),
    enabled: mode === "json",
  });
  const htmlQuery = useQuery({
    queryKey: ["report-html", tenant, caseId],
    queryFn: () => riskApi.reportHtml(tenant, caseId),
    enabled: mode === "html",
  });
  const error = mode === "json" ? jsonQuery.error : htmlQuery.error;

  return (
    <div className="space-y-3">
      <div className="inline-flex rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--muted))] p-1">
        <Button size="sm" variant={mode === "json" ? "default" : "ghost"} onClick={() => setMode("json")}>JSON</Button>
        <Button size="sm" variant={mode === "html" ? "default" : "ghost"} onClick={() => setMode("html")}>HTML</Button>
      </div>
      {error ? <ErrorPanel error={error} /> : null}
      {mode === "json" ? (
        jsonQuery.isLoading ? <Skeleton className="h-96" /> : (
          <pre className="max-h-[520px] overflow-auto rounded-md border border-[rgb(var(--border))] bg-slate-950 p-3 text-xs text-slate-100">
            {formatJson(jsonQuery.data)}
          </pre>
        )
      ) : (
        htmlQuery.isLoading ? <Skeleton className="h-96" /> : (
          <iframe title="Risk report HTML preview" sandbox="" srcDoc={htmlQuery.data ?? ""} className="h-[520px] w-full rounded-md border border-[rgb(var(--border))] bg-white" />
        )
      )}
    </div>
  );
}
