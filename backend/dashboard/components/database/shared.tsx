'use client';

/**
 * Shared vocabulary for the Database (Direct) console: backend metadata
 * (Oracle, SQL Server, JDBC, ODBC), sensible per-backend port/service defaults,
 * the write-only credential form shape, connection-status tones, and small
 * formatting helpers.
 *
 * Credentials are WRITE-ONLY: forms collect values into a payload sent once and
 * never read back — the API only ever returns status, fingerprint, and expiry.
 */

import type { Backend } from '@aequoros/risk-service-api';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';

export type BackendKey = Backend;

export const BACKENDS: {
  key: BackendKey;
  name: string;
  blurb: string;
  defaultPort: number;
  /** Oracle addresses a database by service name; the others by database. */
  usesServiceName: boolean;
  databaseLabel: string;
  databaseHint: string;
}[] = [
  {
    key: 'oracle',
    name: 'Oracle Database',
    blurb:
      'Native Oracle driver (thin). Addresses the instance by service name — the standard for FLEXCUBE and most Oracle-hosted cores.',
    defaultPort: 1521,
    usesServiceName: true,
    databaseLabel: 'Database (SID, optional)',
    databaseHint: 'Leave blank when connecting by service name.',
  },
  {
    key: 'sqlserver',
    name: 'Microsoft SQL Server',
    blurb:
      'Native SQL Server driver. Addresses a named database on the instance — common for Finacle and .NET-hosted reporting replicas.',
    defaultPort: 1433,
    usesServiceName: false,
    databaseLabel: 'Database',
    databaseHint: 'The reporting database to read from.',
  },
  {
    key: 'jdbc',
    name: 'Generic JDBC',
    blurb:
      'Any JDBC-compliant core without a native driver. The driver-specific connection string is carried in connection options.',
    defaultPort: 0,
    usesServiceName: false,
    databaseLabel: 'Database',
    databaseHint: 'The catalog / database exposed to the service user.',
  },
  {
    key: 'odbc',
    name: 'Generic ODBC',
    blurb:
      'Any ODBC-registered source (DSN-less). The driver and DSN attributes are carried in connection options.',
    defaultPort: 0,
    usesServiceName: false,
    databaseLabel: 'Database',
    databaseHint: 'The catalog / database exposed to the service user.',
  },
];

export function backendMeta(key: string) {
  return BACKENDS.find((backend) => backend.key === key);
}

export function backendName(key: string): string {
  return backendMeta(key)?.name ?? key;
}

/** One credential form field. Secrets render as password inputs and are never
 * pre-filled — stored values are represented only by the fingerprint. */
export type CredentialField = {
  key: string;
  label: string;
  secret?: boolean;
  optional?: boolean;
  placeholder?: string;
  hint?: string;
};

/**
 * The direct-connection credential shape. A read-only service user plus, for
 * password auth, its password; `extra` carries backend-specific secret material
 * (an Oracle wallet handle, a JDBC property blob, an ODBC connection attribute)
 * as a JSON object that is stored encrypted and never returned.
 */
export const CREDENTIAL_FIELDS: CredentialField[] = [
  {
    key: 'username',
    label: 'Service user',
    hint: 'The dedicated read-only database user provisioned for AequorOS.',
  },
  { key: 'password', label: 'Password', secret: true },
  {
    key: 'extra',
    label: 'Extra secret material (optional)',
    secret: true,
    optional: true,
    placeholder: '{"wallet": "..."}',
    hint: 'Backend-specific secret material as JSON — e.g. an Oracle wallet handle or a driver property blob.',
  },
];

/** Connection-status chip tones (mirrors the T24 credential-lifecycle mapping). */
const STATUS_TONES: Record<string, StatusTone> = {
  ACTIVE: 'success',
  TESTING: 'action',
  EXPIRING_SOON: 'amber',
  EXPIRED: 'critical',
  REVOKED: 'critical',
  INVALID: 'critical',
  REPLACED_PENDING_DELETION: 'amber',
  DISABLED: 'slate',
};

export function ConnectionStatusPill({ status }: { status: string }) {
  return (
    <StatusPill tone={STATUS_TONES[status] ?? 'pending'}>
      {status.replaceAll('_', ' ')}
    </StatusPill>
  );
}

/** Last-sync outcome tone. The backend uses ACCEPTED_WITH_WARNINGS for the demo
 * bank's deliberate GL drift — surfaced as a warning, not a failure. */
const SYNC_STATUS_TONES: Record<string, StatusTone> = {
  ACCEPTED: 'success',
  accepted: 'success',
  ACCEPTED_WITH_WARNINGS: 'amber',
  accepted_with_warnings: 'amber',
  REJECTED: 'critical',
  rejected: 'critical',
  FAILED: 'critical',
  failed: 'critical',
  RUNNING: 'action',
  running: 'action',
};

export function SyncStatusPill({ status }: { status: string }) {
  return (
    <StatusPill tone={SYNC_STATUS_TONES[status] ?? 'pending'}>
      {status.replaceAll('_', ' ')}
    </StatusPill>
  );
}

export function fmtWhen(value: string | Date | null | undefined): string {
  if (!value) return 'never';
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return 'never';
  return date.toLocaleString('en-GB', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'UTC',
  });
}

/** Split a comma / whitespace / newline separated list into trimmed entries. */
export function splitList(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((entry) => entry.trim())
    .filter(Boolean);
}

/** Turn a stored credential-extra JSON string into an object, or undefined. */
export function parseExtra(value: string): Record<string, unknown> | undefined {
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  try {
    const parsed = JSON.parse(trimmed);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
  } catch {
    // Not valid JSON — fall through; the caller surfaces the validation hint.
  }
  return undefined;
}

export function extraIsValid(value: string): boolean {
  return !value.trim() || parseExtra(value) !== undefined;
}
