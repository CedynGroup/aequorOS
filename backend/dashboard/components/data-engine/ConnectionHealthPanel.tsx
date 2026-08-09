'use client';

/**
 * Connection health — the one screen a bank's IT team reads when a feed breaks.
 * Aggregates every configured data-source connection (core database-direct,
 * Temenos T24, market-data vendors) with its live status, last successful sync,
 * credential expiry, and a plain-language remediation hint. Per-row Test runs
 * the source's read-only reachability probe; fixing still happens in each
 * integration's own tab.
 */

import { useState } from 'react';
import Link from 'next/link';
import { Activity, Loader2 } from 'lucide-react';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import EmptyState from '@/components/ui/EmptyState';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import { SkeletonLine } from '@/components/ui/Skeleton';
import { useBankContext } from '@/components/shell/BankContext';
import { isApiError } from '@/lib/api/client';
import {
  useDatabaseConnections,
  useTestDatabaseConnection,
} from '@/lib/api/database-direct';
import {
  useMarketDataConnections,
  useTemenosConnections,
  useTestMarketDataConnection,
  useTestTemenosConnection,
} from '@/lib/api/hooks';
import { fmtRelative, labelize } from '@/lib/api/values';

type SourceKind = 'database' | 'temenos' | 'market-data';

interface HealthRow {
  id: string;
  source: SourceKind;
  kind: string;
  href: string;
  name: string;
  status: string;
  lastAt: Date | null;
  lastStatus: string | null;
  credentialExpiresAt: Date | null;
  validationError: string | null;
}

type TestOutcome =
  | { state: 'running' }
  | { state: 'passed'; detail: string }
  | { state: 'failed'; detail: string };

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

  // Read-only reachability probes — the same test endpoints the source tabs use.
  const testDatabase = useTestDatabaseConnection(bank?.id);
  const testTemenos = useTestTemenosConnection(bank?.id);
  const testMarketData = useTestMarketDataConnection(bank?.id);
  const [tests, setTests] = useState<Record<string, TestOutcome>>({});

  const runTest = async (row: HealthRow) => {
    const key = `${row.source}-${row.id}`;
    setTests((prev) => ({ ...prev, [key]: { state: 'running' } }));
    try {
      let outcome: TestOutcome;
      if (row.source === 'database') {
        const result = await testDatabase.mutateAsync(row.id);
        outcome = result.reachable
          ? {
              state: 'passed',
              detail: `Reachable${
                result.latencyMs != null ? ` · ${result.latencyMs} ms` : ''
              }`,
            }
          : {
              state: 'failed',
              detail: result.error ?? 'Connection test failed.',
            };
      } else {
        const result =
          row.source === 'temenos'
            ? await testTemenos.mutateAsync(row.id)
            : await testMarketData.mutateAsync(row.id);
        outcome = result.success
          ? { state: 'passed', detail: 'Test pull succeeded.' }
          : { state: 'failed', detail: result.error ?? 'Test pull failed.' };
      }
      setTests((prev) => ({ ...prev, [key]: outcome }));
    } catch (error) {
      setTests((prev) => ({
        ...prev,
        [key]: {
          state: 'failed',
          detail: isApiError(error) ? error.message : 'Connection test failed.',
        },
      }));
    }
  };

  const loading = dbDirect.isLoading || temenos.isLoading || marketData.isLoading;

  const rows: HealthRow[] = [
    ...(dbDirect.data?.connections ?? []).map((c) => ({
      id: c.id,
      source: 'database' as const,
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
      source: 'temenos' as const,
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
      source: 'market-data' as const,
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
          <div className="p-5">
            <EmptyState
              Icon={Activity}
              title="No data-source connections yet"
              description="Set one up in the Database, T24, or Market data tab — uploads via Excel & CSV work without a connection."
              action={
                <Link
                  href="/data-engine/database"
                  className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
                >
                  Connect a data source
                </Link>
              }
            />
          </div>
        ) : (
          <ul className="divide-y divide-border-light">
            {rows.map((row) => {
              const hint = remediation(row);
              const test = tests[`${row.source}-${row.id}`];
              const testing = test?.state === 'running';
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
                    <button
                      type="button"
                      onClick={() => void runTest(row)}
                      disabled={testing}
                      className="shrink-0 inline-flex items-center gap-1 px-2 py-1 text-caption font-medium text-slate border border-border rounded-md hover:bg-surface disabled:opacity-50 disabled:pointer-events-none"
                    >
                      {testing && (
                        <Loader2 size={11} className="animate-spin" aria-hidden />
                      )}
                      Test
                    </button>
                  </div>
                  {test && test.state !== 'running' && (
                    <p
                      className={`mt-2 ml-7 text-caption leading-relaxed ${
                        test.state === 'passed' ? 'text-success' : 'text-critical'
                      }`}
                    >
                      {test.detail}
                    </p>
                  )}
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
