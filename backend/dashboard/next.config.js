/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Serve under a path prefix when deployed behind the main domain
  // (production: aequoros.com/dashboard — set NEXT_PUBLIC_BASE_PATH=/dashboard).
  // Empty in dev, so localhost:3001 keeps working unchanged.
  basePath: process.env.NEXT_PUBLIC_BASE_PATH ?? '',
};

module.exports = nextConfig;
