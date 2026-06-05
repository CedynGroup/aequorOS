import type {
  CaseSort,
  CaseStatus,
  RiskLevel,
} from "@aequoros/risk-service-api";

import type { ConsoleTab, ReportMode } from "../lib/constants";

export type SearchState = {
  tab?: ConsoleTab;
  report?: ReportMode;
  q?: string;
  status?: CaseStatus | "all";
  risk?: RiskLevel | "all";
  archived?: boolean;
  sort?: CaseSort;
  page?: number;
};

export function parseSearchState(input: Record<string, unknown>): SearchState {
  return {
    tab: typeof input.tab === "string" ? (input.tab as ConsoleTab) : undefined,
    report: input.report === "html" ? "html" : "json",
    q: typeof input.q === "string" ? input.q : undefined,
    status: typeof input.status === "string" ? (input.status as CaseStatus | "all") : undefined,
    risk: typeof input.risk === "string" ? (input.risk as RiskLevel | "all") : undefined,
    archived: input.archived === true || input.archived === "true",
    sort: typeof input.sort === "string" ? (input.sort as CaseSort) : undefined,
    page: Number.isFinite(Number(input.page)) ? Number(input.page) : undefined,
  };
}
