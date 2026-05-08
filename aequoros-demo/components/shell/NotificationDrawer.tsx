'use client';

import { X, AlertCircle, CheckCircle2, Info, Clock } from 'lucide-react';
import { useEffect } from 'react';

const notifications = [
  {
    id: '1',
    severity: 'amber',
    title: 'BoG Monthly Prudential Return — review pending',
    body: 'BSD-2 Q1 due in 8 days. Three line items flagged for treasury review.',
    when: '2h ago',
    Icon: AlertCircle,
    color: 'text-warning',
    bg: 'bg-warning-light/40',
  },
  {
    id: '2',
    severity: 'success',
    title: 'LCR run completed — 142.0%',
    body: 'Daily recalculation finished. All 28 BoG validation checks passed.',
    when: '5h ago',
    Icon: CheckCircle2,
    color: 'text-success',
    bg: 'bg-success-light/40',
  },
  {
    id: '3',
    severity: 'info',
    title: 'New AI hedging recommendation',
    body: 'Deep RL: Add 6M IRS notional GHS 50M, pay fixed at 25.30%. Confidence 81%.',
    when: '5h ago',
    Icon: Info,
    color: 'text-action',
    bg: 'bg-action-light/40',
  },
  {
    id: '4',
    severity: 'amber',
    title: 'FX hedge expiring in 11 days',
    body: 'FX-2025-091 (USD 4M forward at 12.62) expires 12 Apr. ML model recommends extending.',
    when: 'Yesterday',
    Icon: Clock,
    color: 'text-warning',
    bg: 'bg-warning-light/40',
  },
  {
    id: '5',
    severity: 'success',
    title: 'Capital Adequacy Return Q4 2025 acknowledged',
    body: 'BoG returns receipt confirmed. No follow-up queries.',
    when: 'Yesterday',
    Icon: CheckCircle2,
    color: 'text-success',
    bg: 'bg-success-light/40',
  },
];

export default function NotificationDrawer({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

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
        className="absolute inset-0 bg-navy/30 backdrop-blur-sm"
      />
      <aside className="relative w-full max-w-md bg-white border-l border-border h-full flex flex-col shadow-pop">
        <div className="h-16 px-5 border-b border-border-light flex items-center justify-between">
          <div>
            <h2 className="text-h3 text-navy">Notifications</h2>
            <p className="text-caption text-slate">
              {notifications.length} active · 3 require action
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
          <ul className="divide-y divide-border-light">
            {notifications.map((n) => (
              <li
                key={n.id}
                className={`px-5 py-4 hover:bg-surface-alt cursor-pointer ${n.bg}`}
              >
                <div className="flex items-start gap-3">
                  <n.Icon size={16} className={`shrink-0 mt-0.5 ${n.color}`} aria-hidden />
                  <div className="flex-1 min-w-0">
                    <p className="text-body font-medium text-navy">{n.title}</p>
                    <p className="mt-1 text-body text-navy/75 leading-relaxed">{n.body}</p>
                    <p className="mt-1.5 text-caption text-slate">{n.when}</p>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </div>
        <div className="px-5 py-3 border-t border-border-light flex items-center justify-between">
          <button
            type="button"
            className="text-caption font-medium text-action hover:text-action-hover"
          >
            Mark all as read
          </button>
          <button
            type="button"
            className="text-caption font-medium text-slate hover:text-navy"
          >
            Notification settings →
          </button>
        </div>
      </aside>
    </div>
  );
}
