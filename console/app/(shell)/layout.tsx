'use client';

import AuthGate from '@/components/AuthGate';
import Shell from '@/components/Shell';
import type { ReactNode } from 'react';

/** Every screen except /login lives behind the auth gate inside the shell. */
export default function ConsoleLayout({ children }: { children: ReactNode }) {
  return (
    <AuthGate>
      <Shell>{children}</Shell>
    </AuthGate>
  );
}
