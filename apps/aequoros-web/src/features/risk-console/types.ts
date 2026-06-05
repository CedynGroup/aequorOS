import type {
  CaseQueueItemRead,
  CaseSort as CaseSortType,
  CaseStatus as CaseStatusType,
  RiskLevel as RiskLevelType,
} from "@aequoros/risk-service-api";

import type { SearchState } from "../../routes/search";

export const pageSize = 12;

export type CaseQueueFilters = {
  q: string;
  status: CaseStatusType | "all";
  risk: RiskLevelType | "all";
  archived: boolean;
  sort: CaseSortType;
};

export type CaseListData = {
  items: CaseQueueItemRead[];
  total: number;
  pages: number;
  hasMore: boolean;
};

export type UpdateSearch = (next: Partial<SearchState>) => void;
