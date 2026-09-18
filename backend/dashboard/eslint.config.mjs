import { defineConfig, globalIgnores } from "eslint/config";
import nextCoreWebVitals from "eslint-config-next/core-web-vitals";

export default defineConfig([
  ...nextCoreWebVitals,
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
    "next-env.d.ts",
  ]),
]);
