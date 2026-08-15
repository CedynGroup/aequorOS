'use client';

import { AuditTimeline, SectionCard, type AuditEvent } from '@/components/ui';
import { shortId } from '@/lib/format';
import type { DeskDetermination } from '@/lib/api';

/**
 * The determination's maker-checker lifecycle rendered as an AuditTimeline,
 * derived from the record's own fields (prepared → submitted → reviewed →
 * published). Timestamps appear only where the wire carries one (created_at,
 * published_at); the intermediate transitions are ordered but untimed.
 */
export function DeterminationTimeline({ determination: d }: { determination: DeskDetermination }) {
  const events: AuditEvent[] = [];

  events.push({
    action: 'Draft prepared',
    actor: d.prepared_by,
    at: d.created_at,
    tone: 'accent',
    status: 'draft',
    detail: d.supersedes_id ? `Correction superseding ${shortId(d.supersedes_id, 10)}` : undefined,
  });

  if (d.status !== 'draft') {
    events.push({
      action: 'Submitted for supervisor review',
      actor: d.prepared_by,
      tone: 'neutral',
      status: 'pending_review',
    });
  }

  if (d.status === 'rejected' && d.reviewed_by) {
    events.push({
      action: 'Rejected',
      actor: d.reviewed_by,
      detail: d.review_note ?? undefined,
      tone: 'crit',
      status: 'rejected',
    });
  } else if ((d.status === 'approved' || d.status === 'published') && d.reviewed_by) {
    events.push({
      action: 'Approved (four-eyes)',
      actor: d.reviewed_by,
      tone: 'ok',
      status: 'approved',
    });
  }

  if (d.status === 'published' && d.published_at) {
    events.push({
      action: 'Published to all banks',
      at: d.published_at,
      tone: 'accent',
      status: 'published',
    });
  }

  return (
    <SectionCard title="Lifecycle" subtitle="Maker-checker state transitions for this determination.">
      <AuditTimeline events={events} />
    </SectionCard>
  );
}
