'use client';

/**
 * One configured direct-database connection: status chip, backend, endpoint,
 * scoped schemas, last sync, and credential fingerprint, plus the
 * per-connection actions — Test, Discover schema, Sync, Edit, Disable/Enable,
 * Revoke. Stored credentials are never displayed; only the fingerprint
 * identifies what is on file.
 */

import { useState } from 'react';
import {
  CheckCircle2,
  Database,
  KeyRound,
  Loader2,
  PauseCircle,
  PlayCircle,
  ShieldAlert,
  Table2,
  XCircle,
  Zap,
} from 'lucide-react';
import type {
  DatabaseConnectionDiscoverResult,
  DatabaseConnectionRead,
  DatabaseConnectionTestResult,
} from '@aequoros/risk-service-api';
import { isApiError } from '@/lib/api/client';
import {
  useDisableDatabaseConnection,
  useDiscoverDatabaseSchema,
  useEnableDatabaseConnection,
  useRevokeDatabaseConnection,
  useTestDatabaseConnection,
  useUpdateDatabaseConnection,
} from '@/lib/api/database-direct';
import ConnectionForm, {
  type DbFormState,
  buildUpdatePayload,
  formStateFromConnection,
} from './ConnectionForm';
import SchemaPanel from './SchemaPanel';
import SyncPanel from './SyncPanel';
import { ConnectionStatusPill, backendName, fmtWhen } from './shared';
import { fmtLocale } from '@/lib/format';

function errorMessage(error: unknown): string {
  if (isApiError(error)) return error.message;
  if (error instanceof Error) return error.message;
  return 'The request failed.';
}

function ActionButton({
  onClick,
  disabled,
  active,
  icon,
  children,
  tone = 'default',
}: {
  onClick: () => void;
  disabled?: boolean;
  active?: boolean;
  icon: React.ReactNode;
  children: React.ReactNode;
  tone?: 'default' | 'danger';
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded border text-caption font-medium disabled:opacity-40 disabled:cursor-not-allowed ${
        tone === 'danger'
          ? 'border-critical/30 text-critical hover:bg-critical-light/40'
          : active
            ? 'border-action bg-action-light/60 text-action'
            : 'border-border text-navy hover:bg-surface'
      }`}
    >
      {icon}
      {children}
    </button>
  );
}

type OpenPanel = 'sync' | 'schema' | 'edit' | null;

export default function ConnectionCard({
  bankId,
  connection,
}: {
  bankId: string;
  connection: DatabaseConnectionRead;
}) {
  const test = useTestDatabaseConnection(bankId);
  const discover = useDiscoverDatabaseSchema(bankId);
  const update = useUpdateDatabaseConnection(bankId);
  const disable = useDisableDatabaseConnection(bankId);
  const enable = useEnableDatabaseConnection(bankId);
  const revoke = useRevokeDatabaseConnection(bankId);

  const [panel, setPanel] = useState<OpenPanel>(null);
  const [testResult, setTestResult] = useState<DatabaseConnectionTestResult | null>(null);
  const [schema, setSchema] = useState<DatabaseConnectionDiscoverResult | null>(null);
  const [editForm, setEditForm] = useState<DbFormState>(() =>
    formStateFromConnection(connection),
  );
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const isRevoked = connection.status === 'REVOKED';
  const isDisabled = connection.status === 'DISABLED';
  const busy =
    test.isPending ||
    discover.isPending ||
    update.isPending ||
    disable.isPending ||
    enable.isPending ||
    revoke.isPending;

  const run = async (action: () => Promise<unknown>, doneNotice?: string) => {
    setActionError(null);
    setNotice(null);
    try {
      await action();
      if (doneNotice) setNotice(doneNotice);
    } catch (error) {
      setActionError(errorMessage(error));
    }
  };

  const togglePanel = (next: OpenPanel) => {
    setActionError(null);
    setNotice(null);
    setPanel((current) => {
      const opening = current === next ? null : next;
      if (opening === 'edit') setEditForm(formStateFromConnection(connection));
      return opening;
    });
  };

  const runDiscover = () =>
    run(async () => {
      setPanel('schema');
      setSchema(await discover.mutateAsync(connection.id));
    });

  const submitEdit = () =>
    run(async () => {
      await update.mutateAsync({
        connectionId: connection.id,
        payload: buildUpdatePayload(editForm),
      });
      setPanel(null);
    }, 'Connection updated. New credentials, if any, were validated before the swap.');

  return (
    <section className="card p-5 space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Database size={16} className="text-slate" aria-hidden />
        <h3 className="text-h3 text-navy">{connection.displayName}</h3>
        <span className="text-caption text-slate">{backendName(connection.backend)}</span>
        <ConnectionStatusPill status={connection.status} />
      </div>

      <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2 lg:grid-cols-4 text-body">
        <div className="min-w-0">
          <dt className="text-caption text-slate">Endpoint</dt>
          <dd
            className="font-mono text-navy truncate"
            title={`${connection.host ?? ''}${connection.port != null ? `:${connection.port}` : ''}`}
          >
            {connection.host ?? '—'}
            {connection.port != null && (
              <span className="text-slate">:{connection.port}</span>
            )}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-caption text-slate">
            {connection.serviceName ? 'Service name' : 'Database'}
          </dt>
          <dd className="font-mono text-navy truncate">
            {connection.serviceName || connection.database || '—'}
          </dd>
        </div>
        <div>
          <dt className="text-caption text-slate">Schemas</dt>
          <dd className="font-mono text-navy">
            {connection.schemas.length > 0 ? connection.schemas.join(', ') : 'default'}
          </dd>
        </div>
        <div>
          <dt className="text-caption text-slate">Last sync</dt>
          <dd className="font-mono text-navy">
            {fmtWhen(connection.lastSyncedAt)}
            {connection.lastSyncStatus && (
              <span
                className={
                  /reject|fail/i.test(connection.lastSyncStatus)
                    ? 'text-critical ml-1.5'
                    : /warn/i.test(connection.lastSyncStatus)
                      ? 'text-warning ml-1.5'
                      : 'text-success ml-1.5'
                }
              >
                ({connection.lastSyncStatus.replaceAll('_', ' ').toLowerCase()})
              </span>
            )}
          </dd>
        </div>
      </dl>

      <div className="flex flex-wrap gap-x-6 gap-y-1 text-caption text-slate">
        <span>
          TLS{' '}
          <span className="text-navy">
            {connection.tlsEnabled
              ? connection.tlsVerifyServerCertificate
                ? 'on · cert verified'
                : 'on · cert not verified'
              : 'off'}
          </span>
        </span>
        {connection.preferReadReplica && (
          <span>
            Read replica{' '}
            <span className="text-navy">
              preferred{connection.readReplicas.length > 0 ? ` (${connection.readReplicas.length})` : ''}
            </span>
          </span>
        )}
        <span>
          Query timeout{' '}
          <span className="font-mono text-navy">{connection.queryTimeoutSeconds}s</span>
        </span>
        <span>
          Credential{' '}
          <span
            className="font-mono text-navy"
            title={connection.credentialFingerprint ?? undefined}
          >
            {connection.credentialFingerprint
              ? `${connection.credentialFingerprint.slice(0, 12)}…`
              : 'none stored'}
          </span>
        </span>
        {connection.credentialExpiresAt && (
          <span>
            Expires{' '}
            <span className="font-mono text-navy">
              {fmtWhen(connection.credentialExpiresAt)}
            </span>
          </span>
        )}
      </div>

      {connection.validationError && (
        <div className="rounded border border-warning/30 bg-warning-light/50 px-4 py-3">
          <p className="text-body text-navy">{connection.validationError}</p>
        </div>
      )}

      {!isRevoked && (
        <div className="flex flex-wrap gap-2">
          <ActionButton
            onClick={() =>
              run(async () => {
                setTestResult(await test.mutateAsync(connection.id));
              })
            }
            disabled={busy || isDisabled}
            icon={
              test.isPending ? (
                <Loader2 size={13} className="animate-spin" aria-hidden />
              ) : (
                <Zap size={13} aria-hidden />
              )
            }
          >
            Test
          </ActionButton>
          <ActionButton
            onClick={() => {
              if (panel === 'schema') {
                setPanel(null);
              } else {
                void runDiscover();
              }
            }}
            disabled={busy || isDisabled}
            active={panel === 'schema'}
            icon={
              discover.isPending ? (
                <Loader2 size={13} className="animate-spin" aria-hidden />
              ) : (
                <Table2 size={13} aria-hidden />
              )
            }
          >
            Discover schema
          </ActionButton>
          <ActionButton
            onClick={() => togglePanel('sync')}
            disabled={busy || isDisabled}
            active={panel === 'sync'}
            icon={<Database size={13} aria-hidden />}
          >
            {panel === 'sync' ? 'Close sync' : 'Sync'}
          </ActionButton>
          <ActionButton
            onClick={() => togglePanel('edit')}
            disabled={busy}
            active={panel === 'edit'}
            icon={<KeyRound size={13} aria-hidden />}
          >
            {panel === 'edit' ? 'Cancel edit' : 'Edit'}
          </ActionButton>
          {isDisabled ? (
            <ActionButton
              onClick={() =>
                run(
                  () => enable.mutateAsync(connection.id),
                  'Connection re-validated and enabled.',
                )
              }
              disabled={busy}
              icon={<PlayCircle size={13} aria-hidden />}
            >
              Enable
            </ActionButton>
          ) : (
            <ActionButton
              onClick={() =>
                run(
                  () => disable.mutateAsync(connection.id),
                  'Connection disabled. Scheduled syncs are paused; credentials stay stored.',
                )
              }
              disabled={busy}
              icon={<PauseCircle size={13} aria-hidden />}
            >
              Disable
            </ActionButton>
          )}
          <ActionButton
            onClick={() => {
              if (
                window.confirm(
                  `Revoke the connection "${connection.displayName}"? ` +
                    'Stored credentials are cryptographically wiped and scheduled syncs stop. ' +
                    'Data already synced remains valid.',
                )
              ) {
                void run(() => revoke.mutateAsync(connection.id));
              }
            }}
            disabled={busy}
            tone="danger"
            icon={<ShieldAlert size={13} aria-hidden />}
          >
            Revoke
          </ActionButton>
        </div>
      )}

      {testResult && (
        <div
          className={`rounded border px-4 py-3 space-y-2 ${
            testResult.reachable
              ? 'border-success/30 bg-success-light/50'
              : 'border-critical/30 bg-critical-light/40'
          }`}
        >
          <div className="flex items-center gap-2">
            {testResult.reachable ? (
              <CheckCircle2 size={15} className="text-success" aria-hidden />
            ) : (
              <XCircle size={15} className="text-critical" aria-hidden />
            )}
            <p className="text-body font-medium text-navy">
              {testResult.reachable ? 'Reachable' : 'Not reachable'}
            </p>
            <button
              type="button"
              onClick={() => setTestResult(null)}
              className="ml-auto text-caption text-slate hover:text-navy"
            >
              Dismiss
            </button>
          </div>
          {testResult.reachable ? (
            <ul className="space-y-0.5 text-body text-navy font-mono">
              {testResult.latencyMs != null && (
                <li>latency: {Number(testResult.latencyMs).toLocaleString(fmtLocale())} ms</li>
              )}
              {testResult.tablesPulled != null && (
                <li>tables reached: {testResult.tablesPulled}</li>
              )}
              {testResult.rowsPulled != null && (
                <li>sample rows: {testResult.rowsPulled}</li>
              )}
            </ul>
          ) : (
            <>
              <p className="text-body text-navy">
                {testResult.error ?? 'The connection could not be established.'}
              </p>
              {testResult.errorCode && (
                <p className="text-caption font-mono text-slate">{testResult.errorCode}</p>
              )}
            </>
          )}
        </div>
      )}

      {panel === 'schema' && schema && <SchemaPanel result={schema} />}

      {panel === 'sync' && (
        <SyncPanel bankId={bankId} connectionId={connection.id} disabled={isDisabled} />
      )}

      {panel === 'edit' && (
        <div className="rounded border border-border p-4 space-y-4 bg-surface-alt">
          <ConnectionForm
            form={editForm}
            onChange={(patch) => setEditForm((current) => ({ ...current, ...patch }))}
            idPrefix={`edit-${connection.id}`}
            credentialsMode="rotate"
            lockBackend
          />
          <button
            type="button"
            onClick={() => void submitEdit()}
            disabled={busy || !editForm.displayName.trim()}
            className="inline-flex items-center gap-2 px-4 py-2 rounded text-body font-medium bg-action text-white hover:bg-action-hover disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {update.isPending ? (
              <Loader2 size={15} className="animate-spin" aria-hidden />
            ) : (
              <CheckCircle2 size={15} aria-hidden />
            )}
            Save changes
          </button>
        </div>
      )}

      {actionError && (
        <div className="rounded border border-critical/30 bg-critical-light/40 px-4 py-3">
          <p className="text-body text-critical">{actionError}</p>
        </div>
      )}
      {notice && !actionError && <p className="text-caption text-slate">{notice}</p>}
    </section>
  );
}
