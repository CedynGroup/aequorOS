import { fileURLToPath } from "node:url";
import { configDefaults, defineConfig } from "vitest/config";

export default defineConfig({
  resolve: { alias: { "@": fileURLToPath(new URL(".", import.meta.url)) } },
  test: {
    include: ["tests/**/*.spec.{ts,tsx}"],
    // Browser suites share the feature folders: integration (HTTP and
    // presentation contracts) and e2e (journeys) belong to Playwright.
    exclude: [
      ...configDefaults.exclude,
      "tests/**/*.integration.spec.ts",
      "tests/**/*.e2e.spec.ts",
    ],
    coverage: {
      provider: "v8",
      include: [
        "app/**/*.{ts,tsx}",
        "components/**/*.{ts,tsx}",
        "features/**/*.{ts,tsx}",
        "lib/**/*.{ts,tsx}",
      ],
      exclude: ["**/*.test.{ts,tsx}", "**/*.spec.{ts,tsx}", "**/*.d.ts"],
      reportsDirectory: "coverage/vitest",
      reporter: ["json", "json-summary", "lcov", "html", "text-summary"],
    },
  },
});
