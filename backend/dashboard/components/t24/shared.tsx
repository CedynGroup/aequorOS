'use client';

/**
 * Shared vocabulary for the Temenos T24 console: connection-mode metadata,
 * per-mode credential form shapes, credential-lifecycle status tones, domain
 * category labels, and pull-cadence options.
 *
 * Credentials are WRITE-ONLY: forms collect values into a payload sent once and
 * never read back — the API only ever returns status, fingerprint, and expiry.
 */

import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';

export type ModeKey = 'OFS' | 'IRIS' | 'OPEN_API';
export type CoreSystemKey = 'T24' | 'FINACLE' | 'FLEXCUBE';

export const MODES: {
  key: ModeKey;
  name: string;
  channel: string;
  description: string;
  endpointHint: string;
}[] = [
  {
    key: 'OFS',
    name: 'OFS',
    channel: 'Open Financial Service (TAFJ)',
    description:
      'The standard TAFJ integration for on-premise T24 — OFS enquiries over an authenticated service user. The dominant channel for African T24 sites.',
    endpointHint: 'ofs://core.bank.internal',
  },
  {
    key: 'IRIS',
    name: 'IRIS REST',
    channel: 'Interaction Framework provider container',
    description:
      'The IRIS REST layer over T24 enquiries and services, authenticated with an OAuth2 bearer token.',
    endpointHint: 'https://iris.bank.internal',
  },
  {
    key: 'OPEN_API',
    name: 'Transact Open APIs',
    channel: 'Transact API gateway / Data Hub',
    description:
      'The published Transact product APIs (party, holdings, lending, …) via the API gateway, authenticated with client credentials.',
    endpointHint: 'https://api.bank.internal',
  },
];

export function modeName(key: string): string {
  return MODES.find((mode) => mode.key === key)?.name ?? key;
}

export const CORE_SYSTEMS: { key: CoreSystemKey; name: string }[] = [
  { key: 'T24', name: 'Temenos T24 / Transact' },
  { key: 'FINACLE', name: 'Infosys Finacle' },
  { key: 'FLEXCUBE', name: 'Oracle FLEXCUBE' },
];

/** One credential form field. Secrets render as password inputs and are never
 * pre-filled — stored values are represented only by the fingerprint. */
export type CredentialField = {
  key: string;
  label: string;
  secret?: boolean;
  placeholder?: string;
  hint?: string;
};

/** Per-mode credential structures. OFS wants a service-user password;
 * IRIS/Open API want a client-credentials pair (an api_key bearer is accepted
 * as an alternative). */
export const CREDENTIAL_FIELDS: Record<ModeKey, CredentialField[]> = {
  OFS: [
    {
      key: 'username',
      label: 'Service user',
      hint: 'The dedicated T24 user provisioned for AequorOS (read-only).',
    },
    { key: 'password', label: 'Service user password', secret: true },
  ],
  IRIS: [
    { key: 'client_id', label: 'Client ID' },
    { key: 'client_secret', label: 'Client secret', secret: true },
    {
      key: 'api_key',
      label: 'API key (optional)',
      secret: true,
      hint: 'A bearer key may be used instead of the client-credentials pair.',
    },
  ],
  OPEN_API: [
    { key: 'client_id', label: 'Client ID' },
    { key: 'client_secret', label: 'Client secret', secret: true },
    {
      key: 'api_key',
      label: 'API key (optional)',
      secret: true,
      hint: 'A bearer key may be used instead of the client-credentials pair.',
    },
  ],
};

/** Credential-lifecycle status-chip tones (mirrors the market-data mapping). */
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

export const DOMAIN_CATEGORY_LABELS: Record<string, string> = {
  GL: 'General ledger',
  POSITIONS: 'Positions',
  SECURITIES: 'Securities',
  OFF_BALANCE: 'Off-balance sheet',
  MASTER_DATA: 'Master data',
  LIMITS: 'Limits',
  CASHFLOWS: 'Cash flows',
  HISTORICAL: 'Historical',
  REFERENCE: 'Reference',
};

export const ENTITY_TYPE_LABELS: Record<string, string> = {
  gl_account: 'GL account',
  position: 'Position',
  counterparty: 'Counterparty',
  product: 'Product',
  reference: 'Reference dataset',
};

export const CADENCE_OPTIONS = [
  { value: 'ON_DEMAND', label: 'On demand' },
  { value: 'END_OF_DAY', label: 'End of day (post-COB)' },
  { value: 'WEEKLY', label: 'Weekly' },
  { value: 'MONTHLY', label: 'Monthly' },
];

export function cadenceLabel(value: string): string {
  return CADENCE_OPTIONS.find((option) => option.value === value)?.label ?? value;
}

/** "POSITIONS_LOANS" -> "Loans", "OFF_BALANCE_LC" -> "LC". */
export function domainShortLabel(domain: string, category: string): string {
  const stripped = domain.replace(new RegExp(`^${category}_`), '');
  return stripped
    .toLowerCase()
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (character) => character.toUpperCase());
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
