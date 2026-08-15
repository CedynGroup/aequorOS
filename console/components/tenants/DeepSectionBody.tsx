'use client';

import type { ReactNode } from 'react';
import { Lock } from 'lucide-react';
import type { ApiError } from '@/lib/api';
import {
  EmptyState,
  ErrorPanel,
  SectionCard,
  SkeletonRows,
} from '@/components/ui';
import { InspectTenantButton } from './InspectTenantButton';
import { isInspectionRequired } from './util';

/**
 * The exact copy the task pins for the gated placeholder — kept in one place so
 * the full-region card and the mid-view "session ended" fallback stay in sync.
 */
export const GATED_COPY =
  'Deep tenant data is available during an audited inspection session. Start one to view ingestion, metrics, findings, and configuration — every view is logged.';

/**
 * The full-region gate shown when the operator holds NO active inspection
 * session for this org. A single SectionCard + EmptyState with the "Inspect
 * this tenant" CTA (reuses InspectTenantButton, which opens a session and sets
 * the shell banner). This replaces the entire deep-data region.
 */
export function GatedTenantData({ orgId, orgLabel }: { orgId: string; orgLabel?: string }) {
  return (
    <SectionCard title="Deep tenant data" subtitle="Audited inspection required">
      <EmptyState
        Icon={Lock}
        title="Start an inspection session to view this tenant's data"
        description={GATED_COPY}
        action={<InspectTenantButton orgId={orgId} orgLabel={orgLabel} />}
      />
    </SectionCard>
  );
}

/**
 * The in-card fallback for when a session expires MID-VIEW: a deep read 403s
 * with `inspection_required`. Rather than a raw error panel, show a compact
 * re-inspect prompt inside the section that failed.
 */
export function SessionEndedNote({ orgId, orgLabel }: { orgId: string; orgLabel?: string }) {
  return (
    <EmptyState
      Icon={Lock}
      title="Inspection session ended"
      description={GATED_COPY}
      action={<InspectTenantButton orgId={orgId} orgLabel={orgLabel} />}
    />
  );
}

/**
 * Shared loading / gated-403 / error / empty gate for a deep-section body. The
 * `inspection_required` 403 is caught BEFORE the generic error panel so an
 * expired session degrades to the re-inspect prompt, never a raw error.
 */
export function DeepSectionBody({
  loading,
  error,
  reload,
  context,
  orgId,
  orgLabel,
  showEmpty,
  empty,
  children,
}: {
  loading: boolean;
  error: ApiError | null;
  reload: () => void;
  context: string;
  orgId: string;
  orgLabel?: string;
  showEmpty: boolean;
  empty: ReactNode;
  children: ReactNode;
}) {
  if (error && isInspectionRequired(error))
    return <SessionEndedNote orgId={orgId} orgLabel={orgLabel} />;
  if (loading) return <SkeletonRows rows={4} />;
  if (error)
    return (
      <div className="p-4">
        <ErrorPanel error={error} onRetry={reload} context={context} />
      </div>
    );
  if (showEmpty) return <>{empty}</>;
  return <>{children}</>;
}
