'use client';

import type { ReactNode } from 'react';
import { ApiError } from '@/lib/api';
import { ErrorPanel } from './Feedback';
import { SkeletonCard, SkeletonChart, SkeletonTable } from './Skeleton';

/**
 * Full-page loading placeholder — KPI row, a chart, and a table. No outer
 * padding: the console's <main> owns page padding.
 */
export function PageSkeleton() {
  return (
    <div className="space-y-6" aria-busy="true" aria-label="Loading">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4">
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

/**
 * Standard loading / error boundary for `useApi`-backed pages: skeleton while
 * loading, an ErrorPanel (with Retry) on failure, children once data is ready.
 */
export function QueryBoundary({
  loading,
  error,
  onRetry,
  context,
  skeleton,
  children,
}: {
  loading: boolean;
  error: ApiError | null | undefined;
  onRetry?: () => void;
  context?: string;
  skeleton?: ReactNode;
  children: ReactNode;
}) {
  if (loading) return <>{skeleton ?? <PageSkeleton />}</>;
  if (error) return <ErrorPanel error={error} onRetry={onRetry} context={context} />;
  return <>{children}</>;
}
