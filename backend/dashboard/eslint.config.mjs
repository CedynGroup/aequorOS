import { fixupConfigRules } from "@eslint/compat";
import { defineConfig, globalIgnores } from "eslint/config";
import { readdirSync } from "node:fs";
import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import tseslint from "typescript-eslint";

// Features are enumerated so each feature's override allows its own internals
// while the fast inner-loop rule stays correct as new features move in.
const featureNames = readdirSync(new URL("./features", import.meta.url), {
  withFileTypes: true,
})
  .filter((entry) => entry.isDirectory())
  .map((entry) => entry.name);

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
  // Feature packages cross features only through @/features/<feature>/public
  // (FEATURE_LAYOUT.md); a feature's own internals are unrestricted. app/(app)
  // route shims stay exempt: they exist to re-export feature routes. The
  // ratchet guard in scripts/check_feature_boundaries.mjs remains the
  // authoritative boundary check; this rule is the fast inner loop.
  ...featureNames.map((name) => ({
    files: [`features/${name}/**`],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              regex: `^@/features/(?!${name}/)(?![^/]+/public$)`,
              message:
                "Cross-feature imports must go through @/features/<feature>/public (FEATURE_LAYOUT.md).",
            },
          ],
        },
      ],
    },
  })),
  globalIgnores([
    ".next/**",
    ".next-*/**",
    ".test-out/**",
    "coverage/**",
    "e2e/.tmp/**",
    "test-results/**",
    "playwright-report/**",
    "next-env.d.ts",
  ]),
]);
