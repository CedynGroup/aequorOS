'use client';

/**
 * Alert stream: open live findings grouped by module or severity. Each row
 * carries the severity dot, module chip, rule + metric context, and age, and
 * links into the owning module page. Read-only by design — the pipeline owns
 * the finding lifecycle (open on breach, superseded on clear) and the API
 * exposes no acknowledge mutation, so none is rendered.
 */

import Link from 'next/link';
import { ArrowUpRight } from 'lucide-react';
import type { AlertItemRead } from '@aequoros/risk-service-api';
import StatusPill from '@/components/ui/StatusPill';
import SectionCard from '@/components/ui/SectionCard';
import { fmtRelative, labelize } from '@/lib/api/values';

export type AlertGroupBy = 'module' | 'severity';

export const ALERT_MODULE_HREFS: Record<string, string> = {
  liquidity: '/liquidity',
  capital: '/basel',
  irr: '/irr',
  fx: '/fx',
  ftp: '/ftp',
  forecast: '/forecasting',
};

const SEVERITY_ORDER: Record<string, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
};

function severityDotClass(severity: string): string {
  if (severity === 'critical') return 'bg-critical';
  if (severity === 'high') return 'bg-warning';
  return 'bg-slate-light';
}

function severityTone(severity: string) {
  if (severity === 'critical') return 'critical' as const;
  if (severity === 'high') return 'amber' as const;
  return 'slate' as const;
}

function AlertRow({ item }: { item: AlertItemRead }) {
  const href = ALERT_MODULE_HREFS[item.module];
  return (
    <li className="px-5 py-3.5 flex items-start gap-3">
      <span
        aria-hidden
        title={item.severity}
        className={`mt-1.5 shrink-0 inline-block w-2 h-2 rounded-full ${severityDotClass(
          item.severity
        )}`}
      />
      <div className="min-w-0 flex-1">
        <p className="text-body text-navy leading-relaxed">{item.message}</p>
        <p className="mt-0.5 text-caption text-slate">
          <span className="font-mono">{labelize(item.ruleId)}</span>
          {item.metric && (
            <>
              {' · metric '}
              <span className="font-mono text-navy/80">{item.metric}</span>
            </>
          )}
        </p>
      </div>
      <div className="shrink-0 flex items-center gap-2.5">
        <StatusPill tone={severityTone(item.severity)}>{item.severity}</StatusPill>
        {href ? (
          <Link
            href={href}
            className="inline-flex items-center gap-1 text-caption font-medium text-action hover:underline whitespace-nowrap"
          >
            {labelize(item.module)}
            <ArrowUpRight size={12} aria-hidden />
          </Link>
        ) : (
          <span className="text-caption text-slate">{labelize(item.module)}</span>
        )}
        <span
          className="text-caption text-slate-light whitespace-nowrap font-mono tnum"
          title={item.createdAt.toISOString()}
        >
          {fmtRelative(item.createdAt)}
        </span>
      </div>
    </li>
  );
}

export default function AlertStream({
  items,
  groupBy,
}: {
  items: AlertItemRead[];
  groupBy: AlertGroupBy;
}) {
  const keys =
    groupBy === 'module'
      ? [...new Set(items.map((item) => item.module as string))].sort()
      : [...new Set(items.map((item) => item.severity as string))].sort(
          (a, b) => (SEVERITY_ORDER[a] ?? 99) - (SEVERITY_ORDER[b] ?? 99)
        );

  return (
    <div className="space-y-6">
      {keys.map((key) => {
        const groupItems = items.filter((item) =>
          groupBy === 'module' ? item.module === key : item.severity === key
        );
        return (
          <SectionCard
            key={key}
            title={
              <span className="inline-flex items-center gap-2.5">
                {labelize(key)}
                <span className="text-caption font-normal text-slate">
                  {groupItems.length} open
                </span>
              </span>
            }
            actions={
              groupBy === 'module' && ALERT_MODULE_HREFS[key] ? (
                <Link
                  href={ALERT_MODULE_HREFS[key]}
                  className="text-caption font-medium text-action hover:underline"
                >
                  Open module →
                </Link>
              ) : undefined
            }
            noPadding
          >
            <ul className="divide-y divide-border-light">
              {groupItems.map((item) => (
                <AlertRow key={item.findingId} item={item} />
              ))}
            </ul>
          </SectionCard>
        );
      })}
    </div>
  );
}
