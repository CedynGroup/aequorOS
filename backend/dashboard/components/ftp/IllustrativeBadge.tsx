import { TriangleAlert } from 'lucide-react';

/**
 * Amber marker for figures shown as stand-ins rather than measured
 * outcomes — e.g. the ex-post column before realized accounting margins
 * are ingested.
 */
export default function IllustrativeBadge({
  className = '',
}: {
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded border border-warning/30 bg-warning-light text-warning text-[10px] font-medium uppercase tracking-wider ${className}`}
    >
      <TriangleAlert size={10} aria-hidden />
      Illustrative
    </span>
  );
}
