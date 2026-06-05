import type { CaseBulkActionRead } from "@aequoros/risk-service-api";

import { labelize, truncateId } from "../../lib/utils";
import { EmptyRow } from "../../shared/route-ui";

export function BulkResult({ result }: { result: CaseBulkActionRead }) {
  return (
    <div className="grid gap-3 border-t border-[rgb(var(--border))] pt-3">
      <div>
        <div className="mb-2 text-xs font-semibold uppercase tracking-[0.04em] text-emerald-800">
          Succeeded ({result.succeeded.length})
        </div>
        <div className="space-y-1">
          {result.succeeded.length === 0 ? <EmptyRow label="No successful updates" /> : null}
          {result.succeeded.map((item) => (
            <div key={item.caseId} className="rounded border border-emerald-200 bg-emerald-50 px-2 py-1 text-xs">
              {truncateId(item.caseId)}: {labelize(item.status)}
            </div>
          ))}
        </div>
      </div>
      <div>
        <div className="mb-2 text-xs font-semibold uppercase tracking-[0.04em] text-red-800">
          Failed ({result.failed.length})
        </div>
        <div className="space-y-1">
          {result.failed.length === 0 ? <EmptyRow label="No failures" /> : null}
          {result.failed.map((item) => (
            <div key={item.caseId} className="rounded border border-red-200 bg-red-50 px-2 py-1 text-xs">
              <span className="font-medium">{truncateId(item.caseId)}</span> status {item.statusCode}: {item.error.code} - {item.error.message}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
