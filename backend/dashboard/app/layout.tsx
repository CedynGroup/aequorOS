import type { Metadata } from 'next';
import { Inter, IBM_Plex_Mono } from 'next/font/google';
import { Analytics } from '@vercel/analytics/react';
import Providers from './providers';
import ThemeProvider from '@/components/shell/ThemeProvider';
import './globals.css';

/**
 * Sets `data-theme` on <html> synchronously, before first paint, so the
 * dark-first token system in globals.css never flashes the wrong theme.
 * Mirrors ThemeProvider's storage key ('aeq-theme'); default is dark.
 */
const themeInitScript = `(function(){try{var t=localStorage.getItem('aeq-theme');document.documentElement.dataset.theme=t==='light'?'light':'dark';}catch(e){document.documentElement.dataset.theme='dark';}})();`;

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
  title: 'AequorOS — Treasury Reimagined',
  description:
    'AequorOS Treasury and ALM platform — interactive prototype demonstrating Liquidity, IRR, FX, Basel Capital, FTP, and Balance Sheet Forecasting modules for mid-tier African banks.',
  robots: {
    // Demo prototype — keep out of search until explicit launch
    index: false,
    follow: false,
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${plexMono.variable}`}
      suppressHydrationWarning
    >
      <body className="font-sans text-body bg-surface-alt text-ink">
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
        <Providers>
          <ThemeProvider>{children}</ThemeProvider>
        </Providers>
        <Analytics />
      </body>
    </html>
  );
}
