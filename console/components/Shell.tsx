'use client';

/**
 * Back-compat shim. The console shell was rebuilt into
 * `components/shell/AppShell.tsx` (grouped rail + top bar + command palette +
 * toast/inspector providers). This re-export keeps any `@/components/Shell`
 * import working.
 */
export { default } from '@/components/shell/AppShell';
