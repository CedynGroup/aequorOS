'use client';

/**
 * Shared vocabulary for the Market Data Sources console: vendor metadata,
 * credential form shapes (§6.1 / §7.1), status-chip tones (§10.2), scope
 * category labels, pull-frequency options, and the tenant-header template
 * download helper.
 *
 * Credentials are WRITE-ONLY: forms collect values into a payload that is
 * sent once and never read back — the API only ever returns status,
 * fingerprint, and expiry.
 */

import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import { apiBaseUrl } from '@/lib/api/client';
import { getAccessToken } from '@/lib/api/token';

export type VendorKey = 'bloomberg' | 'refinitiv' | 'manual_upload';

export const VENDORS: {
  key: VendorKey;
  name: string;
  description: string;
}[] = [
  {
    key: 'bloomberg',
    name: 'Bloomberg',
    description:
      'Connect your existing Bloomberg B-PIPE or Data License subscription.',
  },
  {
    key: 'refinitiv',
    name: 'LSEG (formerly Refinitiv)',
    description: 'Connect your LSEG Data Platform subscription (the platform APIs behind LSEG Workspace).',
  },
  {
    key: 'manual_upload',
    name: 'Manual upload',
    description:
      "Upload market data files directly. Use if you don't have Bloomberg or LSEG, or as a backup source.",
  },
];

export function vendorName(key: string): string {
  return VENDORS.find((vendor) => vendor.key === key)?.name ?? key;
}

/** One credential form field. Secrets render as password inputs and are
 * never pre-filled — stored values are represented only by the fingerprint. */
export type CredentialField = {
  key: string;
  label: string;
  secret?: boolean;
  multiline?: boolean;
  placeholder?: string;
  hint?: string;
};

/** §6.1 Bloomberg and §7.1 Refinitiv credential structures. */
export const CREDENTIAL_FIELDS: Record<
  Exclude<VendorKey, 'manual_upload'>,
  CredentialField[]
> = {
  bloomberg: [
    { key: 'application_identifier', label: 'Application identifier' },
    { key: 'serial_number', label: 'Serial number' },
    { key: 'authentication_endpoint', label: 'Authentication endpoint' },
    {
      key: 'certificate',
      label: 'SSL client certificate (PEM)',
      secret: true,
      multiline: true,
      placeholder: '-----BEGIN CERTIFICATE-----',
    },
    { key: 'subscription_tier', label: 'Subscription tier' },
    {
      key: 'contact_admin',
      label: 'Bloomberg administrator contact',
      hint: 'Surfaced in error messages so operators know who to contact.',
    },
  ],
  refinitiv: [
    {
      key: 'client_id',
      label: 'Client ID',
      hint: 'Provided when you create an OAuth application in your Refinitiv Data Platform account.',
    },
    { key: 'client_secret', label: 'Client secret', secret: true },
    { key: 'scope', label: 'Scope' },
    { key: 'subscription_type', label: 'Subscription type' },
    { key: 'refresh_token', label: 'Refresh token', secret: true },
    { key: 'token_endpoint', label: 'Token endpoint' },
    { key: 'contact_admin', label: 'Refinitiv administrator contact' },
  ],
};

/** §10.2 credential-state chip tones. */
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

export const CATEGORY_LABELS: Record<string, string> = {
  YIELD_CURVE: 'Yield curves',
  FX_SPOT: 'FX spot rates',
  FX_FORWARD: 'FX forwards',
  SECURITY_MASTER: 'Security master',
  CREDIT_RATING: 'Credit ratings',
  MACRO_FORECAST: 'Macro forecasts',
};

export const FREQUENCY_OPTIONS = [
  { value: 'ON_DEMAND', label: 'On demand' },
  { value: 'HOURLY', label: 'Hourly (market hours)' },
  { value: 'END_OF_DAY', label: 'End of day' },
  { value: 'WEEKLY', label: 'Weekly' },
  { value: 'MONTHLY', label: 'Monthly' },
];

export function frequencyLabel(value: string): string {
  return (
    FREQUENCY_OPTIONS.find((option) => option.value === value)?.label ?? value
  );
}

/** "YIELD_CURVE_GHS" -> "GHS", "FX_SPOT_USD_GHS" -> "USD/GHS". */
export function scopeShortLabel(scope: string, category: string): string {
  const rest = scope
    .replace(new RegExp(`^${category}_`), '')
    .replace(/^MACRO_/, '');
  if (category === 'FX_SPOT') return rest.replace('_', '/');
  if (category === 'FX_FORWARD') {
    const parts = rest.split('_');
    return `${parts[0]}/${parts[1]} ${parts[2] ?? ''}`.trim();
  }
  return rest.replaceAll('_', ' ');
}

export function fmtWhen(value: Date | null | undefined): string {
  if (!value) return 'never';
  return value.toLocaleString('en-GB', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'UTC',
  });
}

/** §8.2 template kinds served by getMarketDataTemplate. */
export const TEMPLATE_KINDS: { kind: string; label: string }[] = [
  { kind: 'yield_curve', label: 'Yield curves' },
  { kind: 'fx_rates', label: 'FX rates' },
  { kind: 'credit_ratings', label: 'Credit ratings' },
  { kind: 'macro_forecasts', label: 'Macro forecasts' },
];

/**
 * Download one upload template. The endpoint is tenant-scoped, so a plain
 * anchor cannot carry the headers — fetch the bytes and hand them to the
 * browser as a Blob download.
 */
export async function downloadTemplate(kind: string): Promise<void> {
  const response = await fetch(`${apiBaseUrl}/market-data/templates/${kind}`, {
    headers: { Authorization: `Bearer ${getAccessToken() ?? ''}` },
  });
  if (!response.ok) {
    throw new Error(`Template download failed (${response.status}).`);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `${kind}_template.xlsx`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
