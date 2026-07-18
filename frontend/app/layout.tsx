import type { Metadata } from 'next';
import { Fraunces, Inter } from 'next/font/google';
import { Analytics } from '@vercel/analytics/react';
import Navigation from '@/components/Navigation';
import Footer from '@/components/Footer';
import './globals.css';

const fraunces = Fraunces({
  subsets: ['latin'],
  variable: '--font-fraunces',
  weight: ['400', '600', '700'],
  display: 'swap',
});

const inter = Inter({
  subsets: ['latin'],
  variable: '--font-inter',
  weight: ['400', '500', '600'],
  display: 'swap',
});

export const metadata: Metadata = {
  title: 'AequorOS — Treasury and ALM infrastructure for African banks',
  description:
    'Cloud-native balance sheet management, regulatory capital reporting, and risk modeling for mid-tier banks across sub-Saharan Africa.',
  metadataBase: new URL('https://aequoros.com'),
  openGraph: {
    title: 'AequorOS — Treasury and ALM infrastructure for African banks',
    description:
      'A working, cloud-native Treasury and ALM platform for mid-tier African banks — connected to the core, auditable end to end. MVP live; onboarding pilot banks.',
    type: 'website',
    url: 'https://aequoros.com',
    siteName: 'AequorOS',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'AequorOS — Treasury and ALM infrastructure for African banks',
    description:
      'A working, cloud-native Treasury and ALM platform for mid-tier African banks. MVP live; onboarding pilot banks.',
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${fraunces.variable} ${inter.variable}`}>
      <body className="font-sans bg-white text-text-primary antialiased">
        <Navigation />
        <main>{children}</main>
        <Footer />
        <Analytics />
      </body>
    </html>
  );
}
