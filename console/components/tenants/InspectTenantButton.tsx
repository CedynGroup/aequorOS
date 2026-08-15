'use client';

import { useState } from 'react';
import { Eye, ShieldAlert } from 'lucide-react';
import { startInspectorSession } from '@/lib/api';
import { useInspector } from '@/lib/inspector';
import { useMutation } from '@/lib/use-api';
import {
  Button,
  Field,
  Modal,
  Select,
  Textarea,
} from '@/components/ui';

/**
 * Launches a time-boxed, audited Tenant Inspector session against one org.
 * Self-contained: renders the trigger Button, the confirmation Modal (reason
 * required, consent vs break-glass mode, TTL), calls startInspectorSession, and
 * pushes the result into the shell's inspector context so the un-dismissable
 * ImpersonationBanner appears. This console surface only OPENS a session — the
 * /admin/inspector page owns management.
 */

// consent = a read-only look with the tenant's knowledge; break_glass = an
// emergency read-only session without consent (admin-gated). Both are the
// backend's InspectorMode values, sent as-is.
type InspectMode = 'consent' | 'break_glass';

const TTL_PRESETS = [15, 30, 60, 120];

export function InspectTenantButton({
  orgId,
  orgLabel,
}: {
  orgId: string;
  orgLabel?: string;
}) {
  const { setActive } = useInspector();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState('');
  const [mode, setMode] = useState<InspectMode>('consent');
  const [ttl, setTtl] = useState(30);

  const start = useMutation(startInspectorSession, {
    successMessage: 'Inspector session started',
    errorContext: 'Start inspector session',
    onSuccess: (session) => {
      setActive(session);
      setOpen(false);
      setReason('');
      setMode('consent');
      setTtl(30);
    },
  });

  const reasonValid = reason.trim().length >= 3;
  const breakGlass = mode === 'break_glass';

  return (
    <>
      <Button
        variant="secondary"
        size="sm"
        icon={<Eye size={14} aria-hidden />}
        onClick={() => setOpen(true)}
      >
        Inspect
      </Button>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Open a Tenant Inspector session"
        description={`Cross-tenant access to ${orgLabel ?? orgId}. Every session is time-boxed and written to the operator audit log.`}
        size="md"
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)} disabled={start.loading}>
              Cancel
            </Button>
            <Button
              variant={breakGlass ? 'danger' : 'primary'}
              loading={start.loading}
              disabled={!reasonValid}
              icon={breakGlass ? <ShieldAlert size={14} aria-hidden /> : <Eye size={14} aria-hidden />}
              onClick={() =>
                void start.mutate({
                  organization_id: orgId,
                  reason: reason.trim(),
                  mode,
                  ttl_minutes: ttl,
                })
              }
            >
              {breakGlass ? 'Break glass' : 'Start read-only'}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field
            label="Reason"
            required
            hint="Recorded verbatim in the audit log and shown in the active-session banner."
          >
            <Textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
              placeholder="e.g. Investigating a failed official run reported by the bank."
              invalid={reason.length > 0 && !reasonValid}
            />
          </Field>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Mode">
              <Select value={mode} onChange={(e) => setMode(e.target.value as InspectMode)}>
                <option value="consent">Consent — read-only</option>
                <option value="break_glass">Break glass — emergency, no consent</option>
              </Select>
            </Field>
            <Field label="Duration">
              <Select value={String(ttl)} onChange={(e) => setTtl(Number(e.target.value))}>
                {TTL_PRESETS.map((m) => (
                  <option key={m} value={m}>
                    {m} minutes
                  </option>
                ))}
              </Select>
            </Field>
          </div>

          <div
            className={`rounded-md border p-3 text-caption ${
              breakGlass
                ? 'border-critical/40 bg-critical-light text-critical'
                : 'border-warning/40 bg-warning-light text-warning'
            }`}
          >
            {breakGlass ? (
              <p>
                <span className="font-medium">Break glass</span> opens a read / write session — you
                will be able to modify this tenant&apos;s data. Use only for an emergency the tenant
                cannot resolve themselves.
              </p>
            ) : (
              <p>
                A <span className="font-medium">read-only</span> session lets you view this
                tenant&apos;s data without any ability to change it.
              </p>
            )}
          </div>
        </div>
      </Modal>
    </>
  );
}
