'use client';

import type { ReactNode } from 'react';
import { Lock } from 'lucide-react';
import { ApiError } from '@/lib/api';
import { ErrorPanel } from '@/components/ui';

/**
 * The Admin surfaces are `operator_admin`-gated on the backend. The console
 * cannot read its own operator's role client-side, so it does not pre-hide the
 * pages — instead it calls the endpoint and, on a 403, degrades the WHOLE
 * surface to this explainer. Any other error keeps the normal retry panel.
 */
export function AccessDeniedPanel({
  surface,
  error,
}: {
  surface: string;
  error?: ApiError;
}) {
  return (
    <div className="card border-warning/40 p-6" role="alert">
      <div className="flex items-start gap-3">
        <span className="mt-0.5 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-warning-light text-warning">
          <Lock size={18} aria-hidden />
        </span>
        <div className="min-w-0">
          <p className="text-body font-medium text-navy">Operator-admin access required</p>
          <p className="mt-1 max-w-xl text-caption text-slate">
            {surface} is restricted to operators with the <code>operator_admin</code> role (or
            above). Your account is authenticated but not authorized for this surface. Ask a super
            admin to elevate your role, then reload.
          </p>
          {error && (
            <p className="mt-3 break-words text-micro text-slate">
              <span className="font-mono text-caption text-warning">{error.code}</span>
              {' · '}
              {error.message}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * Loading / 403 / error / ready boundary for the admin pages. Mirrors
 * QueryBoundary but adds the operator-admin 403 degrade and lets the caller
 * supply a table-shaped skeleton.
 */
export function AdminBoundary({
  loading,
  error,
  onRetry,
  surface,
  skeleton,
  children,
}: {
  loading: boolean;
  error: ApiError | null | undefined;
  onRetry?: () => void;
  surface: string;
  skeleton: ReactNode;
  children: ReactNode;
}) {
  if (loading) return <>{skeleton}</>;
  if (error && error.status === 403) return <AccessDeniedPanel surface={surface} error={error} />;
  if (error) return <ErrorPanel error={error} onRetry={onRetry} context={`Loading ${surface}`} />;
  return <>{children}</>;
}
