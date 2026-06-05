import type {
  CaseDecision,
  CaseStatus as CaseStatusType,
  RiskLevel as RiskLevelType,
} from "@aequoros/risk-service-api";
import { formatDistanceToNowStrict } from "date-fns";

import { Badge } from "../../components/ui";
import { labelize } from "../../lib/utils";

export function StatusBadge({ value }: { value: CaseStatusType }) {
  const tone =
    value === "completed"
      ? "success"
      : value === "archived"
        ? "neutral"
        : value === "in_review"
          ? "info"
          : "warning";
  return <Badge tone={tone}>{labelize(value)}</Badge>;
}

export function RiskBadge({ value }: { value: RiskLevelType | null }) {
  const tone =
    value === "critical" || value === "high"
      ? "danger"
      : value === "medium"
        ? "warning"
        : value === "low"
          ? "success"
          : "neutral";
  return <Badge tone={tone}>{labelize(value)}</Badge>;
}

export function DecisionBadge({ value }: { value: CaseDecision | null }) {
  const tone =
    value === "approved"
      ? "success"
      : value === "rejected"
        ? "danger"
        : value === "escalated"
          ? "warning"
          : "neutral";
  return <Badge tone={tone}>{labelize(value)}</Badge>;
}

export function relative(value: Date | string | null | undefined) {
  if (!value) return "n/a";
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "n/a";
  return `${formatDistanceToNowStrict(date)} ago`;
}
