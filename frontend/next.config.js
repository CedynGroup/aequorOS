/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  distDir: process.env.NEXT_DIST_DIR || '.next',
  images: {
    remotePatterns: [
      {
        protocol: 'https',
        hostname: 'ui-avatars.com',
      },
    ],
  },
  // The client dashboard is a separate deployment on its own subdomain
  // (bank.aequoros.com). "Client Login" links there directly — no proxy/rewrite
  // from the marketing app is needed. (Auth callbacks and the sign-in page all
  // live on the dashboard's own origin.)
};

module.exports = nextConfig;
