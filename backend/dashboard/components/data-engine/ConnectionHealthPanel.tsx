'use client';

/**
 * Connection health — the one screen a bank's IT team reads when a feed breaks.
 * Aggregates every configured data-source connection (core database-direct,
 * Temenos T24, market-data vendors) with its live status, last successful sync,
 * credential expiry, and a plain-language remediation hint. Read-only; fixing
 * happens in each integration's own tab.
 */

import Link from 'next/link';
import { Activity } from 'lucide-react';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import { SkeletonLine } from '@/components/ui/Skeleton';
import { useBankContext } from '@/components/shell/BankContext';
import { useDatabaseConnections } from '@/lib/api/database-direct';
import { useMarketDataConnections, useTemenosConnections } from '@/lib/api/hooks';
import { fmtRelative, labelize } from '@/lib/api/values';

interface HealthRow {
  id: string;
  kind: string;
  href: string;
  name: string;
  status: string;
  lastAt: Date | null;
  lastStatus: string | null;
  credentialExpiresAt: Date | null;
  validationError: string | null;
}

/** Generated models vary between Date and ISO-string for timestamps — normalize. */
function asDate(value: Date | string | null | undefined): Date | null {
  if (!value) return null;
  return value instanceof Date ? value : new Date(value);
}

const MS_PER_DAY = 86_400_000;
const EXPIRY_WARN_DAYS = 30;

function tone(status: string): StatusTone {
  const s = status.toLowerCase();
  if (s === 'active') return 'success';
  if (s === 'testing' || s === 'disabled' || s === 'replaced_pending_deletion') return 'slate';
  if (s === 'expiring_soon') return 'amber';
  return 'critical'; // expired / revoked / invalid
}

/** Plain-language "what your IT should do" — no vendor jargon, no stack traces. */
function remediation(row: HealthRow): string | null {
  const s = row.status.toLowerCase();
  if (s === 'expired' || s === 'revoked') {
    return 'Service credential no longer valid — rotate it at the source system and update it in the connection tab.';
  }
  if (s === 'invalid') {
    return (
      row.validationError ??
      'Connection test failed — check host, port, and network access from AequorOS to the source.'
    );
  }
  if (s === 'expiring_soon') {
    return 'Service credential expires soon — rotate it before syncs start failing.';
  }
  if (row.lastStatus && /fail|error|reject/i.test(row.lastStatus)) {
    return 'The last sync did not complete — open the connection tab for record-level diagnostics.';
  }
  if (
    row.credentialExpiresAt &&
    row.credentialExpiresAt.getTime() - Date.now() < EXPIRY_WARN_DAYS * MS_PER_DAY
  ) {
    return `Credential expires ${fmtRelative(row.credentialExpiresAt)} — plan the rotation.`;
  }
  return null;
}

export default function ConnectionHealthPanel() {
  const { bank } = useBankContext();
  const dbDirect = useDatabaseConnections(bank?.id);
  const temenos = useTemenosConnections(bank?.id);
  const marketData = useMarketDataConnections(bank?.id);

  const loading = dbDirect.isLoading || temenos.isLoading || marketData.isLoading;

  const rows: HealthRow[] = [
    ...(dbDirect.data?.connections ?? []).map((c) => ({
      id: c.id,
      kind: `Core database · ${labelize(c.backend)}`,
      href: '/data-engine/database',
      name: c.displayName,
      status: c.status,
      lastAt: asDate(c.lastSyncedAt),
      lastStatus: c.lastSyncStatus ?? null,
      credentialExpiresAt: asDate(c.credentialExpiresAt),
      validationError: c.validationError ?? null,
    })),
    ...(temenos.data?.connections ?? []).map((c) => ({
      id: c.id,
      kind: `Core banking · ${labelize(c.coreSystem)}`,
      href: '/data-engine/t24',
      name: c.displayName,
      status: c.status,
      lastAt: asDate(c.lastPullAt),
      lastStatus: c.lastPullStatus ?? null,
      credentialExpiresAt: asDate(c.credentialExpiresAt),
      validationError: c.validationError ?? null,
    })),
    ...(marketData.data?.connections ?? []).map((c) => ({
      id: c.id,
      kind: `Market data · ${labelize(c.vendor)}`,
      href: '/data-engine/market-data',
      name: c.displayName,
      status: c.status,
      lastAt: asDate(c.lastPullAt),
      lastStatus: c.lastPullStatus ?? null,
      credentialExpiresAt: asDate(c.credentialExpiresAt),
      validationError: c.validationError ?? null,
    })),
  ];

  const attention = rows.filter((row) => remediation(row) !== null).length;

  return (
    <Card>
      <CardHeader
        title="Connection health"
        subtitle="Live status of every configured data source — the first stop when a feed breaks"
        action={
          loading ? undefined : rows.length === 0 ? undefined : (
            <StatusPill tone={attention === 0 ? 'success' : 'amber'}>
              {attention === 0 ? 'All healthy' : `${attention} need attention`}
            </StatusPill>
          )
        }
      />
      <CardBody className="p-0">
        {loading ? (
          <div className="p-5 space-y-3">
            <SkeletonLine width="65%" />
            <SkeletonLine width="50%" />
          </div>
        ) : rows.length === 0 ? (
          <p className="px-5 py-4 text-body text-slate">
            No data-source connections configured yet. Set one up in the Database,
            T24, or Market data tab — uploads via Excel &amp; CSV work without a
            connection.
          </p>
        ) : (
          <ul className="divide-y divide-border-light">
            {rows.map((row) => {
              const hint = remediation(row);
              return (
                <li key={`${row.kind}-${row.id}`} className="px-5 py-3">
                  <div className="flex items-center gap-3">
                    <Activity size={15} className="text-slate shrink-0" aria-hidden />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <Link
                          href={row.href}
                          className="text-body font-medium text-navy hover:underline truncate"
                        >
                          {row.name}
                        </Link>
                        <span className="text-caption text-slate shrink-0">{row.kind}</span>
                      </div>
                      <p className="mt-0.5 text-caption text-slate">
                        {row.lastAt
                          ? `Last sync ${fmtRelative(row.lastAt)}${
                              row.lastStatus ? ` · ${labelize(row.lastStatus)}` : ''
                            }`
                          : 'Never synced'}
                      </p>
                    </div>
                    <StatusPill tone={tone(row.status)} className="shrink-0">
                      {labelize(row.status)}
                    </StatusPill>
                  </div>
                  {hint && (
                    <p className="mt-2 ml-7 text-caption text-warning leading-relaxed">
                      {hint}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}
