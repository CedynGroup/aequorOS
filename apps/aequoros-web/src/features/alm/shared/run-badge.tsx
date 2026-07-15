import { Badge } from "../../../components/ui";
import { labelize } from "../../../lib/utils";
import { formatDateTime, runStatusTone } from "./format";

export type RunBadgeRun = {
  status: string;
  engineVersion: string;
  inputHash: string;
  createdAt: Date;
};

export function RunBadge({ run }: { run: RunBadgeRun }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-2 text-[11px] text-[rgb(var(--muted-foreground))]">
      <Badge tone={runStatusTone(run.status)}>{labelize(run.status)}</Badge>
      <span>{run.engineVersion}</span>
      <span className="font-mono" title={run.inputHash}>
        {run.inputHash.slice(0, 10)}
      </span>
      <span>{formatDateTime(run.createdAt)}</span>
    </span>
  );
}
