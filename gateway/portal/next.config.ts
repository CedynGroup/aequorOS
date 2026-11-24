import path from "node:path";
import type { NextConfig } from "next";

// The gateway is its own workspace (gateway/pnpm-workspace.yaml). Pin both the
// Turbopack root and the output tracing root to it, so the build never
// resolves or traces files from the AequorOS repository around it.
const gatewayRoot = path.join(__dirname, "..");

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  poweredByHeader: false,
  outputFileTracingRoot: gatewayRoot,
  turbopack: { root: gatewayRoot },
};

export default nextConfig;
