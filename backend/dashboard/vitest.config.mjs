import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: { alias: { "@": fileURLToPath(new URL(".", import.meta.url)) } },
  test: {
    include: ["unit/**/*.spec.{ts,tsx}"],
    coverage: {
      provider: "v8",
      include: [
        "app/**/*.{ts,tsx}",
        "components/**/*.{ts,tsx}",
        "lib/**/*.{ts,tsx}",
      ],
      exclude: ["**/*.test.{ts,tsx}", "**/*.spec.{ts,tsx}", "**/*.d.ts"],
      reportsDirectory: "coverage/vitest",
      reporter: ["json", "json-summary", "lcov", "html", "text-summary"],
    },
  },
});
