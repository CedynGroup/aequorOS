/**
 * Small helpers for rendering the signed-in user's identity (name, role, avatar
 * initials) consistently across the shell header and settings.
 */

/** Initials from a display name (or the email local-part as a fallback). */
export function initialsFrom(nameOrEmail: string): string {
  const base = nameOrEmail.includes('@') ? nameOrEmail.split('@')[0] : nameOrEmail;
  const parts = base.split(/[\s._-]+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return base.slice(0, 2).toUpperCase();
}

/** Backend role code → human label. Unknown roles fall back to capitalization. */
export function roleLabel(role: string | undefined | null): string {
  if (!role) return 'Signed in';
  const labels: Record<string, string> = {
    admin: 'Administrator',
    approver: 'Approver',
    analyst: 'Analyst',
    viewer: 'Viewer',
  };
  return labels[role] ?? role.charAt(0).toUpperCase() + role.slice(1);
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
