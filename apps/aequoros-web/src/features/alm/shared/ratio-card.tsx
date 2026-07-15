import type { ReactNode } from "react";

import { Badge, Button } from "../../../components/ui";
import { labelize } from "../../../lib/utils";
import { formatPct, formatPp, statusTone } from "./format";

export function RatioCard({
  label,
  value,
  status,
  thresholds = [],
  meta,
  onOpen,
  openLabel,
}: {
  label: string;
  value: string;
  status: string;
  thresholds?: Array<{ label: string; valuePct: string }>;
  meta?: ReactNode;
  onOpen?: () => void;
  openLabel?: string;
}) {
  const primaryThreshold = thresholds[0];
  const variancePp = primaryThreshold
    ? Number(value) - Number(primaryThreshold.valuePct)
    : null;

  return (
    <div className="min-w-0 rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--surface))] p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs font-medium uppercase tracking-[0.04em] text-[rgb(var(--muted-foreground))]">
          {label}
        </div>
        <Badge tone={statusTone(status)}>{labelize(status)}</Badge>
      </div>
      <div
        className="mt-1 truncate font-mono text-2xl font-semibold tabular-nums"
        title={`${label} ${formatPct(value, 2)}`}
      >
        {formatPct(value)}
      </div>
      <div className="mt-1 space-y-0.5 text-[11px] text-[rgb(var(--muted-foreground))]">
        {thresholds.map((threshold) => (
          <div key={threshold.label} className="font-mono tabular-nums">
            {threshold.label} {formatPct(threshold.valuePct)}
          </div>
        ))}
        {variancePp !== null && Number.isFinite(variancePp) ? (
          <div className="font-mono tabular-nums">
            {formatPp(variancePp)} vs {primaryThreshold?.label.toLowerCase()}
          </div>
        ) : null}
        {meta ? <div>{meta}</div> : null}
      </div>
      {onOpen ? (
        <Button
          size="sm"
          variant="ghost"
          className="mt-2 h-6 px-1 text-[11px]"
          onClick={onOpen}
        >
          {openLabel ?? "Open"}
        </Button>
      ) : null}
    </div>
  );
}
