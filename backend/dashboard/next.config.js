/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The dashboard owns the root of its own origin (production: app.aequoros.com;
  // dev: localhost:3001), so there is NO path prefix by default. Only set
  // NEXT_PUBLIC_BASE_PATH (e.g. /dashboard) if the app is ever path-mounted behind
  // another domain — leave it unset for the subdomain deployment.
  basePath: process.env.NEXT_PUBLIC_BASE_PATH ?? '',
};

module.exports = nextConfig;
