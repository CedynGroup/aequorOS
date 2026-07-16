import { Info } from 'lucide-react';

/**
 * Small amber marker for visuals derived client-side from stored fields
 * rather than read verbatim from an engine output (e.g. a history strip
 * evaluated against the *current* limit). Never used to dress up fake data —
 * anything without real fields behind it is omitted instead.
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
      <Info size={10} aria-hidden />
      {label}
    </span>
  );
}
