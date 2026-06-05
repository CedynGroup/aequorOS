import type { FinancialDataWorkspaceRead } from "@aequoros/risk-service-api";
import { formatDistanceToNowStrict } from "date-fns";

import { labelize, truncateId } from "../../lib/utils";

export function FinancialSections({
  workspace,
  mocked,
}: {
  workspace: FinancialDataWorkspaceRead;
  mocked: boolean;
}) {
  const sections = [
    { title: "institutions", rows: workspace.institutions, columns: ["name", "institutionType", "referenceCode", "metadata"] },
    { title: "accounts", rows: workspace.accounts, columns: ["accountName", "accountNumber", "accountType", "currency", "status", "institutionId"] },
    { title: "reporting_periods", rows: workspace.reportingPeriods, columns: ["label", "periodType", "startDate", "endDate", "asOfDate"] },
    { title: "balances", rows: workspace.balances, columns: ["balanceType", "amount", "currency", "asOfDate", "accountId", "reportingPeriodId"] },
    { title: "obligations", rows: workspace.obligations, columns: ["obligationType", "facilityType", "principalAmount", "outstandingAmount", "currency", "maturityDate", "interestRate", "status"] },
    { title: "source_rows", rows: workspace.sourceRows, columns: ["rowIndex", "locator", "rawPayload", "documentId"] },
    { title: "record_source_links", rows: workspace.recordSourceLinks, columns: ["recordTable", "recordId", "sourceRowId", "confidence", "metadata"] },
    { title: "manual_edits", rows: workspace.manualEdits, columns: ["recordTable", "recordId", "fieldName", "previousValue", "newValue", "reason"] },
    { title: "validation_issues", rows: workspace.validationIssues, columns: ["severity", "status", "ruleId", "message", "recordTable", "recordId"] },
  ] satisfies Array<{ title: string; rows: object[]; columns: string[] }>;

  return (
    <div className="space-y-3">
      {sections.map((section) => (
        <FinancialTable key={section.title} {...section} mocked={mocked} />
      ))}
    </div>
  );
}

function FinancialTable({
  title,
  rows,
  columns,
  mocked,
}: {
  title: string;
  rows: object[];
  columns: string[];
  mocked: boolean;
}) {
  return (
    <div className="rounded-md border border-[rgb(var(--border))]">
      <div className="flex h-9 items-center justify-between border-b border-[rgb(var(--border))] px-3">
        <div className="text-xs font-semibold uppercase tracking-[0.04em]">{labelize(title)}</div>
        <div className="text-xs text-[rgb(var(--muted-foreground))]">{rows.length} rows{mocked ? " mocked" : ""}</div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] text-left text-xs">
          <thead>
            <tr className="bg-[rgb(var(--surface-2))]">
              {columns.map((column) => (
                <th key={column} className="h-8 px-2 font-medium text-[rgb(var(--muted-foreground))]">{labelize(column)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={columns.length}>
                  <EmptyRow label={`No ${labelize(title).toLowerCase()} records`} />
                </td>
              </tr>
            ) : (
              rows.map((row, index) => (
                <tr key={index} className="border-t border-[rgb(var(--border))]">
                  {columns.map((column) => (
                    <td key={column} className="h-8 px-2">{renderCell((row as Record<string, unknown>)[column])}</td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function EmptyRow({ label }: { label: string }) {
  return (
    <div className="px-3 py-6 text-center text-xs text-[rgb(var(--muted-foreground))]">
      {label}
    </div>
  );
}

function relative(value: Date | string | null | undefined) {
  if (!value) return "n/a";
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "n/a";
  return `${formatDistanceToNowStrict(date)} ago`;
}

function renderCell(value: unknown) {
  if (value == null || value === "") return <span className="text-[rgb(var(--muted-foreground))]">None</span>;
  if (value instanceof Date) return relative(value);
  if (typeof value === "object") return <code className="text-[10px]">{JSON.stringify(value)}</code>;
  if (typeof value === "string" && value.length > 20 && /^[0-9a-f-]{16,}$/i.test(value)) {
    return <span className="rounded bg-[rgb(var(--muted))] px-1.5 py-0.5 font-mono text-[10px]">{truncateId(value)}</span>;
  }
  return String(value);
}
