import {
  type CaseDecision,
  type CaseQueueItemRead,
  type CaseRead,
  CaseSort,
  type CaseSort as CaseSortType,
  CaseStatus,
  type CaseStatus as CaseStatusType,
  type FinancialDataWorkspaceRead,
  RiskLevel,
  type RiskLevel as RiskLevelType,
} from "@aequoros/risk-service-api";

import { DEFAULT_USER_ID } from "../../lib/constants";

const pageSize = 12;

export function emptyWorkspace(
  organizationId: string,
  caseId: string,
): FinancialDataWorkspaceRead {
  return {
    organizationId,
    caseId,
    institutions: [],
    accounts: [],
    reportingPeriods: [],
    balances: [],
    cashFlows: [],
    covenants: [],
    obligations: [],
    sourceRows: [],
    recordSourceLinks: [],
    manualEdits: [],
    validationIssues: [],
    validationSummary: { error: 0, info: 0, total: 0, warning: 0 },
  };
}

export function mockCaseList(
  organizationId: string,
  filters: {
    q: string;
    status: CaseStatusType | "all";
    risk: RiskLevelType | "all";
    archived: boolean;
    sort: CaseSortType;
  },
  page: number,
): {
  items: CaseQueueItemRead[];
  total: number;
  pages: number;
  hasMore: boolean;
} {
  let items = mockCases(organizationId);
  if (!filters.archived) {
    items = items.filter((item) => item.status !== CaseStatus.Archived);
  }
  if (filters.status !== "all") {
    items = items.filter((item) => item.status === filters.status);
  }
  if (filters.risk !== "all") {
    items = items.filter((item) => item.riskLevel === filters.risk);
  }
  if (filters.q) {
    const q = filters.q.toLowerCase();
    items = items.filter(
      (item) =>
        item.title.toLowerCase().includes(q) ||
        (item.subjectName ?? "").toLowerCase().includes(q),
    );
  }
  items = [...items].sort((a, b) => {
    if (filters.sort === CaseSort.RiskScoreDesc)
      return (b.riskScore ?? 0) - (a.riskScore ?? 0);
    if (filters.sort === CaseSort.RiskScoreAsc)
      return (a.riskScore ?? 0) - (b.riskScore ?? 0);
    if (filters.sort === CaseSort.TitleAsc)
      return a.title.localeCompare(b.title);
    if (filters.sort === CaseSort.CreatedAtAsc)
      return a.createdAt.getTime() - b.createdAt.getTime();
    if (filters.sort === CaseSort.CreatedAtDesc)
      return b.createdAt.getTime() - a.createdAt.getTime();
    if (filters.sort === CaseSort.UpdatedAtAsc)
      return a.updatedAt.getTime() - b.updatedAt.getTime();
    return b.updatedAt.getTime() - a.updatedAt.getTime();
  });
  const total = items.length;
  const start = (page - 1) * pageSize;
  return {
    items: items.slice(start, start + pageSize),
    total,
    pages: Math.max(Math.ceil(total / pageSize), 1),
    hasMore: start + pageSize < total,
  };
}

export function mockCase(organizationId: string, caseId: string): CaseRead {
  const queueItem =
    mockCases(organizationId).find((item) => item.id === caseId) ??
    mockCases(organizationId)[0];
  return {
    ...queueItem,
    archivedAt: queueItem.status === CaseStatus.Archived ? new Date() : null,
    assignedAt: new Date(queueItem.updatedAt.getTime() - 3_600_000),
    createdBy: DEFAULT_USER_ID,
    decidedAt: queueItem.decision
      ? new Date(queueItem.updatedAt.getTime() - 600_000)
      : null,
    description:
      "Frontend-only demo case seeded to exercise queue, detail, tabs, and empty financial workspace states while the backend API is unavailable.",
    metadata: {
      mocked: true,
      source: "aequoros-web demo seed",
    },
    scoredAt: new Date(queueItem.updatedAt.getTime() - 1_800_000),
    scoringVersion: "demo-v1",
  };
}

function mockCases(organizationId: string): CaseQueueItemRead[] {
  const now = Date.now();
  return [
    mockQueueItem(
      organizationId,
      "90000000-0000-4000-8000-000000000001",
      "Covenant review - Northstar Foods",
      CaseStatus.InReview,
      RiskLevel.High,
      "needs_more_info",
      82,
      4,
      now - 3_600_000,
    ),
    mockQueueItem(
      organizationId,
      "90000000-0000-4000-8000-000000000002",
      "Quarterly liquidity packet - Mariner Trust",
      CaseStatus.Active,
      RiskLevel.Medium,
      null,
      57,
      2,
      now - 8_600_000,
    ),
    mockQueueItem(
      organizationId,
      "90000000-0000-4000-8000-000000000003",
      "Basel capital review - Harbor Bank",
      CaseStatus.Completed,
      RiskLevel.Low,
      "approved",
      22,
      0,
      now - 26_000_000,
    ),
    mockQueueItem(
      organizationId,
      "90000000-0000-4000-8000-000000000004",
      "Exception review - Cedar Lending",
      CaseStatus.Draft,
      RiskLevel.Critical,
      "escalated",
      94,
      7,
      now - 42_000_000,
    ),
  ];
}

function mockQueueItem(
  organizationId: string,
  id: string,
  title: string,
  status: CaseStatusType,
  riskLevel: RiskLevelType,
  decision: CaseDecision | null,
  riskScore: number,
  openFindingsCount: number,
  updatedTime: number,
): CaseQueueItemRead {
  return {
    id,
    organizationId,
    title,
    caseType: "financial_statement_review",
    subjectType: "institution",
    subjectName: title.split(" - ")[1] ?? "Demo institution",
    status,
    assignedToUserId: DEFAULT_USER_ID,
    assigneeDisplayName: "Demo Analyst",
    assigneeEmail: "analyst@aequoros.local",
    riskScore,
    riskLevel,
    decision,
    findingsCount: openFindingsCount + 1,
    openFindingsCount,
    createdAt: new Date(updatedTime - 86_400_000),
    updatedAt: new Date(updatedTime),
  };
}
