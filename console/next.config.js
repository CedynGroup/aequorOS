/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // No eslint config is shipped yet (the package.json deliberately has no lint
  // script until eslint is configured) — keep `next build` off the interactive
  // eslint-setup path. Type-checking still runs during the build.
  eslint: { ignoreDuringBuilds: true },
};

module.exports = nextConfig;
