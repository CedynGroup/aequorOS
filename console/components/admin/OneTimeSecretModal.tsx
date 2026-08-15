'use client';

import { KeyRound, ShieldAlert } from 'lucide-react';
import { Button, CopyButton, Modal } from '@/components/ui';

export interface OneTimeSecret {
  email: string;
  password: string;
  /** Heading — "Operator created" vs "Password reset". */
  title: string;
}

/**
 * Reveal-once one-time-password panel. The backend stores only a hash; the
 * plaintext is returned exactly once by createOperator / resetOperatorPassword
 * and can never be retrieved again. This modal makes that irreversibility
 * explicit and gives a single copy affordance — dismissing it is destructive.
 */
export function OneTimeSecretModal({
  secret,
  onClose,
}: {
  secret: OneTimeSecret | null;
  onClose: () => void;
}) {
  return (
    <Modal
      open={secret !== null}
      onClose={onClose}
      title={
        <span className="inline-flex items-center gap-2">
          <KeyRound size={16} className="text-action" aria-hidden />
          {secret?.title ?? 'One-time password'}
        </span>
      }
      description="Copy this now — it is shown once and cannot be retrieved again."
      size="md"
      dismissOnBackdrop={false}
      footer={
        <Button variant="primary" onClick={onClose}>
          I&rsquo;ve copied it
        </Button>
      }
    >
      {secret && (
        <div className="space-y-4">
          <div className="flex items-start gap-2.5 rounded-md border border-warning/30 bg-warning-light px-3 py-2.5 text-caption text-warning">
            <ShieldAlert size={15} className="mt-0.5 shrink-0" aria-hidden />
            <span>
              Only a hash is stored server-side. Hand it to{' '}
              <span className="font-medium">{secret.email}</span> over a trusted channel; they must
              change it on first sign-in.
            </span>
          </div>

          <div>
            <p className="mb-1 text-caption font-medium text-slate">Operator</p>
            <p className="font-mono text-body text-ink">{secret.email}</p>
          </div>

          <div>
            <p className="mb-1 text-caption font-medium text-slate">One-time password</p>
            <div className="flex items-center justify-between gap-2 rounded-md border border-border bg-surface px-3 py-2.5">
              <code className="break-all font-mono text-body text-navy">{secret.password}</code>
              <span className="shrink-0">
                <CopyButton value={secret.password} label="Copy one-time password" />
              </span>
            </div>
          </div>
        </div>
      )}
    </Modal>
  );
}
