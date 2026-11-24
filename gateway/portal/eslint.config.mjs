import { fixupConfigRules } from "@eslint/compat";
import { defineConfig, globalIgnores } from "eslint/config";
import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import tseslint from "typescript-eslint";

export default defineConfig([
  // eslint-config-next 16 still bundles plugins written against the ESLint 9
  // rule context; the compat shim restores those APIs under ESLint 10.
  ...fixupConfigRules(nextCoreWebVitals),
  {
    // Next's vendored Babel parser lacks the scope-manager API ESLint 10
    // needs, so plain JavaScript goes through the TypeScript parser as well.
    files: ["**/*.{js,jsx,mjs,cjs}"],
    languageOptions: { parser: tseslint.parser },
  },
  {
    // Server Components render once per request and read the clock on
    // purpose (relative dates, default due dates); the React Compiler's
    // purity rule assumes re-rendering client components.
    rules: { "react-hooks/purity": "off" },
  },
  globalIgnores([".next/**", ".test-out/**", "next-env.d.ts"]),
]);
