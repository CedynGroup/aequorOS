import type { Metadata } from 'next';
import { Inter, IBM_Plex_Mono } from 'next/font/google';
import './globals.css';

const inter = Inter({
  subsets: ['latin'],
  variable: '--font-inter',
  weight: ['400', '500', '600', '700'],
  display: 'swap',
});

const plexMono = IBM_Plex_Mono({
  subsets: ['latin'],
  variable: '--font-plex-mono',
  weight: ['400', '500', '600'],
  display: 'swap',
});

export const metadata: Metadata = {
  title: 'AequorOS Operator Console',
  description:
    'Staff developer portal for the AequorOS platform — tenant inventory, onboarding, and data-engine operations. Internal tooling; not a bank-facing surface.',
  robots: {
    // Internal staff tooling — never indexed.
    index: false,
    follow: false,
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // Dark-only console: data-theme is pinned statically (no toggle, no
    // theme-init script — the light token block is deliberately absent from
    // globals.css).
    <html lang="en" data-theme="dark" className={`${inter.variable} ${plexMono.variable}`}>
      <body className="font-sans text-body bg-surface-alt text-ink">{children}</body>
    </html>
  );
}
