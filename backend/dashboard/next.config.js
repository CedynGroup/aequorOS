const path = require('path');

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Self-contained server output for the Docker/Coolify image (.next/standalone).
  output: 'standalone',
  experimental: {
    // Monorepo: trace files from the repo root so the standalone bundle picks up
    // the workspace API client and the hoisted pnpm node_modules.
    outputFileTracingRoot: path.join(__dirname, '../../'),
  },
  // @aequoros/risk-service-api is a workspace package consumed as TypeScript
  // source, so Next must transpile it during the production build.
  transpilePackages: ['@aequoros/risk-service-api'],
  // Lint is enforced in CI/local (`pnpm lint`), not during the container build —
  // keeps deploys deterministic and off the interactive eslint-setup path.
  // Type-checking still runs.
  eslint: { ignoreDuringBuilds: true },
  // The dashboard owns the root of its own origin (production: app.aequoros.com;
  // dev: localhost:3001), so there is NO path prefix by default. Only set
  // NEXT_PUBLIC_BASE_PATH (e.g. /dashboard) if the app is ever path-mounted behind
  // another domain — leave it unset for the subdomain deployment.
  basePath: process.env.NEXT_PUBLIC_BASE_PATH ?? '',
};

module.exports = nextConfig;
