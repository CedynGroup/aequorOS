import type { ReactNode } from 'react';

/**
 * Markets Desk route group. Navigation now lives in the AppShell rail (the
 * "Markets Desk" group lists every desk surface), so this layout is a
 * pass-through — the old horizontal top-tab bar was removed to avoid crowding
 * the nav with a second row.
 */
export default function DeskLayout({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
