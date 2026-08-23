import { TriangleAlert } from 'lucide-react';

/**
 * Amber marker for figures shown as stand-ins rather than measured
 * outcomes — e.g. the ex-post column before realized accounting margins
 * are ingested, or a grouping the FTP engine does not itself publish.
 *
 * `label` / `title` let a caller name exactly what is illustrative and why;
 * the defaults keep the original single-word marker.
 */
export default function IllustrativeBadge({
  label = 'Illustrative',
  title,
  className = '',
}: {
  label?: string;
  /** Tooltip explaining exactly what is derived and from which fields. */
  title?: string;
  className?: string;
}) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded border border-warning/30 bg-warning-light text-warning text-micro font-medium uppercase tracking-wider ${className}`}
    >
      <TriangleAlert size={10} aria-hidden />
      {label}
    </span>
  );
}
