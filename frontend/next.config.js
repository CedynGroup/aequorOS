/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  distDir: process.env.NEXT_DIST_DIR || '.next',
  images: {
    // Self-hosted optimizer (Coolify, no Vercel edge): cache generated
    // variants for 31 days so only the first visitor ever pays a transcode.
    minimumCacheTTL: 2678400,
  },
  // The client dashboard is a separate deployment on its own subdomain
  // (bank.aequoros.com). "Client Login" links there directly — no proxy/rewrite
  // from the marketing app is needed. (Auth callbacks and the sign-in page all
  // live on the dashboard's own origin.)
};

module.exports = nextConfig;
