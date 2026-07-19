/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  images: {
    remotePatterns: [
      {
        protocol: 'https',
        hostname: 'ui-avatars.com',
      },
    ],
  },
  // Serve the sign-in page at the ROOT /login (aequoros.com/login) by proxying
  // the dashboard app's login page. The dashboard itself lives under
  // /dashboard (its own deployment); this rewrite is what lets the login URL
  // stay clean while the app keeps its base path. In dev this proxies the
  // local dashboard dev server.
  async rewrites() {
    const dashboard = (
      process.env.NEXT_PUBLIC_DASHBOARD_URL ?? 'http://localhost:3001'
    ).replace(/\/$/, '');
    return [{ source: '/login', destination: `${dashboard}/login` }];
  },
};

module.exports = nextConfig;
