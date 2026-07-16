'use client';

/**
 * Active market data source cards (§9.3): one card per configured connection
 * with the §10.2 status chip, last pull time, this-month quota consumption,
 * scope count, and the per-connection lifecycle actions — Test, Validate,
 * Rotate credentials (§10.4), Disable/Enable, and Revoke (§10.5, with
 * confirm). Rotation shows a fresh credential form; stored values are never
 * displayed — only the fingerprint identifies what is on file.
 */

import { useState } from 'react';
import {
  CheckCircle2,
  KeyRound,
  Loader2,
  PauseCircle,
  PlayCircle,
  ShieldAlert,
  XCircle,
  Zap,
} from 'lucide-react';
import type {
  MarketDataConnectionRead,
  QuotaSummaryRead,
  TestPullRead,
} from '@aequoros/risk-service-api';
import { isApiError } from '@/lib/api/client';
import {
  useDisableMarketDataConnection,
  useEnableMarketDataConnection,
  useRevokeMarketDataConnection,
  useTestMarketDataConnection,
  useUpdateMarketDataConnection,
  useValidateMarketDataConnection,
} from '@/lib/api/hooks';
import CredentialFields from './CredentialFields';
import {
  ConnectionStatusPill,
  fmtWhen,
  vendorName,
  type VendorKey,
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

export default function SourceCard({
  bankId,
  connection,
  quota,
}: {
  bankId: string;
  connection: MarketDataConnectionRead;
  quota: QuotaSummaryRead | undefined;
}) {
  const validate = useValidateMarketDataConnection(bankId);
  const test = useTestMarketDataConnection(bankId);
  const update = useUpdateMarketDataConnection(bankId);
  const disable = useDisableMarketDataConnection(bankId);
  const enable = useEnableMarketDataConnection(bankId);
  const revoke = useRevokeMarketDataConnection(bankId);

  const [testResult, setTestResult] = useState<TestPullRead | null>(null);
  const [rotating, setRotating] = useState(false);
  const [rotateValues, setRotateValues] = useState<Record<string, string>>({});
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const isVendor = connection.vendor !== 'manual_upload';
  const isRevoked = connection.status === 'REVOKED';
  const isDisabled = connection.status === 'DISABLED';
  const busy =
    validate.isPending ||
    test.isPending ||
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

  const submitRotation = () =>
    run(async () => {
      await update.mutateAsync({
        connectionId: connection.id,
        payload: { credentials: rotateValues },
      });
      setRotating(false);
      setRotateValues({});
    }, 'Credentials rotated. The stored set was swapped atomically after vendor validation.');

  return (
    <section className="card p-5 space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="text-h3 text-navy">{connection.displayName}</h3>
        <span className="text-caption text-slate">
          {vendorName(connection.vendor)}
        </span>
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
        <div>
          <dt className="text-caption text-slate">This month</dt>
          <dd className="font-mono text-navy">
            {quota
              ? `${quota.unitsConsumed.toLocaleString('en-GH')} units · ${quota.pullCount} pulls`
              : '—'}
            {quota?.monthlyCap != null && (
              <span className="text-slate"> / cap {quota.monthlyCap.toLocaleString('en-GH')}</span>
            )}
          </dd>
        </div>
        <div>
          <dt className="text-caption text-slate">Scopes</dt>
          <dd className="font-mono text-navy">{connection.scopes.length}</dd>
        </div>
        <div>
          <dt className="text-caption text-slate">
            {isVendor ? 'Credential fingerprint' : 'Credentials'}
          </dt>
          <dd
            className="font-mono text-navy truncate"
            title={connection.credentialFingerprint ?? undefined}
          >
            {isVendor
              ? connection.credentialFingerprint
                ? `${connection.credentialFingerprint.slice(0, 12)}…`
                : 'none stored'
              : 'not required'}
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
          {isVendor && (
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
          )}
          {isVendor && (
            <ActionButton
              onClick={() =>
                run(
                  () => validate.mutateAsync(connection.id),
                  'Credential health check completed.'
                )
              }
              disabled={busy || isDisabled}
              icon={<CheckCircle2 size={13} aria-hidden />}
            >
              Validate
            </ActionButton>
          )}
          {isVendor && (
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
          )}
          {isDisabled ? (
            <ActionButton
              onClick={() =>
                run(
                  () => enable.mutateAsync(connection.id),
                  'Connection re-validated and enabled.'
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
                  `Revoke the ${vendorName(connection.vendor)} connection "${connection.displayName}"? ` +
                    'Stored credentials are cryptographically wiped and scheduled pulls stop. ' +
                    'Historical data already pulled remains valid.'
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

      {rotating && isVendor && !isRevoked && (
        <div className="rounded border border-border p-4 space-y-4 bg-surface-alt">
          <p className="text-body text-slate">
            Enter the new credentials generated at {vendorName(connection.vendor)}. They are
            validated against the vendor first; only on success is the stored set swapped —
            on failure nothing changes.
          </p>
          <CredentialFields
            vendor={connection.vendor as Exclude<VendorKey, 'manual_upload'>}
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
            Validate & swap credentials
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
              {testResult.success ? 'Test pull succeeded' : 'Test pull failed'}
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
      {notice && !actionError && (
        <p className="text-caption text-slate">{notice}</p>
      )}
    </section>
  );
}
