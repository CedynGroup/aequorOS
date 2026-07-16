'use client';

/**
 * Live breach alerts bell for the app header. Polls the bank alerts view and
 * renders the open limit-breach count as a badge (red when > 0). Clicking opens
 * a popover listing each breach — module, severity, message, and relative time
 * — with a link straight to the offending module's dashboard.
 *
 * Replaces the previous static bell + mocked notification drawer.
 */

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { Bell } from 'lucide-react';
import type { AlertItemRead, AlertSeverity } from '@aequoros/risk-service-api';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import { useBankContext } from '@/components/shell/BankContext';
import { useBankAlerts } from '@/lib/api/hooks';
import { fmtRelative, labelize } from '@/lib/api/values';
import { LIVE_MODULE_HREFS, LIVE_MODULE_LABELS } from './moduleDisplay';

function severityTone(severity: AlertSeverity): StatusTone {
  switch (severity) {
    case 'critical':
    case 'high':
      return 'breach';
    case 'medium':
      return 'amber';
    default:
      return 'slate';
  }
}

export default function AlertsBell() {
  const { bank } = useBankContext();
  const alertsQuery = useBankAlerts(bank?.id);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const total = alertsQuery.data?.total ?? 0;
  const items = alertsQuery.data?.items ?? [];

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        aria-label={`Alerts${total > 0 ? ` (${total} active)` : ''}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="relative w-9 h-9 inline-flex items-center justify-center rounded text-slate hover:bg-surface"
      >
        <Bell size={16} aria-hidden />
        {total > 0 && (
          <span className="absolute -top-0.5 -right-0.5 min-w-[16px] h-4 px-1 inline-flex items-center justify-center rounded-full bg-critical text-white text-[10px] font-semibold leading-none ring-2 ring-white">
            {total > 99 ? '99+' : total}
          </span>
        )}
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Active breaches"
          className="absolute right-0 mt-1.5 w-96 max-w-[calc(100vw-2rem)] bg-white border border-border rounded-md shadow-pop z-40 overflow-hidden"
        >
          <div className="px-4 py-3 border-b border-border-light flex items-center justify-between">
            <div>
              <p className="text-body font-medium text-navy">Limit breaches</p>
              <p className="text-caption text-slate">
                {total === 0
                  ? 'No active breaches'
                  : `${total} active across ${
                      Object.keys(alertsQuery.data?.byModule ?? {}).length
                    } module${
                      Object.keys(alertsQuery.data?.byModule ?? {}).length === 1
                        ? ''
                        : 's'
                    }`}
              </p>
            </div>
            {total > 0 && (
              <StatusPill tone="breach">{total}</StatusPill>
            )}
          </div>

          <div className="max-h-[24rem] overflow-y-auto">
            {items.length === 0 ? (
              <div className="px-4 py-8 text-center">
                <p className="text-body text-slate">No active breaches</p>
                <p className="mt-1 text-caption text-slate">
                  Live limits are within tolerance for {bank?.name ?? 'this bank'}.
                </p>
              </div>
            ) : (
              <ul className="divide-y divide-border-light">
                {items.map((alert: AlertItemRead) => (
                  <li key={alert.findingId}>
                    <Link
                      href={LIVE_MODULE_HREFS[alert.module] ?? '/'}
                      onClick={() => setOpen(false)}
                      className="block px-4 py-3 hover:bg-surface"
                    >
                      <div className="flex items-center gap-2 mb-1">
                        <StatusPill tone={severityTone(alert.severity)}>
                          {alert.severity}
                        </StatusPill>
                        <span className="text-caption font-medium text-navy">
                          {LIVE_MODULE_LABELS[alert.module] ?? labelize(alert.module)}
                        </span>
                        <span className="ml-auto text-caption text-slate whitespace-nowrap">
                          {fmtRelative(alert.createdAt)}
                        </span>
                      </div>
                      <p className="text-body text-navy/85 leading-snug">
                        {alert.message}
                      </p>
                      {alert.metric && (
                        <p className="mt-0.5 text-caption font-mono text-slate">
                          {alert.metric}
                        </p>
                      )}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
