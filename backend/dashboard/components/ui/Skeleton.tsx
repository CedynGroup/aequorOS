/**
 * Skeleton loading shimmers — used for tables and dashboards while data loads.
 * Per design brief: subtle, no animation gimmicks.
 */

export function SkeletonLine({
  width = '100%',
  height = 12,
  className = '',
}: {
  width?: string | number;
  height?: number;
  className?: string;
}) {
  return (
    <div
      className={`bg-surface rounded animate-pulse ${className}`}
      style={{ width, height }}
      aria-hidden
    />
  );
}

export function SkeletonCard() {
  return (
    <div className="card p-5 space-y-4" aria-busy="true" aria-label="Loading">
      <SkeletonLine width="40%" height={10} />
      <SkeletonLine width="60%" height={28} />
      <SkeletonLine width="80%" height={10} />
    </div>
  );
}

export function SkeletonTable({ rows = 5 }: { rows?: number }) {
  return (
    <div aria-busy="true" aria-label="Loading table">
      <div className="bg-surface px-4 py-2.5 border-b border-border flex gap-6">
        {[1, 2, 3, 4].map((i) => (
          <SkeletonLine key={i} width={`${15 + (i % 2) * 10}%`} height={10} />
        ))}
      </div>
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="px-4 py-3 border-b border-border-light flex gap-6"
        >
          <SkeletonLine width="20%" height={12} />
          <SkeletonLine width="14%" height={12} />
          <SkeletonLine width="12%" height={12} />
          <SkeletonLine width="14%" height={12} />
        </div>
      ))}
    </div>
  );
}

export function SkeletonChart({ height = 240 }: { height?: number }) {
  return (
    <div
      className="card p-5 flex flex-col gap-4"
      style={{ minHeight: height }}
      aria-busy="true"
    >
      <SkeletonLine width="30%" height={14} />
      <div className="flex-1 bg-surface rounded animate-pulse" />
    </div>
  );
}
