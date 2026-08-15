'use client';

import AuthGate from '@/components/AuthGate';
import AppShell from '@/components/shell/AppShell';
import type { ReactNode } from 'react';

/** Every screen except /login lives behind the auth gate inside the app shell. */
export default function ConsoleLayout({ children }: { children: ReactNode }) {
  return (
    <AuthGate>
      <AppShell>{children}</AppShell>
    </AuthGate>
  );
}
