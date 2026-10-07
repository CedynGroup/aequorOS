import { fixupConfigRules } from "@eslint/compat";
import { defineConfig, globalIgnores } from "eslint/config";
import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import tseslint from "typescript-eslint";

export default defineConfig([
  // eslint-config-next 16 still bundles plugins written against the ESLint 9
  // rule context (vercel/next.js#89764); the compat shim restores those APIs.
  ...fixupConfigRules(nextCoreWebVitals),
  {
    // Next's vendored Babel 7 parser lacks the scope-manager API ESLint 10
    // needs, so plain JavaScript goes through the TypeScript parser as well.
    files: ["**/*.{js,jsx,mjs,cjs}"],
    languageOptions: { parser: tseslint.parser },
  },
  {
    // These React Compiler diagnostics were not part of the previous lint gate.
    // Enable them with a dedicated cleanup when the app adopts the compiler.
    rules: {
      "react-hooks/purity": "off",
      "react-hooks/refs": "off",
      "react-hooks/set-state-in-effect": "off",
    },
  },
  globalIgnores([
    ".next/**",
    ".next-*/**",
    ".test-out/**",
    "e2e/.tmp/**",
    "test-results/**",
    "playwright-report/**",
    "next-env.d.ts",
  ]),
]);
