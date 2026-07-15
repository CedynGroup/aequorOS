import { formatMoneyCompact, formatMoneyFull } from "./format";

const segmentShades = [
  "rgb(var(--primary))",
  "rgba(var(--primary), 0.75)",
  "rgba(var(--primary), 0.55)",
  "rgba(var(--primary), 0.38)",
  "rgba(var(--primary), 0.24)",
  "rgba(var(--primary), 0.14)",
] as const;

export type CompositionSegment = {
  label: string;
  value: string;
};

export function CompositionBar({
  segments,
  currency,
  ariaLabel,
}: {
  segments: CompositionSegment[];
  currency: string;
  ariaLabel: string;
}) {
  const numeric = segments.map((segment) => ({
    ...segment,
    amount: Math.max(Number(segment.value), 0),
  }));
  const total = numeric.reduce((sum, segment) => sum + segment.amount, 0);

  return (
    <div aria-label={ariaLabel} className="min-w-0">
      <div className="flex h-3 w-full overflow-hidden rounded-sm border border-[rgb(var(--border))]">
        {numeric.map((segment, index) =>
          total > 0 && segment.amount > 0 ? (
            <div
              key={segment.label}
              title={`${segment.label} · ${formatMoneyFull(segment.value, currency)}`}
              style={{
                width: `${(segment.amount / total) * 100}%`,
                background: segmentShades[index % segmentShades.length],
              }}
            />
          ) : null,
        )}
      </div>
      <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-[rgb(var(--muted-foreground))]">
        {numeric.map((segment, index) => (
          <span key={segment.label} className="inline-flex items-center gap-1.5">
            <span
              aria-hidden
              className="inline-block size-2 rounded-[2px]"
              style={{
                background: segmentShades[index % segmentShades.length],
              }}
            />
            {segment.label}
            <span
              className="font-mono tabular-nums"
              title={formatMoneyFull(segment.value, currency)}
            >
              {formatMoneyCompact(segment.value, currency)}
            </span>
          </span>
        ))}
      </div>
    </div>
  );
}
