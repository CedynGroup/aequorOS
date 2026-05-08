import type { ReactNode } from 'react';
import { Inbox } from 'lucide-react';

export default function EmptyState({
  title,
  description,
  action,
  Icon = Inbox,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  Icon?: typeof Inbox;
}) {
  return (
    <div className="card p-10 flex flex-col items-center text-center gap-3">
      <div className="w-12 h-12 rounded-full bg-surface text-slate inline-flex items-center justify-center">
        <Icon size={20} aria-hidden />
      </div>
      <p className="text-h3 text-navy">{title}</p>
      {description && (
        <p className="text-body text-slate max-w-md leading-relaxed">
          {description}
        </p>
      )}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}
