import type { Metadata } from "next";
import localFont from "next/font/local";
import Providers from "./providers";
import ThemeProvider from "@/components/shell/ThemeProvider";
import "./globals.css";

/**
 * Sets `data-theme` on <html> synchronously, before first paint, so the
 * dark-first token system in globals.css never flashes the wrong theme.
 * Mirrors ThemeProvider's storage key ('aeq-theme'); dark is the fallback —
 * 'system' applies only when a user explicitly chose it in the toggle.
 */
const themeInitScript = `(function(){try{var t=localStorage.getItem('aeq-theme');var p=t==='light'||t==='dark'||t==='system'?t:'dark';var r=p==='system'?(matchMedia('(prefers-color-scheme: light)').matches?'light':'dark'):p;document.documentElement.dataset.theme=r;}catch(e){document.documentElement.dataset.theme='dark';}})();`;

const inter = localFont({
  src: "./fonts/inter-latin-wght-normal.woff2",
  variable: "--font-inter",
  weight: "400 700",
  display: "swap",
});

const plexMono = localFont({
  src: [
    { path: "./fonts/ibm-plex-mono-latin-400-normal.woff2", weight: "400" },
    { path: "./fonts/ibm-plex-mono-latin-500-normal.woff2", weight: "500" },
    { path: "./fonts/ibm-plex-mono-latin-600-normal.woff2", weight: "600" },
  ],
  variable: "--font-plex-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "AequorOS — Treasury Reimagined",
  description:
    "AequorOS Treasury and ALM platform — interactive prototype demonstrating Liquidity, IRR, FX, Basel Capital, FTP, and Balance Sheet Forecasting modules for mid-tier African banks.",
  robots: {
    // Demo prototype — keep out of search until explicit launch
    index: false,
    follow: false,
  },
  // Explicit order so the crisp SVG is the primary favicon (Next's file
  // convention otherwise links only the PNG). PNG stays as a raster fallback.
  icons: {
    icon: [
      { url: "/icon.svg", type: "image/svg+xml" },
      { url: "/icon.png", type: "image/png", sizes: "1024x1024" },
    ],
    apple: { url: "/apple-icon.png", sizes: "180x180" },
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
      </body>
    </html>
  );
}
