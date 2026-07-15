import { CheckCircle2, XCircle } from "lucide-react";

import { Badge } from "../../../components/ui";
import { EmptyRow } from "../../../shared/route-ui";
import { labelize } from "../../../lib/utils";
import { severityTone } from "./format";

export type ValidationRow = {
  ruleCode: string;
  passed: boolean;
  severity: string;
  message: string;
};

export function ValidationList({
  validations,
  empty = "No validation rules were evaluated.",
}: {
  validations: ValidationRow[];
  empty?: string;
}) {
  if (!validations.length) return <EmptyRow label={empty} />;
  return (
    <ul className="divide-y divide-[rgb(var(--border))]">
      {validations.map((validation) => (
        <li
          key={validation.ruleCode}
          className="flex items-start gap-2 px-1 py-1.5 text-xs"
        >
          {validation.passed ? (
            <CheckCircle2
              aria-label={`${validation.ruleCode} passed`}
              className="mt-0.5 size-3.5 shrink-0 text-[rgb(var(--success))]"
            />
          ) : (
            <XCircle
              aria-label={`${validation.ruleCode} failed`}
              className="mt-0.5 size-3.5 shrink-0 text-[rgb(var(--danger))]"
            />
          )}
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="font-mono text-[11px] text-[rgb(var(--muted-foreground))]">
                {validation.ruleCode}
              </span>
              <Badge tone={severityTone(validation.severity)}>
                {labelize(validation.severity)}
              </Badge>
            </div>
            <div className="mt-0.5 text-[rgb(var(--foreground))]">
              {validation.message}
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}
