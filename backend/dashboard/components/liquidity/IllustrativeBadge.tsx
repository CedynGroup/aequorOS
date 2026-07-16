import { PenLine } from 'lucide-react';

/**
 * Amber marker for framework / playbook content that is NOT computed from
 * bank data — escalation playbooks, what-if planners, policy templates.
 * Every section that mixes narrative guidance into a data page must carry
 * this badge so reviewers can tell engine output from illustration.
 */
export default function IllustrativeBadge({
  label = 'Illustrative',
  title = 'Framework content for illustration — not computed from bank data.',
  className = '',
}: {
  label?: string;
  title?: string;
  className?: string;
}) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-caption font-medium uppercase tracking-wider border bg-warning-light text-warning border-warning/30 ${className}`}
    >
      <PenLine size={11} aria-hidden />
      {label}
    </span>
  );
}
