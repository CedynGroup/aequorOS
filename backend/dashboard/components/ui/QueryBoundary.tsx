'use client';

/**
 * Standard loading / error boundary for API-backed pages. Renders skeletons
 * while loading, an error panel with retry on failure, and children once the
 * data is available.
 */

import type { ReactNode } from 'react';
import { AlertCircle, RotateCw } from 'lucide-react';
import { isApiError } from '@/lib/api/client';
import { SkeletonCard, SkeletonChart, SkeletonTable } from './Skeleton';

export function ErrorPanel({
  error,
  onRetry,
  title = 'Could not load data',
}: {
  error: unknown;
  onRetry?: () => void;
  title?: string;
}) {
  const message = isApiError(error)
    ? error.message
    : error instanceof Error
    ? error.message
    : 'An unexpected error occurred.';
  return (
    <div className="card border-l-4 border-l-critical bg-critical-light/40 p-5 flex items-start gap-3">
      <AlertCircle size={18} className="text-critical shrink-0 mt-0.5" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="text-body font-medium text-navy">{title}</p>
        <p className="mt-1 text-body text-navy/80 leading-relaxed">{message}</p>
      </div>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="shrink-0 inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-slate border border-border rounded-md hover:bg-surface"
        >
          <RotateCw size={13} aria-hidden />
          Retry
        </button>
      )}
    </div>
  );
}

export function PageSkeleton() {
  return (
    <div className="px-8 py-6 space-y-6" aria-busy="true" aria-label="Loading">
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <SkeletonCard />
        <SkeletonCard />
        <SkeletonCard />
        <SkeletonCard />
      </div>
      <SkeletonChart height={280} />
      <div className="card">
        <SkeletonTable rows={6} />
      </div>
    </div>
  );
}

export default function QueryBoundary({
  isLoading,
  error,
  onRetry,
  skeleton,
  children,
}: {
  isLoading: boolean;
  error: unknown;
  onRetry?: () => void;
  skeleton?: ReactNode;
  children: ReactNode;
}) {
  if (isLoading) return <>{skeleton ?? <PageSkeleton />}</>;
  if (error) {
    return (
      <div className="px-8 py-6">
        <ErrorPanel error={error} onRetry={onRetry} />
      </div>
    );
  }
  return <>{children}</>;
}
