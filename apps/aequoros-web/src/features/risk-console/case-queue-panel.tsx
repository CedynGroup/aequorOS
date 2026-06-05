import type { CaseQueueItemRead } from "@aequoros/risk-service-api";
import {
  CaseSort,
  type CaseSort as CaseSortType,
  CaseStatus,
  type CaseStatus as CaseStatusType,
  RiskLevel,
  type RiskLevel as RiskLevelType,
} from "@aequoros/risk-service-api";
import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { ArrowLeft, ArrowRight, Search } from "lucide-react";
import { useMemo } from "react";

import {
  Button,
  Checkbox,
  Input,
  Panel,
  PanelHeader,
  Select,
  SelectItem,
  Skeleton,
  Switch,
} from "../../components/ui";
import type { TenantHeaders } from "../../lib/api";
import { cn, labelize, truncateId } from "../../lib/utils";
import { ErrorPanel } from "../../shared/route-ui";
import { BulkActionDialog } from "./bulk-actions";
import { DecisionBadge, RiskBadge, StatusBadge, relative } from "./format";
import type { CaseListData, CaseQueueFilters, UpdateSearch } from "./types";

export function CaseQueuePanel({
  query,
  mockList,
  taxonomy,
  filters,
  page,
  selected,
  setSelected,
  chooseCase,
  activeCaseId,
  selectedIds,
  tenant,
  updateSearch,
}: {
  query: {
    data?: CaseListData;
    error: unknown;
    isError: boolean;
    isLoading: boolean;
  };
  mockList?: CaseListData;
  taxonomy?: { statuses: string[]; riskLevels: string[]; sortOptions: string[] };
  filters: CaseQueueFilters;
  page: number;
  selected: Record<string, boolean>;
  setSelected: (selected: Record<string, boolean>) => void;
  chooseCase: (caseId: string) => void;
  activeCaseId?: string;
  selectedIds: string[];
  tenant: TenantHeaders;
  updateSearch: UpdateSearch;
}) {
  const rows = useMemo(
    () => mockList?.items ?? query.data?.items ?? [],
    [mockList?.items, query.data?.items],
  );
  const allSelected = rows.length > 0 && rows.every((item) => selected[item.id]);
  const columns = useMemo<ColumnDef<CaseQueueItemRead>[]>(
    () => [
      {
        id: "select",
        header: () => (
          <Checkbox
            aria-label="Select all cases"
            checked={allSelected}
            onCheckedChange={(checked) => {
              const next = { ...selected };
              rows.forEach((item) => {
                next[item.id] = checked;
              });
              setSelected(next);
            }}
          />
        ),
        cell: ({ row }) => (
          <Checkbox
            aria-label={`Select ${row.original.title}`}
            checked={Boolean(selected[row.original.id])}
            onCheckedChange={(checked) =>
              setSelected({ ...selected, [row.original.id]: checked })
            }
          />
        ),
      },
      {
        accessorKey: "title",
        header: "Title",
        cell: ({ row }) => (
          <button
            className="max-w-56 truncate text-left font-medium hover:underline"
            onClick={() => chooseCase(row.original.id)}
          >
            {row.original.title}
          </button>
        ),
      },
      {
        accessorKey: "status",
        header: "Status",
        cell: ({ row }) => <StatusBadge value={row.original.status} />,
      },
      {
        accessorKey: "assigneeDisplayName",
        header: "Assignee",
        cell: ({ row }) =>
          row.original.assigneeDisplayName ?? truncateId(row.original.assignedToUserId),
      },
      {
        accessorKey: "riskScore",
        header: "Score",
        cell: ({ row }) => row.original.riskScore ?? "n/a",
      },
      {
        accessorKey: "riskLevel",
        header: "Risk",
        cell: ({ row }) => <RiskBadge value={row.original.riskLevel} />,
      },
      {
        accessorKey: "decision",
        header: "Decision",
        cell: ({ row }) => <DecisionBadge value={row.original.decision} />,
      },
      {
        accessorKey: "openFindingsCount",
        header: "Open",
      },
      {
        accessorKey: "updatedAt",
        header: "Updated",
        cell: ({ row }) => relative(row.original.updatedAt),
      },
    ],
    [allSelected, chooseCase, rows, selected, setSelected],
  );
  const table = useReactTable({
    data: rows,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  return (
    <Panel className="min-h-[640px] overflow-hidden">
      <PanelHeader
        title="Case Queue"
        meta={`${mockList?.total ?? query.data?.total ?? 0} cases${mockList ? " mocked" : ""}`}
        actions={<BulkActionDialog selectedIds={selectedIds} tenant={tenant} />}
      />
      <div className="flex flex-wrap items-center gap-2 border-b border-[rgb(var(--border))] p-3">
        <div className="relative min-w-52 flex-1">
          <Search className="pointer-events-none absolute left-2 top-1/2 size-4 -translate-y-1/2 text-[rgb(var(--muted-foreground))]" />
          <Input
            className="pl-8"
            value={filters.q}
            placeholder="Search cases"
            onChange={(event) => updateSearch({ q: event.target.value, page: 1 })}
          />
        </div>
        <Select value={filters.status} onValueChange={(value) => updateSearch({ status: value as CaseStatusType | "all", page: 1 })} placeholder="Status">
          <SelectItem value="all">All statuses</SelectItem>
          {(taxonomy?.statuses ?? Object.values(CaseStatus)).map((status) => (
            <SelectItem key={status} value={status}>
              {labelize(status)}
            </SelectItem>
          ))}
        </Select>
        <Select value={filters.risk} onValueChange={(value) => updateSearch({ risk: value as RiskLevelType | "all", page: 1 })} placeholder="Risk">
          <SelectItem value="all">All risks</SelectItem>
          {(taxonomy?.riskLevels ?? Object.values(RiskLevel)).map((risk) => (
            <SelectItem key={risk} value={risk}>
              {labelize(risk)}
            </SelectItem>
          ))}
        </Select>
        <Select value={filters.sort} onValueChange={(value) => updateSearch({ sort: value as CaseSortType })} placeholder="Sort">
          {(taxonomy?.sortOptions ?? Object.values(CaseSort)).map((sort) => (
            <SelectItem key={sort} value={sort}>
              {labelize(sort)}
            </SelectItem>
          ))}
        </Select>
        <div className="flex items-center gap-2 text-xs text-[rgb(var(--muted-foreground))]">
          <Switch checked={filters.archived} onCheckedChange={(archived) => updateSearch({ archived, page: 1 })} />
          <span>Archived</span>
        </div>
      </div>
      {query.isLoading && !mockList ? (
        <div className="space-y-2 p-3">
          {Array.from({ length: 8 }).map((_, index) => (
            <Skeleton key={index} className="h-9" />
          ))}
        </div>
      ) : query.isError && !mockList ? (
        <div className="p-3">
          <ErrorPanel error={query.error} />
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[920px] border-collapse text-left text-sm">
            <thead>
              {table.getHeaderGroups().map((group) => (
                <tr key={group.id} className="border-b border-[rgb(var(--border))] bg-[rgb(var(--surface-2))]">
                  {group.headers.map((header) => (
                    <th key={header.id} className="h-9 px-2 text-xs font-semibold uppercase tracking-[0.04em] text-[rgb(var(--muted-foreground))]">
                      {flexRender(header.column.columnDef.header, header.getContext())}
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody>
              {table.getRowModel().rows.length === 0 ? (
                <tr>
                  <td colSpan={columns.length} className="h-24 px-3 text-center text-sm text-[rgb(var(--muted-foreground))]">
                    No cases match the current filters.
                  </td>
                </tr>
              ) : (
                table.getRowModel().rows.map((row) => (
                  <tr
                    key={row.id}
                    className={cn(
                      "border-b border-[rgb(var(--border))] hover:bg-[rgb(var(--muted))]",
                      row.original.id === activeCaseId && "bg-emerald-50/60",
                    )}
                  >
                    {row.getVisibleCells().map((cell) => (
                      <td key={cell.id} className="h-10 px-2 align-middle text-xs">
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}
      <div className="flex items-center justify-between border-t border-[rgb(var(--border))] p-3 text-xs text-[rgb(var(--muted-foreground))]">
        <span>
          Page {mockList?.pages ?? query.data?.pages ? page : 0} of {mockList?.pages ?? query.data?.pages ?? 0}
        </span>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => updateSearch({ page: page - 1 })}>
            <ArrowLeft className="size-3.5" />
            Prev
          </Button>
          <Button variant="outline" size="sm" disabled={!(mockList?.hasMore ?? query.data?.hasMore)} onClick={() => updateSearch({ page: page + 1 })}>
            Next
            <ArrowRight className="size-3.5" />
          </Button>
        </div>
      </div>
    </Panel>
  );
}
