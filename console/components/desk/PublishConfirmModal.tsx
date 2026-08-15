'use client';

import { Upload } from 'lucide-react';
import { Button, CopyButton, FieldRow, Modal, StatusPill } from '@/components/ui';
import { DASH, fmtDate } from '@/lib/format';
import type { DeskDetermination } from '@/lib/api';

/**
 * Deliberate publish confirmation — replaces the old "arm" checkbox. Shows the
 * package + input digests being fanned out and exactly which scopes reach the
 * banks (rates always; curve scopes only when curves QA passed).
 */
export function PublishConfirmModal({
  open,
  onClose,
  onConfirm,
  loading,
  determination,
  title = 'Publish to every bank',
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  loading: boolean;
  determination: DeskDetermination;
  title?: string;
}) {
  const pkgDigest = determination.derived_values.package_digest ?? null;
  const curvesPass = determination.derived_values.curves_qa_passed;

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="md"
      title={title}
      description="Deliberate, logged fan-out through the desk-as-vendor adapter (vendor aequor_desk). One ingestion batch per tenant; supersedes by natural key."
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={loading}>
            Cancel
          </Button>
          <Button variant="primary" icon={<Upload size={14} />} onClick={onConfirm} loading={loading}>
            Confirm publish
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="divide-y divide-border-light rounded-md border border-border-light px-4">
          <FieldRow label="COB">{fmtDate(determination.cob_date)}</FieldRow>
          <FieldRow label="Methodology">
            {determination.methodology_code} v{determination.methodology_version}
          </FieldRow>
          <FieldRow label="Package digest">
            <span className="inline-flex items-center gap-0.5">
              <span className="break-all font-mono text-caption">
                {pkgDigest ? `${pkgDigest.slice(0, 20)}…` : DASH}
              </span>
              {pkgDigest && <CopyButton value={pkgDigest} label="Copy package digest" />}
            </span>
          </FieldRow>
          <FieldRow label="Input digest">
            <span className="inline-flex items-center gap-0.5">
              <span className="break-all font-mono text-caption">
                {determination.input_digest.slice(0, 20)}…
              </span>
              <CopyButton value={determination.input_digest} label="Copy input digest" />
            </span>
          </FieldRow>
        </div>

        <div>
          <p className="mb-2 text-caption font-medium uppercase tracking-wide text-slate">
            What fans out
          </p>
          <ul className="space-y-2">
            <li className="flex items-center justify-between gap-3">
              <span className="text-body text-navy">Rates package</span>
              <StatusPill tone="action">published</StatusPill>
            </li>
            <li className="flex items-center justify-between gap-3">
              <span className="text-body text-navy">Curve scopes</span>
              {curvesPass === true ? (
                <StatusPill tone="action">included</StatusPill>
              ) : (
                <StatusPill tone="slate">omitted</StatusPill>
              )}
            </li>
          </ul>
          {curvesPass === false && (
            <p className="mt-2 text-caption text-warning">
              Curve scopes are omitted because curves QA did not pass — rates still publish.
            </p>
          )}
        </div>
      </div>
    </Modal>
  );
}
