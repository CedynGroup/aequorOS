import type { Metadata } from 'next';
import { Inter, IBM_Plex_Mono } from 'next/font/google';
import { Analytics } from '@vercel/analytics/react';
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
    <html lang="en" className={`${inter.variable} ${plexMono.variable}`}>
      <body className="font-sans text-body bg-surface-alt text-navy">
        {children}
        <Analytics />
      </body>
    </html>
  );
}
