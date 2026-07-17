'use client';

/**
 * One configured Temenos connection: the credential-lifecycle status chip,
 * last pull, endpoint, enabled-domain count, and fingerprint, plus the
 * per-connection actions — Test, Validate, Rotate credentials, Disable/Enable,
 * Revoke — and the T24 pull controls (Pull now / Backfill). Stored credentials
 * are never displayed; only the fingerprint identifies what is on file.
 */

import { useState } from 'react';
import {
  CalendarClock,
  CheckCircle2,
  DownloadCloud,
  KeyRound,
  Loader2,
  PauseCircle,
  PlayCircle,
  ShieldAlert,
  XCircle,
  Zap,
} from 'lucide-react';
import type {
  TemenosConnectionRead,
  TemenosTestPullRead,
} from '@aequoros/risk-service-api';
import { isApiError } from '@/lib/api/client';
import {
  useDisableTemenosConnection,
  useEnableTemenosConnection,
  useRevokeTemenosConnection,
  useTestTemenosConnection,
  useTriggerTemenosBackfill,
  useTriggerTemenosPull,
  useUpdateTemenosConnection,
  useValidateTemenosConnection,
} from '@/lib/api/hooks';
import CredentialFields from './CredentialFields';
import {
  ConnectionStatusPill,
  fmtWhen,
  modeName,
  type ModeKey,
} from './shared';

function errorMessage(error: unknown): string {
  if (isApiError(error)) return error.message;
  if (error instanceof Error) return error.message;
  return 'The request failed.';
}

function ActionButton({
  onClick,
  disabled,
  icon,
  children,
  tone = 'default',
}: {
  onClick: () => void;
  disabled?: boolean;
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
          : 'border-border text-navy hover:bg-surface'
      }`}
    >
      {icon}
      {children}
    </button>
  );
}

export default function ConnectionCard({
  bankId,
  connection,
}: {
  bankId: string;
  connection: TemenosConnectionRead;
}) {
  const validate = useValidateTemenosConnection(bankId);
  const test = useTestTemenosConnection(bankId);
  const update = useUpdateTemenosConnection(bankId);
  const disable = useDisableTemenosConnection(bankId);
  const enable = useEnableTemenosConnection(bankId);
  const revoke = useRevokeTemenosConnection(bankId);
  const pull = useTriggerTemenosPull(bankId);
  const backfill = useTriggerTemenosBackfill(bankId);

  const [testResult, setTestResult] = useState<TemenosTestPullRead | null>(null);
  const [rotating, setRotating] = useState(false);
  const [rotateValues, setRotateValues] = useState<Record<string, string>>({});
  const [showPull, setShowPull] = useState(false);
  const [pullDate, setPullDate] = useState('');
  const [backfillStart, setBackfillStart] = useState('');
  const [backfillEnd, setBackfillEnd] = useState('');
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const isRevoked = connection.status === 'REVOKED';
  const isDisabled = connection.status === 'DISABLED';
  const busy =
    validate.isPending ||
    test.isPending ||
    update.isPending ||
    disable.isPending ||
    enable.isPending ||
    revoke.isPending ||
    pull.isPending ||
    backfill.isPending;

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

  const submitRotation = () =>
    run(async () => {
      await update.mutateAsync({
        connectionId: connection.id,
        payload: { credentials: rotateValues },
      });
      setRotating(false);
      setRotateValues({});
    }, 'Credentials rotated. The stored set was swapped atomically after validation.');

  const triggerPull = () =>
    run(async () => {
      const result = await pull.mutateAsync({
        connectionId: connection.id,
        asOfDate: pullDate || undefined,
      });
      setShowPull(false);
      return result;
    }, `Pull enqueued for ${pullDate || 'today'}. The worker stages the bundle and ingests it.`);

  const triggerBackfill = () =>
    run(async () => {
      const result = await backfill.mutateAsync({
        connectionId: connection.id,
        payload: {
          startDate: new Date(`${backfillStart}T00:00:00Z`),
          endDate: new Date(`${backfillEnd}T00:00:00Z`),
        },
      });
      setShowPull(false);
      setNotice(`Backfill enqueued: ${result.count} pull job${result.count === 1 ? '' : 's'}.`);
    });

  return (
    <section className="card p-5 space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="text-h3 text-navy">{connection.displayName}</h3>
        <span className="text-caption text-slate">{modeName(connection.connectionMode)}</span>
        <ConnectionStatusPill status={connection.status} />
      </div>

      <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2 lg:grid-cols-4 text-body">
        <div>
          <dt className="text-caption text-slate">Last pull</dt>
          <dd className="font-mono text-navy">
            {fmtWhen(connection.lastPullAt)}
            {connection.lastPullStatus && (
              <span
                className={
                  connection.lastPullStatus === 'succeeded'
                    ? 'text-success ml-1.5'
                    : 'text-critical ml-1.5'
                }
              >
                ({connection.lastPullStatus})
              </span>
            )}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-caption text-slate">Endpoint</dt>
          <dd className="font-mono text-navy truncate" title={connection.endpoint}>
            {connection.endpoint}
          </dd>
        </div>
        <div>
          <dt className="text-caption text-slate">Enabled domains</dt>
          <dd className="font-mono text-navy">
            {connection.domains.length > 0 ? connection.domains.length : 'all supported'}
          </dd>
        </div>
        <div>
          <dt className="text-caption text-slate">Credential fingerprint</dt>
          <dd
            className="font-mono text-navy truncate"
            title={connection.credentialFingerprint ?? undefined}
          >
            {connection.credentialFingerprint
              ? `${connection.credentialFingerprint.slice(0, 12)}…`
              : 'none stored'}
          </dd>
        </div>
      </dl>

      {connection.credentialExpiresAt && (
        <p className="text-caption text-slate">
          Credential expires{' '}
          <span className="font-mono">{fmtWhen(connection.credentialExpiresAt)}</span> · last
          validated <span className="font-mono">{fmtWhen(connection.lastValidatedAt)}</span>
        </p>
      )}

      {connection.validationError && (
        <div className="rounded border border-warning/30 bg-warning-light/50 px-4 py-3">
          <p className="text-body text-navy">{connection.validationError}</p>
        </div>
      )}

      {!isRevoked && (
        <div className="flex flex-wrap gap-2">
          <ActionButton
            onClick={() => {
              setShowPull((current) => !current);
              setActionError(null);
              setNotice(null);
            }}
            disabled={busy || isDisabled}
            icon={<DownloadCloud size={13} aria-hidden />}
          >
            {showPull ? 'Cancel pull' : 'Pull now'}
          </ActionButton>
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
            onClick={() =>
              run(() => validate.mutateAsync(connection.id), 'Credential health check completed.')
            }
            disabled={busy || isDisabled}
            icon={<CheckCircle2 size={13} aria-hidden />}
          >
            Validate
          </ActionButton>
          <ActionButton
            onClick={() => {
              setRotating((current) => !current);
              setActionError(null);
              setNotice(null);
            }}
            disabled={busy}
            icon={<KeyRound size={13} aria-hidden />}
          >
            {rotating ? 'Cancel rotation' : 'Rotate credentials'}
          </ActionButton>
          {isDisabled ? (
            <ActionButton
              onClick={() =>
                run(() => enable.mutateAsync(connection.id), 'Connection re-validated and enabled.')
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
                  'Connection disabled. Scheduled pulls are paused; credentials stay stored.'
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
                    'Stored credentials are cryptographically wiped and scheduled pulls stop. ' +
                    'Data already pulled remains valid.'
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

      {showPull && !isRevoked && (
        <div className="rounded border border-border p-4 space-y-4 bg-surface-alt">
          <div className="flex flex-wrap items-end gap-3">
            <div>
              <label
                htmlFor={`pull-date-${connection.id}`}
                className="block text-caption font-medium text-slate mb-1"
              >
                Pull one date
              </label>
              <input
                id={`pull-date-${connection.id}`}
                type="date"
                value={pullDate}
                onChange={(event) => setPullDate(event.target.value)}
                className="px-3 py-1.5 rounded border border-border text-body text-navy font-mono"
              />
            </div>
            <button
              type="button"
              onClick={() => void triggerPull()}
              disabled={busy}
              className="inline-flex items-center gap-1.5 px-3 py-2 rounded text-caption font-medium bg-action text-white hover:bg-action-hover disabled:opacity-40"
            >
              {pull.isPending ? (
                <Loader2 size={13} className="animate-spin" aria-hidden />
              ) : (
                <DownloadCloud size={13} aria-hidden />
              )}
              Pull {pullDate || 'today'}
            </button>
          </div>
          <div className="border-t border-border pt-4">
            <p className="flex items-center gap-1.5 text-caption font-medium text-slate mb-2">
              <CalendarClock size={13} aria-hidden /> Historical backfill
            </p>
            <div className="flex flex-wrap items-end gap-3">
              <div>
                <label
                  htmlFor={`bf-start-${connection.id}`}
                  className="block text-caption text-slate mb-1"
                >
                  From
                </label>
                <input
                  id={`bf-start-${connection.id}`}
                  type="date"
                  value={backfillStart}
                  onChange={(event) => setBackfillStart(event.target.value)}
                  className="px-3 py-1.5 rounded border border-border text-body text-navy font-mono"
                />
              </div>
              <div>
                <label
                  htmlFor={`bf-end-${connection.id}`}
                  className="block text-caption text-slate mb-1"
                >
                  To
                </label>
                <input
                  id={`bf-end-${connection.id}`}
                  type="date"
                  value={backfillEnd}
                  onChange={(event) => setBackfillEnd(event.target.value)}
                  className="px-3 py-1.5 rounded border border-border text-body text-navy font-mono"
                />
              </div>
              <button
                type="button"
                onClick={() => void triggerBackfill()}
                disabled={busy || !backfillStart || !backfillEnd}
                className="inline-flex items-center gap-1.5 px-3 py-2 rounded border border-border text-caption font-medium text-navy hover:bg-surface disabled:opacity-40"
              >
                {backfill.isPending ? (
                  <Loader2 size={13} className="animate-spin" aria-hidden />
                ) : (
                  <CalendarClock size={13} aria-hidden />
                )}
                Enqueue backfill
              </button>
            </div>
            <p className="mt-2 text-caption text-slate">
              One pull job per day in the range; re-ingesting a date supersedes rather than
              duplicates.
            </p>
          </div>
        </div>
      )}

      {rotating && !isRevoked && (
        <div className="rounded border border-border p-4 space-y-4 bg-surface-alt">
          <p className="text-body text-slate">
            Enter the new service credentials. They are validated first; only on success is
            the stored set swapped — on failure nothing changes.
          </p>
          <CredentialFields
            mode={connection.connectionMode as ModeKey}
            values={rotateValues}
            onChange={(key, value) =>
              setRotateValues((current) => ({ ...current, [key]: value }))
            }
            idPrefix={`rotate-${connection.id}`}
          />
          <button
            type="button"
            onClick={() => void submitRotation()}
            disabled={busy || Object.values(rotateValues).every((value) => !value.trim())}
            className="inline-flex items-center gap-2 px-4 py-2 rounded text-body font-medium bg-action text-white hover:bg-action-hover disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {update.isPending ? (
              <Loader2 size={15} className="animate-spin" aria-hidden />
            ) : (
              <KeyRound size={15} aria-hidden />
            )}
            Validate &amp; swap credentials
          </button>
        </div>
      )}

      {testResult && (
        <div
          className={`rounded border px-4 py-3 space-y-2 ${
            testResult.success
              ? 'border-success/30 bg-success-light/50'
              : 'border-critical/30 bg-critical-light/40'
          }`}
        >
          <div className="flex items-center gap-2">
            {testResult.success ? (
              <CheckCircle2 size={15} className="text-success" aria-hidden />
            ) : (
              <XCircle size={15} className="text-critical" aria-hidden />
            )}
            <p className="text-body font-medium text-navy">
              {testResult.success ? 'Connection verified — pull plan' : 'Verification failed'}
            </p>
            <button
              type="button"
              onClick={() => setTestResult(null)}
              className="ml-auto text-caption text-slate hover:text-navy"
            >
              Dismiss
            </button>
          </div>
          {testResult.success ? (
            <ul className="space-y-1">
              {Object.entries(testResult.sampleValues).map(([label, value]) => (
                <li key={label} className="text-body text-navy font-mono">
                  {label}: {value}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-body text-navy">{testResult.error}</p>
          )}
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
