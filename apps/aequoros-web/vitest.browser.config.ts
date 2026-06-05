import { playwright } from "@vitest/browser-playwright";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  test: {
    browser: {
      enabled: true,
      instances: [{ browser: "chromium" }],
      provider: playwright(),
    },
    globals: true,
    include: ["src/**/*.browser.test.{ts,tsx}"],
    setupFiles: "./src/test/setup.ts",
  },
});
