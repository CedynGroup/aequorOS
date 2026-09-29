/**
 * Small helpers for rendering the signed-in user's identity (name, role, avatar
 * initials) consistently across the shell header and settings.
 */

import { ROLE_OPTIONS } from './grants';
import { labelize } from './values';

/** Initials from a display name (or the email local-part as a fallback). */
export function initialsFrom(nameOrEmail: string): string {
  const base = nameOrEmail.includes('@') ? nameOrEmail.split('@')[0] : nameOrEmail;
  const parts = base.split(/[\s._-]+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return base.slice(0, 2).toUpperCase();
}

/**
 * The scalar `users.role` values (`app/core/security.py::ROLES`, plus the
 * `account_admin` that migration `202608280046` converted every `admin` to)
 * and the role bundles, in production copy.
 *
 * The bundle names are READ from the grant composer's own list so the avatar
 * menu and Settings → Members cannot call one authority two things. The
 * scalar-only roles are named here. Anything else degrades through `labelize`,
 * which never leaves an underscore on screen — the previous fallback
 * capitalised the first letter only, so every account administrator saw
 * "Account_admin" under their name on every page.
 */
const ROLE_LABELS: Readonly<Record<string, string>> = {
  ...Object.fromEntries(ROLE_OPTIONS.map(([code, label]) => [code, label])),
  admin: 'Administrator',
  examiner: 'Examiner',
  org_owner: 'Organization Owner',
};

/** Backend role code → human label. Unknown roles fall back to readable words. */
export function roleLabel(role: string | undefined | null): string {
  if (!role) return 'Signed in';
  return ROLE_LABELS[role] ?? labelize(role);
}

/**
 * Stable, high-contrast initials background derived from immutable identity.
 *
 * Deliberate identity-palette exception to the CSS-variable rule: these are
 * concrete hex values (inline `backgroundColor`) because the same user must
 * get the same recognizable hue in both themes — the point is stable
 * identity, not theme adaptation. Every value holds white initials at
 * readable contrast and stays distinguishable from the dark theme's
 * surfaces (#101827/#17202F).
 */
export function avatarColor(identity: string): string {
  const palette = [
    '#0f766e',
    '#1d4ed8',
    '#6d28d9',
    '#a21caf',
    '#be123c',
    '#b45309',
    '#047857',
    '#475569', // was #334155 — too close to the dark theme's surfaces
  ];
  let hash = 0;
  for (const character of identity) {
    hash = (hash * 31 + character.charCodeAt(0)) | 0;
  }
  return palette[Math.abs(hash) % palette.length];
}
