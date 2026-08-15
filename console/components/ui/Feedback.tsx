import type { ReactNode } from 'react';
import { AlertTriangle, Inbox, RotateCw } from 'lucide-react';
import { ApiError } from '@/lib/api';

/**
 * Error panel bound to the console's `ApiError` envelope (code + message +
 * status). Signature preserved from the original ui.tsx: `{ error, onRetry,
 * context }`. A 401 additionally offers a re-sign-in link.
 */
export function ErrorPanel({
  error,
  onRetry,
  context,
}: {
  error: ApiError;
  onRetry?: () => void;
  context?: string;
}) {
  const unauthorized = error.status === 401;
  return (
    <div className="card border-critical/40 p-4" role="alert">
      <div className="flex items-start gap-3">
        <AlertTriangle size={18} className="mt-0.5 shrink-0 text-critical" />
        <div className="min-w-0 flex-1">
          <p className="text-body font-medium text-navy">
            {context ? `${context} failed` : 'Request failed'}
          </p>
          <p className="mt-1 break-words text-body text-slate">
            <span className="font-mono text-caption text-critical">{error.code}</span>
            {' · '}
            {error.message}
          </p>
          <div className="mt-3 flex items-center gap-2">
            {onRetry && (
              <button
                type="button"
                onClick={onRetry}
                className="btn-primary inline-flex items-center gap-1.5 px-3 py-1.5 text-caption font-medium"
              >
                <RotateCw size={13} /> Retry
              </button>
            )}
            {unauthorized && (
              <a
                href="/login"
                className="inline-flex items-center rounded border border-border px-3 py-1.5 text-caption font-medium text-ink hover:bg-surface"
              >
                Sign in again
              </a>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * Empty state. Back-compatible with the original `{ title, hint }` signature;
 * also accepts `description` / `action` / `Icon` for the richer dashboard
 * grammar (stub pages use `title` + `hint`).
 */
export function EmptyState({
  title,
  hint,
  description,
  action,
  Icon = Inbox,
}: {
  title: string;
  hint?: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  Icon?: typeof Inbox;
}) {
  const body = description ?? hint;
  return (
    <div className="flex flex-col items-center gap-2 p-10 text-center">
      <Icon size={22} className="text-slate-light" aria-hidden />
      <p className="text-body font-medium text-navy">{title}</p>
      {body && <p className="max-w-md text-caption text-slate">{body}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}
