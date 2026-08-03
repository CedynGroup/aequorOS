import type { Metadata } from 'next';
import { Fraunces, Inter } from 'next/font/google';
import Analytics from '@/components/Analytics';
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
    'Live cloud-native Treasury and ALM platform for mid-tier African banks — Data Engine, liquidity, capital, risk, and Bank of Ghana regulatory returns. Product interface public; onboarding pilot banks.',
  metadataBase: new URL('https://aequoros.com'),
  openGraph: {
    title: 'AequorOS — Treasury and ALM infrastructure for African banks',
    description:
      'Working product for mid-tier African banks: connect core systems, run ALM engines, generate auditable regulatory returns. Browse the product interface — no login required.',
    type: 'website',
    url: 'https://aequoros.com',
    siteName: 'AequorOS',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'AequorOS — Treasury and ALM infrastructure for African banks',
    description:
      'Live Treasury and ALM platform for African banks. Product interface public; onboarding pilot banks.',
  },
  // Explicit order so the crisp SVG is the primary favicon (Next's file
  // convention otherwise links only the PNG). PNG stays as a raster fallback.
  icons: {
    icon: [
      { url: '/icon.svg', type: 'image/svg+xml' },
      { url: '/icon.png', type: 'image/png', sizes: '1024x1024' },
    ],
    apple: { url: '/apple-icon.png', sizes: '180x180' },
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
