'use client';

/**
 * "Connect a database" — the onboarding form: backend + endpoint + schemas +
 * TLS posture + routing + write-only credentials, submitted once. On success
 * the connection is created (credentials validated server-side) and immediately
 * tested for reachability. Credentials are never displayed again — only the
 * fingerprint on the connection card.
 */

import { useState } from 'react';
import { CheckCircle2, Loader2, Plug, XCircle } from 'lucide-react';
import type {
  DatabaseConnectionRead,
  DatabaseConnectionTestResult,
} from '@aequoros/risk-service-api';
import { isApiError } from '@/lib/api/client';
import {
  useCreateDatabaseConnection,
  useTestDatabaseConnection,
} from '@/lib/api/database-direct';
import ConnectionForm, {
  type DbFormState,
  buildCreatePayload,
  emptyFormState,
} from './ConnectionForm';
import { ConnectionStatusPill, extraIsValid } from './shared';
import { fmtLocale } from '@/lib/format';

export default function AddConnectionPanel({
  bankId,
  existingNames,
  onDone,
}: {
  bankId: string;
  existingNames: string[];
  onDone: () => void;
}) {
  const create = useCreateDatabaseConnection(bankId);
  const test = useTestDatabaseConnection(bankId);

  const [form, setForm] = useState<DbFormState>(() => emptyFormState('oracle'));
  const [created, setCreated] = useState<DatabaseConnectionRead | null>(null);
  const [testResult, setTestResult] = useState<DatabaseConnectionTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  const isSnowflake = form.backend === 'snowflake';
  const nameTaken =
    form.displayName.trim().length > 0 &&
    existingNames.includes(form.displayName.trim());
  // Snowflake uses key-pair auth (service user + private key), not a password.
  const credentialsIncomplete = isSnowflake
    ? !form.cred.username?.trim() || !form.cred.snowflake_private_key?.trim()
    : !form.cred.username?.trim() || !form.cred.password?.trim();
  // Snowflake connects by account + warehouse (both required) instead of host.
  const snowflakeConfigIncomplete =
    isSnowflake &&
    (!form.snowflake.account.trim() || !form.snowflake.warehouse.trim());
  const extraInvalid = !extraIsValid(form.cred.extra ?? '');
  const canSubmit =
    Boolean(form.displayName.trim()) &&
    !nameTaken &&
    !credentialsIncomplete &&
    !snowflakeConfigIncomplete &&
    !extraInvalid &&
    !running;

  const createAndTest = async () => {
    setRunning(true);
    setError(null);
    try {
      const connection = await create.mutateAsync(buildCreatePayload(form));
      setCreated(connection);
      if (!connection.validationError && connection.status !== 'TESTING') {
        setTestResult(await test.mutateAsync(connection.id));
      }
    } catch (caught) {
      setError(
        isApiError(caught)
          ? caught.message
          : caught instanceof Error
            ? caught.message
            : 'Could not create the connection.',
      );
    } finally {
      setRunning(false);
    }
  };

  return (
    <section className="card p-5 space-y-5">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h2 className="text-h2 text-navy">Connect a database</h2>
          <p className="mt-1 text-body text-slate">
            A read-only connection to a bank-hosted reporting replica. AequorOS connects
            from the deployment, never the browser. Credentials are encrypted at rest and
            never displayed again.
          </p>
        </div>
        <button
          type="button"
          onClick={onDone}
          className="shrink-0 text-caption font-medium text-slate hover:text-navy"
        >
          Close
        </button>
      </div>

      {!created ? (
        <>
          <ConnectionForm
            form={form}
            onChange={(patch) => setForm((current) => ({ ...current, ...patch }))}
            idPrefix="add-db"
            credentialsMode="required"
          />

          {nameTaken && (
            <p className="text-caption text-critical">
              A connection with this name already exists for this bank.
            </p>
          )}

          <div className="flex items-center gap-3 pt-1">
            <button
              type="button"
              onClick={() => void createAndTest()}
              disabled={!canSubmit}
              className="inline-flex items-center gap-2 px-4 py-2 rounded text-body font-medium bg-action text-white hover:bg-action-hover disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {running ? (
                <Loader2 size={15} className="animate-spin" aria-hidden />
              ) : (
                <Plug size={15} aria-hidden />
              )}
              Create, validate &amp; test connection
            </button>
            {(credentialsIncomplete || snowflakeConfigIncomplete) && (
              <span className="text-caption text-slate">
                {isSnowflake
                  ? 'A service user, private key, account, and warehouse are required to create the connection.'
                  : 'A service user and password are required to create the connection.'}
              </span>
            )}
          </div>
        </>
      ) : (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <ConnectionStatusPill status={created.status} />
            <p className="text-body text-navy">
              {created.status === 'TESTING'
                ? 'Connection stored, but credential validation failed — fix it from the connection card (Edit).'
                : `${created.displayName} created.`}
            </p>
          </div>

          {created.validationError && (
            <div className="rounded border border-warning/30 bg-warning-light/50 px-4 py-3">
              <p className="text-body text-navy">{created.validationError}</p>
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
              </div>
              {testResult.reachable ? (
                <ul className="space-y-0.5 text-body text-navy font-mono">
                  {testResult.latencyMs != null && (
                    <li>
                      latency: {Number(testResult.latencyMs).toLocaleString(fmtLocale())} ms
                    </li>
                  )}
                  {testResult.tablesPulled != null && (
                    <li>tables reached: {testResult.tablesPulled}</li>
                  )}
                </ul>
              ) : (
                <>
                  <p className="text-body text-navy">
                    {testResult.error ?? 'The connection could not be established.'}
                  </p>
                  {testResult.errorCode && (
                    <p className="text-caption font-mono text-slate">
                      {testResult.errorCode}
                    </p>
                  )}
                </>
              )}
            </div>
          )}

          <button
            type="button"
            onClick={onDone}
            className="inline-flex items-center gap-2 px-4 py-2 rounded text-body font-medium bg-action text-white hover:bg-action-hover"
          >
            Done
          </button>
        </div>
      )}

      {error && (
        <div className="rounded border border-critical/30 bg-critical-light/40 px-4 py-3">
          <p className="text-body text-critical">{error}</p>
        </div>
      )}
    </section>
  );
}
