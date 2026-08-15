/**
 * Admin-governance shared helpers (operator RBAC, audit log, Tenant Inspector).
 *
 * Pure functions + display maps only — no React. Consumed by the admin View
 * components. The role ladder, CSV export, and detail pretty-printing live here
 * so the three surfaces stay consistent.
 */

import type { StatusTone } from '@/components/ui';

// --------------------------------------------------------------------------
// Operator role ladder (developer < operator_admin < super_admin)
//
// `OperatorUser.role` is a service-owned string; these are the roles the
// console offers when CREATING an operator and the ranks/labels/tones it maps
// any returned role to. A rank rule (backend-enforced) blocks managing a role
// above your own — we surface those 403s cleanly rather than hiding controls,
// because the console cannot read its own operator's role client-side.
// --------------------------------------------------------------------------

export interface RoleOption {
  value: string;
  label: string;
  /** Ladder position (higher = more privilege). */
  rank: number;
  hint: string;
}

export const OPERATOR_ROLES: RoleOption[] = [
  {
    value: 'developer',
    label: 'Developer',
    rank: 1,
    hint: 'Read + developer tooling. Cannot manage operators, audit, or inspection.',
  },
  {
    value: 'operator_admin',
    label: 'Operator admin',
    rank: 2,
    hint: 'Full governance: operator RBAC, audit log, and Tenant Inspector.',
  },
  {
    value: 'super_admin',
    label: 'Super admin',
    rank: 3,
    hint: 'Top of the ladder — founder tier. Can manage every other operator.',
  },
];

const ROLE_RANK: Record<string, number> = {
  developer: 1,
  operator: 1, // legacy alias seen in the wire contract
  operator_admin: 2,
  super_admin: 3,
};

export function roleRank(role: string | null | undefined): number {
  if (!role) return 0;
  return ROLE_RANK[role] ?? 0;
}

/** Human label for a role string (known label, else humanized). */
export function roleLabel(role: string | null | undefined): string {
  if (!role) return '—';
  const known = OPERATOR_ROLES.find((r) => r.value === role);
  if (known) return known.label;
  return humanize(role);
}

/** Status-pill tone for a role — privilege reads warmer as it climbs. */
export function roleTone(role: string | null | undefined): StatusTone {
  switch (role) {
    case 'super_admin':
      return 'action';
    case 'operator_admin':
      return 'approaching';
    case 'developer':
    case 'operator':
      return 'slate';
    default:
      return 'slate';
  }
}

/** `snake_case` / `kebab-case` → `Title Case` for unknown vocabularies. */
export function humanize(value: string): string {
  return value
    .replace(/[_-]+/g, ' ')
    .trim()
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

// --------------------------------------------------------------------------
// Audit detail rendering
// --------------------------------------------------------------------------

/**
 * The audit `detail` field is a string on the wire. It is very often a JSON
 * blob — pretty-print it when it parses, otherwise return it verbatim. Never
 * throw: an un-parseable detail is still shown to the operator as-is.
 */
export function prettyDetail(detail: string | null | undefined): string {
  if (!detail) return '';
  const trimmed = detail.trim();
  if (!trimmed) return '';
  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2);
  } catch {
    return detail;
  }
}

// --------------------------------------------------------------------------
// Client-side CSV export (answers "has any staff accessed tenant X in 90d?")
// --------------------------------------------------------------------------

function csvCell(value: string | number | null | undefined): string {
  const s = value === null || value === undefined ? '' : String(value);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** Build an RFC-4180-ish CSV string from a header row + body rows. */
export function toCsv(
  headers: string[],
  rows: Array<Array<string | number | null | undefined>>,
): string {
  return [headers, ...rows].map((row) => row.map(csvCell).join(',')).join('\r\n');
}

/** Trigger a browser download of a CSV string. Client-only. */
export function downloadCsv(filename: string, csv: string): void {
  const blob = new Blob([`﻿${csv}`], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

// --------------------------------------------------------------------------
// Inspector session state
// --------------------------------------------------------------------------

/**
 * A session is "live" until its `expires_at`. The contract carries no
 * `ended_at`/`ended_by`, so an early-ended or lapsed session both read as
 * not-live here — we label those "expired".
 */
export function isSessionLive(expiresAt: string | null | undefined, now = Date.now()): boolean {
  if (!expiresAt) return false;
  const exp = Date.parse(expiresAt);
  return Number.isNaN(exp) ? false : exp > now;
}
