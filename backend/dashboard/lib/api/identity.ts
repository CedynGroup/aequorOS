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
