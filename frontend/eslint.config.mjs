import { fixupConfigRules } from "@eslint/compat";
import { defineConfig, globalIgnores } from "eslint/config";
import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

export default defineConfig([
  // eslint-config-next 16 still bundles plugins written against the ESLint 9
  // rule context (vercel/next.js#89764); the compat shim restores those APIs.
  // `nextTypescript` already routes every file through the TypeScript parser,
  // which Next's vendored Babel 7 parser cannot replace under ESLint 10.
  ...fixupConfigRules([...nextCoreWebVitals, ...nextTypescript]),
  globalIgnores([
    ".next/**",
    ".next-dev/**",
    "node_modules/**",
    "next-env.d.ts",
  ]),
]);
