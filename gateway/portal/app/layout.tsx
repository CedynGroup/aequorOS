import type { Metadata, Viewport } from "next";
import { IBM_Plex_Mono, Inter } from "next/font/google";
import type { ReactNode } from "react";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter", weight: ["400", "500", "600", "700"], display: "swap" });
const plexMono = IBM_Plex_Mono({ subsets: ["latin"], variable: "--font-plex-mono", weight: ["400", "500"], display: "swap" });

export const metadata: Metadata = {
  title: { default: "AequorOS Portal", template: "%s · AequorOS Portal" },
  description: "Bank of Ghana supervisors review, query and accept regulatory filings received through the gateway.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#0F1E36" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en-GB" className={`${inter.variable} ${plexMono.variable}`}>
      <body className="bg-canvas font-sans text-ink antialiased">{children}</body>
    </html>
  );
}
