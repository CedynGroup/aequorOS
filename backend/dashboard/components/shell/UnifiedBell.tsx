'use client';

/**
 * The ONE header bell: limit breaches and workflow notifications in a single
 * popover with two tabs, per the approved design (consolidated notification
 * center). Badge shows breaches + unread inbox; red whenever a breach is
 * open, accent otherwise. Deep links land on the module's limit sub-page;
 * the full inbox drawer remains reachable from the Inbox tab footer.
 */

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Bell } from 'lucide-react';
import type { AlertItemRead, AlertSeverity } from '@aequoros/risk-service-api';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import { useBankContext } from '@/components/shell/BankContext';
import NotificationDrawer, {
  notificationHref,
} from '@/components/shell/NotificationDrawer';
import {
  useBankAlerts,
  useMarkNotificationRead,
  useNotifications,
} from '@/lib/api/hooks';
import { fmtRelative, labelize } from '@/lib/api/values';
import {
  LIVE_MODULE_HREFS,
  LIVE_MODULE_LABELS,
} from '@/components/live/moduleDisplay';

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

export default function UnifiedBell() {
  const { bank } = useBankContext();
  const router = useRouter();
  const alertsQuery = useBankAlerts(bank?.id);
  const feed = useNotifications();
  const markRead = useMarkNotificationRead();

  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<'breaches' | 'inbox'>('breaches');
  const [drawerOpen, setDrawerOpen] = useState(false);
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

  const breachTotal = alertsQuery.data?.total ?? 0;
  const breaches = alertsQuery.data?.items ?? [];
  const unread = feed.data?.unreadCount ?? 0;
  const notifications = feed.data?.notifications ?? [];
  const badge = breachTotal + unread;

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        aria-label={`Notifications${badge > 0 ? ` (${badge})` : ''}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => {
          setOpen((v) => !v);
          setTab(breachTotal > 0 ? 'breaches' : 'inbox');
        }}
        className="relative w-9 h-9 inline-flex items-center justify-center rounded text-slate hover:bg-surface"
      >
        <Bell size={16} aria-hidden />
        {badge > 0 && (
          <span
            className={`absolute -top-0.5 -right-0.5 min-w-[16px] h-4 px-1 inline-flex items-center justify-center rounded-full text-white text-[10px] font-semibold leading-none ring-2 ring-surface-raised ${
              breachTotal > 0 ? 'bg-critical' : 'bg-action'
            }`}
          >
            {badge > 99 ? '99+' : badge}
          </span>
        )}
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Notifications"
          className="absolute right-0 mt-1.5 w-96 max-w-[calc(100vw-2rem)] bg-surface-raised border border-border rounded-md shadow-pop z-40 overflow-hidden"
        >
          <div className="flex border-b border-border-light">
            <button
              type="button"
              onClick={() => setTab('breaches')}
              className={`flex-1 px-4 py-2.5 text-caption font-medium inline-flex items-center justify-center gap-2 border-b-2 ${
                tab === 'breaches'
                  ? 'border-action text-navy'
                  : 'border-transparent text-slate hover:text-navy'
              }`}
            >
              Breaches
              {breachTotal > 0 && <StatusPill tone="breach">{breachTotal}</StatusPill>}
            </button>
            <button
              type="button"
              onClick={() => setTab('inbox')}
              className={`flex-1 px-4 py-2.5 text-caption font-medium inline-flex items-center justify-center gap-2 border-b-2 ${
                tab === 'inbox'
                  ? 'border-action text-navy'
                  : 'border-transparent text-slate hover:text-navy'
              }`}
            >
              Inbox
              {unread > 0 && <StatusPill tone="action">{unread}</StatusPill>}
            </button>
          </div>

          {tab === 'breaches' ? (
            <>
              <div className="max-h-[22rem] overflow-y-auto">
                {breaches.length === 0 ? (
                  <div className="px-4 py-8 text-center">
                    <p className="text-body text-slate">No active breaches</p>
                    <p className="mt-1 text-caption text-slate">
                      Live limits are within tolerance for {bank?.name ?? 'this bank'}.
                    </p>
                  </div>
                ) : (
                  <ul className="divide-y divide-border-light">
                    {breaches.map((alert: AlertItemRead) => (
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
                          <p className="text-caption text-navy/85 leading-relaxed">
                            {alert.message}
                          </p>
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <Link
                href="/alerts"
                onClick={() => setOpen(false)}
                className="block px-4 py-2.5 text-caption font-medium text-action border-t border-border-light hover:bg-surface"
              >
                Open Alert Center →
              </Link>
            </>
          ) : (
            <>
              <div className="max-h-[22rem] overflow-y-auto">
                {notifications.length === 0 ? (
                  <div className="px-4 py-8 text-center">
                    <p className="text-body text-slate">Nothing in the inbox</p>
                    <p className="mt-1 text-caption text-slate">
                      Deadlines, approvals, and signature requests land here.
                    </p>
                  </div>
                ) : (
                  <ul className="divide-y divide-border-light">
                    {notifications.map((n) => (
                      <li key={n.id}>
                        <button
                          type="button"
                          onClick={() => {
                            if (!n.readAt) markRead.mutate(n.id);
                            const href = notificationHref(n);
                            setOpen(false);
                            if (href) router.push(href);
                          }}
                          className={`w-full text-left px-4 py-3 hover:bg-surface ${
                            n.readAt ? '' : 'bg-action-light/20'
                          }`}
                        >
                          <div className="flex items-center gap-2">
                            {!n.readAt && (
                              <span className="w-1.5 h-1.5 rounded-full bg-action shrink-0" />
                            )}
                            <span className="text-caption font-medium text-navy truncate">
                              {n.title}
                            </span>
                            <span className="ml-auto text-caption text-slate whitespace-nowrap">
                              {fmtRelative(n.createdAt)}
                            </span>
                          </div>
                          {n.body && (
                            <p className="mt-1 text-caption text-slate leading-relaxed line-clamp-2">
                              {n.body}
                            </p>
                          )}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <button
                type="button"
                onClick={() => {
                  setOpen(false);
                  setDrawerOpen(true);
                }}
                className="w-full text-left px-4 py-2.5 text-caption font-medium text-action border-t border-border-light hover:bg-surface"
              >
                Open full inbox →
              </button>
            </>
          )}
        </div>
      )}

      <NotificationDrawer open={drawerOpen} onClose={() => setDrawerOpen(false)} />
    </div>
  );
}
