import type { ReactNode } from 'react';

export type StatusTone =
  | 'compliant'
  | 'approaching'
  | 'breach'
  | 'pending'
  | 'success'
  | 'amber'
  | 'critical'
  | 'slate'
  | 'action';

const toneStyles: Record<StatusTone, string> = {
  compliant: 'bg-success-light text-success border-success/20',
  success: 'bg-success-light text-success border-success/20',
  approaching: 'bg-warning-light text-warning border-warning/20',
  amber: 'bg-warning-light text-warning border-warning/20',
  breach: 'bg-critical-light text-critical border-critical/20',
  critical: 'bg-critical-light text-critical border-critical/20',
  pending: 'bg-surface text-slate border-border',
  slate: 'bg-surface text-slate border-border',
  action: 'bg-action-light text-action border-action/20',
};

const toneLabels: Partial<Record<StatusTone, string>> = {
  compliant: 'Compliant',
  approaching: 'Approaching',
  breach: 'Breach',
  pending: 'Pending',
};

export default function StatusPill({
  tone,
  children,
  className = '',
}: {
  tone: StatusTone;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-caption font-medium uppercase tracking-wider border ${toneStyles[tone]} ${className}`}
    >
      <span
        aria-hidden
        className="inline-block w-1.5 h-1.5 rounded-full bg-current"
      />
      {children ?? toneLabels[tone] ?? tone}
    </span>
  );
}
