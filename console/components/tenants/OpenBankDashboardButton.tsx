'use client';

import { useState } from 'react';
import { ExternalLink } from 'lucide-react';
import { mintInspectorActToken } from '@/lib/api';
import { useInspector } from '@/lib/inspector';
import { useMutation } from '@/lib/use-api';
import { relTime } from '@/lib/format';
import { Button, Modal, useToast } from '@/components/ui';

/**
 * Act-as-examiner launch. From WITHIN an active inspection session for THIS org,
 * opens the bank's own live dashboard as a read-only examiner in a new tab.
 *
 * Hand-off contract (the bank dashboard implements the matching accept side):
 *  1. mint a short-lived (≤15 min) read-only examiner act-token for the session,
 *  2. open `<dashboard_url>/inspect#<encodeURIComponent(act_token)>` in a NEW tab.
 * The token rides the URL FRAGMENT — never a query param — so it is never sent
 * to a server or written to an access log; `noopener` severs the opener handle.
 *
 * Self-gating: renders nothing unless the shell's active inspector session is
 * for THIS org (the Inspect button starts that session first). Enabled only
 * while a session is active.
 */
export function OpenBankDashboardButton({
  orgId,
  orgLabel,
}: {
  orgId: string;
  orgLabel?: string;
}) {
  const { active } = useInspector();
  const toaster = useToast();
  const [open, setOpen] = useState(false);

  const mint = useMutation(mintInspectorActToken, {
    // Bespoke error handling: the 503 (impersonation not configured) gets a
    // plain-English message; everything else toasts like the shared default.
    toast: false,
    onSuccess: (res) => {
      const url = `${res.dashboard_url}/inspect#${encodeURIComponent(res.act_token)}`;
      window.open(url, '_blank', 'noopener');
      setOpen(false);
    },
    onError: (err) => {
      if (err.status === 503 || err.code.toLowerCase().includes('impersonation')) {
        toaster.error("Act-as-examiner isn't configured on this environment.");
      } else {
        toaster.error('Open bank dashboard failed', {
          description: `${err.code} · ${err.message}`,
        });
      }
    },
  });

  // Gate: only surface the action while THIS org has a live inspection session.
  if (!active || active.organization_id !== orgId) return null;
  const session = active;
  const bankLabel = orgLabel ?? orgId;

  return (
    <>
      <Button
        variant="secondary"
        size="sm"
        icon={<ExternalLink size={14} aria-hidden />}
        onClick={() => setOpen(true)}
      >
        Open bank dashboard
      </Button>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Open bank dashboard as examiner"
        description={`Read-only, act-as-examiner access to ${bankLabel}'s live dashboard.`}
        size="md"
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)} disabled={mint.loading}>
              Cancel
            </Button>
            <Button
              variant="primary"
              loading={mint.loading}
              icon={<ExternalLink size={14} aria-hidden />}
              onClick={() => void mint.mutate(session.session_id)}
            >
              Open in new tab
            </Button>
          </>
        }
      >
        <p className="text-body text-navy/90">
          This opens <span className="font-medium text-navy">{bankLabel}</span>&apos;s live dashboard
          in a new tab as a read-only examiner. They&apos;ll see an{' '}
          <span className="font-medium">&ldquo;AequorOS staff is viewing&rdquo;</span> banner for the
          duration. The session ends {relTime(session.expires_at)}.
        </p>
      </Modal>
    </>
  );
}
