'use client';

/**
 * Notification drawer — the real in-app feed (plan W3).
 *
 * Rows are emitted server-side by the reporting workflow (approvals, regulator
 * decisions) and the daily deadline scan (due-soon / overdue / re-upload
 * pending), visible to the signed-in user (direct rows) plus org-wide rows.
 * Unread rows are highlighted; clicking marks read and deep-links to the
 * relevant surface when the notification carries a package/bank entity.
 */

import { X, AlertCircle, AlertTriangle, Info } from 'lucide-react';
import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import type { NotificationRead } from '@aequoros/risk-service-api';
import {
  useMarkAllNotificationsRead,
  useMarkNotificationRead,
  useNotifications,
} from '@/lib/api/hooks';
import { fmtRelative } from '@/lib/api/values';

const SEVERITY_STYLES: Record<
  string,
  { Icon: typeof Info; color: string; unreadBg: string }
> = {
  info: { Icon: Info, color: 'text-action', unreadBg: 'bg-action-light/30' },
  warning: {
    Icon: AlertTriangle,
    color: 'text-warning',
    unreadBg: 'bg-warning-light/30',
  },
  critical: {
    Icon: AlertCircle,
    color: 'text-critical',
    unreadBg: 'bg-critical-light/30',
  },
};

/** Deep-link target for a notification, when its entity supports one. */
export function notificationHref(notification: NotificationRead): string | null {
  // The calendar, explicitly: /submissions now redirects to the Returns
  // workspace, and a deadline notification is asking the reader to look at
  // the deadline board.
  if (notification.type.startsWith('reporting.deadline.')) return '/submissions/calendar';
  if (notification.entityType === 'regulatory_package') {
    return '/submissions/history';
  }
  return null;
}

export default function NotificationDrawer({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const router = useRouter();
  const feed = useNotifications();
  const markRead = useMarkNotificationRead();
  const markAll = useMarkAllNotificationsRead();

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  const notifications = feed.data?.notifications ?? [];
  const unread = feed.data?.unreadCount ?? 0;

  const openNotification = (notification: NotificationRead) => {
    if (notification.readAt == null) {
      markRead.mutate(notification.id);
    }
    const href = notificationHref(notification);
    if (href) {
      onClose();
      router.push(href);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Notifications"
      className="fixed inset-0 z-50 flex justify-end"
    >
      <button
        type="button"
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
      />
      <aside className="relative w-full max-w-md bg-surface-raised border-l border-border h-full flex flex-col shadow-pop">
        <div className="h-16 px-5 border-b border-border-light flex items-center justify-between">
          <div>
            <h2 className="text-h3 text-navy">Notifications</h2>
            <p className="text-caption text-slate">
              {feed.isLoading
                ? 'Loading…'
                : `${notifications.length} recent · ${unread} unread`}
            </p>
          </div>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="w-9 h-9 rounded text-slate hover:bg-surface inline-flex items-center justify-center"
          >
            <X size={16} aria-hidden />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto">
          {feed.error ? (
            <p className="px-5 py-6 text-body text-slate">
              Could not load notifications. They will retry automatically.
            </p>
          ) : notifications.length === 0 && !feed.isLoading ? (
            <p className="px-5 py-6 text-body text-slate">
              Nothing yet — approvals, regulator decisions, and reporting
              deadlines will appear here.
            </p>
          ) : (
            <ul className="divide-y divide-border-light">
              {notifications.map((notification) => {
                const style =
                  SEVERITY_STYLES[notification.severity] ?? SEVERITY_STYLES.info;
                const isUnread = notification.readAt == null;
                return (
                  <li key={notification.id}>
                    <button
                      type="button"
                      onClick={() => openNotification(notification)}
                      className={`w-full text-left px-5 py-4 hover:bg-surface-alt ${
                        isUnread ? style.unreadBg : ''
                      }`}
                    >
                      <div className="flex items-start gap-3">
                        <style.Icon
                          size={16}
                          className={`shrink-0 mt-0.5 ${style.color}`}
                          aria-hidden
                        />
                        <div className="flex-1 min-w-0">
                          <p
                            className={`text-body text-navy ${
                              isUnread ? 'font-semibold' : 'font-medium'
                            }`}
                          >
                            {notification.title}
                          </p>
                          <p className="mt-1 text-body text-navy/75 leading-relaxed">
                            {notification.body}
                          </p>
                          <p className="mt-1.5 text-caption text-slate">
                            {fmtRelative(notification.createdAt)}
                          </p>
                        </div>
                        {isUnread && (
                          <span
                            aria-label="Unread"
                            className="mt-1 w-2 h-2 rounded-full bg-action shrink-0"
                          />
                        )}
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
        <div className="px-5 py-3 border-t border-border-light flex items-center justify-between">
          <button
            type="button"
            onClick={() => markAll.mutate()}
            disabled={markAll.isPending || unread === 0}
            className="text-caption font-medium text-action hover:text-action-hover disabled:text-slate disabled:cursor-default"
          >
            {markAll.isPending ? 'Marking…' : 'Mark all as read'}
          </button>
          <span className="text-caption text-slate">
            Approvals · regulator decisions · deadlines
          </span>
        </div>
      </aside>
    </div>
  );
}
