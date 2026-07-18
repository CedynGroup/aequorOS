'use client';

/**
 * The direct-database connection form: backend, endpoint (host/port/database/
 * service name), schemas, TLS posture, read-replica routing, query timeout, and
 * write-only credentials. Shared by the create panel and the per-connection
 * edit flow — the fields are identical; only the submit semantics differ
 * (create requires credentials; edit rotates them optionally).
 */

import type {
  DatabaseConnectionCreate,
  DatabaseConnectionRead,
  DatabaseConnectionUpdate,
} from '@aequoros/risk-service-api';
import CredentialFields from './CredentialFields';
import OracleWalletFields from './OracleWalletFields';
import {
  BACKENDS,
  type BackendKey,
  backendMeta,
  parseExtra,
  splitList,
} from './shared';

export type DbFormState = {
  backend: BackendKey;
  displayName: string;
  host: string;
  port: string;
  database: string;
  serviceName: string;
  schemas: string;
  tlsEnabled: boolean;
  tlsVerifyServerCertificate: boolean;
  preferReadReplica: boolean;
  readReplicas: string;
  queryTimeoutSeconds: string;
  cred: Record<string, string>;
};

export function emptyFormState(backend: BackendKey = 'oracle'): DbFormState {
  const meta = backendMeta(backend);
  return {
    backend,
    displayName: '',
    host: '',
    port: meta && meta.defaultPort > 0 ? String(meta.defaultPort) : '',
    database: '',
    serviceName: '',
    schemas: '',
    tlsEnabled: true,
    tlsVerifyServerCertificate: true,
    preferReadReplica: false,
    readReplicas: '',
    queryTimeoutSeconds: '',
    cred: {},
  };
}

/** Seed the edit form from a connection's bank-facing view. Credentials are
 * never returned, so the credential fields always start empty (rotation only). */
export function formStateFromConnection(
  connection: DatabaseConnectionRead,
): DbFormState {
  return {
    backend: connection.backend,
    displayName: connection.displayName,
    host: connection.host ?? '',
    port: connection.port != null ? String(connection.port) : '',
    database: connection.database ?? '',
    serviceName: connection.serviceName ?? '',
    schemas: (connection.schemas ?? []).join(', '),
    tlsEnabled: connection.tlsEnabled,
    tlsVerifyServerCertificate: connection.tlsVerifyServerCertificate,
    preferReadReplica: connection.preferReadReplica,
    readReplicas: (connection.readReplicas ?? []).join(', '),
    queryTimeoutSeconds:
      connection.queryTimeoutSeconds != null
        ? String(connection.queryTimeoutSeconds)
        : '',
    cred: {},
  };
}

function parsePort(value: string): number | undefined {
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function parseTimeout(value: string): number | undefined {
  return parsePort(value);
}

/** Collect the write-only credential object, dropping empty fields. `extra` is
 * parsed from its JSON string; for Oracle the friendly wallet control writes
 * `oracle_wallet` + `wallet_password` into that SAME `extra` object. Returns
 * undefined when nothing was entered. */
export function collectCredentials(
  cred: Record<string, string>,
  backend?: BackendKey,
): Record<string, unknown> | undefined {
  const out: Record<string, unknown> = {};
  const username = cred.username?.trim();
  const password = cred.password?.trim();
  if (username) out.username = username;
  if (password) out.password = password;
  // Start from the power-user "extra JSON" escape hatch, then let the friendly
  // Oracle wallet control merge its mTLS material into the same object.
  const extra: Record<string, unknown> = { ...(parseExtra(cred.extra ?? '') ?? {}) };
  if (backend === 'oracle') {
    const wallet = cred.oracle_wallet?.trim();
    const walletPassword = cred.wallet_password?.trim();
    if (wallet) extra.oracle_wallet = wallet;
    if (walletPassword) extra.wallet_password = walletPassword;
  }
  if (Object.keys(extra).length > 0) out.extra = extra;
  return Object.keys(out).length > 0 ? out : undefined;
}

export function buildCreatePayload(form: DbFormState): DatabaseConnectionCreate {
  return {
    backend: form.backend,
    displayName: form.displayName.trim(),
    host: form.host.trim() || undefined,
    port: parsePort(form.port),
    database: form.database.trim() || undefined,
    serviceName: form.serviceName.trim() || undefined,
    schemas: splitList(form.schemas),
    tlsEnabled: form.tlsEnabled,
    tlsVerifyServerCertificate: form.tlsVerifyServerCertificate,
    preferReadReplica: form.preferReadReplica,
    readReplicas: splitList(form.readReplicas),
    queryTimeoutSeconds: parseTimeout(form.queryTimeoutSeconds),
    credentials: collectCredentials(form.cred, form.backend),
  };
}

export function buildUpdatePayload(form: DbFormState): DatabaseConnectionUpdate {
  return {
    displayName: form.displayName.trim(),
    host: form.host.trim() || null,
    port: parsePort(form.port) ?? null,
    database: form.database.trim() || null,
    serviceName: form.serviceName.trim() || null,
    schemas: splitList(form.schemas),
    tlsEnabled: form.tlsEnabled,
    tlsVerifyServerCertificate: form.tlsVerifyServerCertificate,
    preferReadReplica: form.preferReadReplica,
    readReplicas: splitList(form.readReplicas),
    queryTimeoutSeconds: parseTimeout(form.queryTimeoutSeconds) ?? null,
    // Only send credentials when the operator entered a new set (rotation).
    credentials: collectCredentials(form.cred, form.backend),
  };
}

const inputClass =
  'w-full px-3 py-1.5 rounded border border-border text-body text-navy font-mono';
const labelClass = 'block text-caption font-medium text-slate mb-1';

export default function ConnectionForm({
  form,
  onChange,
  idPrefix,
  credentialsMode = 'required',
  lockBackend = false,
}: {
  form: DbFormState;
  onChange: (patch: Partial<DbFormState>) => void;
  idPrefix: string;
  /** create: a first credential set is required; rotate: leaving them blank
   * keeps the stored set. */
  credentialsMode?: 'required' | 'rotate';
  /** The backend cannot change after creation. */
  lockBackend?: boolean;
}) {
  const meta = backendMeta(form.backend);

  const setCred = (key: string, value: string) =>
    onChange({ cred: { ...form.cred, [key]: value } });

  const chooseBackend = (backend: BackendKey) => {
    const next = backendMeta(backend);
    // Adopt the new backend's default port when the field is empty or still
    // holds the previous backend's default.
    const prevDefault = meta && meta.defaultPort > 0 ? String(meta.defaultPort) : '';
    const port =
      !form.port.trim() || form.port === prevDefault
        ? next && next.defaultPort > 0
          ? String(next.defaultPort)
          : ''
        : form.port;
    onChange({ backend, port });
  };

  return (
    <div className="space-y-5">
      {!lockBackend && (
        <fieldset className="space-y-2">
          <legend className={labelClass}>Backend</legend>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {BACKENDS.map((backend) => {
              const active = form.backend === backend.key;
              return (
                <button
                  key={backend.key}
                  type="button"
                  onClick={() => chooseBackend(backend.key)}
                  className={`text-left rounded border p-3 space-y-1 ${
                    active
                      ? 'border-action bg-action-light/60'
                      : 'border-border hover:border-action/50'
                  }`}
                >
                  <p className="text-body font-medium text-navy">{backend.name}</p>
                  <p className="text-caption text-slate leading-snug">{backend.blurb}</p>
                </button>
              );
            })}
          </div>
        </fieldset>
      )}

      <div className="grid gap-4 sm:grid-cols-2 max-w-3xl">
        <div>
          <label htmlFor={`${idPrefix}-name`} className={labelClass}>
            Display name
          </label>
          <input
            id={`${idPrefix}-name`}
            type="text"
            value={form.displayName}
            onChange={(event) => onChange({ displayName: event.target.value })}
            className="w-full px-3 py-1.5 rounded border border-border text-body text-navy"
          />
        </div>
        <div>
          <label htmlFor={`${idPrefix}-query-timeout`} className={labelClass}>
            Query timeout (seconds)
          </label>
          <input
            id={`${idPrefix}-query-timeout`}
            type="number"
            min={1}
            value={form.queryTimeoutSeconds}
            onChange={(event) => onChange({ queryTimeoutSeconds: event.target.value })}
            placeholder="default"
            className="w-40 px-3 py-1.5 rounded border border-border text-body text-navy font-mono"
          />
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 max-w-3xl">
        <div className="lg:col-span-2">
          <label htmlFor={`${idPrefix}-host`} className={labelClass}>
            Host
          </label>
          <input
            id={`${idPrefix}-host`}
            type="text"
            value={form.host}
            onChange={(event) => onChange({ host: event.target.value })}
            placeholder="reporting-replica.bank.internal"
            className={inputClass}
          />
        </div>
        <div>
          <label htmlFor={`${idPrefix}-port`} className={labelClass}>
            Port
          </label>
          <input
            id={`${idPrefix}-port`}
            type="number"
            min={0}
            value={form.port}
            onChange={(event) => onChange({ port: event.target.value })}
            className={inputClass}
          />
        </div>
        <div>
          <label htmlFor={`${idPrefix}-database`} className={labelClass}>
            {meta?.databaseLabel ?? 'Database'}
          </label>
          <input
            id={`${idPrefix}-database`}
            type="text"
            value={form.database}
            onChange={(event) => onChange({ database: event.target.value })}
            className={inputClass}
          />
          {meta?.databaseHint && (
            <p className="mt-1 text-caption text-slate">{meta.databaseHint}</p>
          )}
        </div>
        {meta?.usesServiceName && (
          <div>
            <label htmlFor={`${idPrefix}-service`} className={labelClass}>
              Service name
            </label>
            <input
              id={`${idPrefix}-service`}
              type="text"
              value={form.serviceName}
              onChange={(event) => onChange({ serviceName: event.target.value })}
              placeholder="CORE.bank.internal"
              className={inputClass}
            />
          </div>
        )}
      </div>

      <div className="max-w-3xl">
        <label htmlFor={`${idPrefix}-schemas`} className={labelClass}>
          Schemas
        </label>
        <input
          id={`${idPrefix}-schemas`}
          type="text"
          value={form.schemas}
          onChange={(event) => onChange({ schemas: event.target.value })}
          placeholder="REPORTING, FINANCE"
          className={inputClass}
        />
        <p className="mt-1 text-caption text-slate">
          Comma-separated schemas the extraction is scoped to. Leave blank to use the
          service user&apos;s default schema.
        </p>
      </div>

      <fieldset className="rounded border border-border p-4 space-y-3 max-w-3xl">
        <legend className="px-1 text-caption font-medium uppercase tracking-wider text-slate">
          Transport &amp; routing
        </legend>
        <label className="flex items-start gap-2 text-body text-navy">
          <input
            type="checkbox"
            checked={form.tlsEnabled}
            onChange={(event) => onChange({ tlsEnabled: event.target.checked })}
            className="mt-1 rounded border-border"
          />
          <span>
            TLS enabled
            <span className="block text-caption text-slate">
              Disable only for an endpoint that terminates TLS out of band.
            </span>
          </span>
        </label>
        <label className="flex items-start gap-2 text-body text-navy">
          <input
            type="checkbox"
            checked={form.tlsVerifyServerCertificate}
            disabled={!form.tlsEnabled}
            onChange={(event) =>
              onChange({ tlsVerifyServerCertificate: event.target.checked })
            }
            className="mt-1 rounded border-border disabled:opacity-40"
          />
          <span>
            Verify server certificate
            <span className="block text-caption text-slate">
              Reject a certificate that does not chain to a trusted CA.
            </span>
          </span>
        </label>
        <label className="flex items-start gap-2 text-body text-navy">
          <input
            type="checkbox"
            checked={form.preferReadReplica}
            onChange={(event) => onChange({ preferReadReplica: event.target.checked })}
            className="mt-1 rounded border-border"
          />
          <span>
            Prefer a read replica
            <span className="block text-caption text-slate">
              Route extraction to a replica when one is reachable, sparing the primary.
            </span>
          </span>
        </label>
        <div>
          <label htmlFor={`${idPrefix}-replicas`} className={labelClass}>
            Read replicas
          </label>
          <input
            id={`${idPrefix}-replicas`}
            type="text"
            value={form.readReplicas}
            onChange={(event) => onChange({ readReplicas: event.target.value })}
            placeholder="replica-1.bank.internal, replica-2.bank.internal"
            className={inputClass}
          />
          <p className="mt-1 text-caption text-slate">
            Comma-separated replica hosts, tried in order.
          </p>
        </div>
      </fieldset>

      <div className="space-y-3 max-w-3xl">
        <p className="text-body text-slate">
          {credentialsMode === 'required'
            ? 'Credentials for the read-only service user. Validated on submission and stored encrypted; only the fingerprint is shown afterwards.'
            : 'Enter a new credential set to rotate it. Leave blank to keep the stored set. A new set is validated first; only on success is it swapped.'}
        </p>
        <CredentialFields values={form.cred} onChange={setCred} idPrefix={`${idPrefix}-cred`} />
        {form.backend === 'oracle' && (
          <OracleWalletFields
            values={form.cred}
            onChange={setCred}
            idPrefix={`${idPrefix}-wallet`}
            mode={credentialsMode}
          />
        )}
      </div>
    </div>
  );
}
