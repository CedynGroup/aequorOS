import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { configDefaults, defineConfig } from "vitest/config";

function manualChunks(id: string) {
  if (id.includes("node_modules/react") || id.includes("node_modules/react-dom")) {
    return "vendor-react";
  }
  if (id.includes("node_modules/@tanstack")) {
    return "vendor-tanstack";
  }
  if (id.includes("node_modules/@radix-ui")) {
    return "vendor-radix";
  }
  if (
    id.includes("node_modules/lucide-react") ||
    id.includes("node_modules/date-fns") ||
    id.includes("node_modules/sonner") ||
    id.includes("node_modules/clsx") ||
    id.includes("node_modules/tailwind-merge")
  ) {
    return "vendor-ui";
  }
  if (id.includes("@aequoros/risk-service-api")) {
    return "risk-service-api";
  }
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    rolldownOptions: {
      output: {
        manualChunks,
      },
    },
  },
  test: {
    environment: "jsdom",
    exclude: [...configDefaults.exclude, "e2e/**", "src/**/*.browser.test.{ts,tsx}"],
    globals: true,
    setupFiles: "./src/test/setup.ts",
  },
});
