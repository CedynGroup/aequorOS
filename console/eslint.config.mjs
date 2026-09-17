import { defineConfig, globalIgnores } from 'eslint/config';
import nextCoreWebVitals from 'eslint-config-next/core-web-vitals';

export default defineConfig([
  ...nextCoreWebVitals,
  {
    // These React Compiler diagnostics need a dedicated cleanup before they
    // can become part of the console's first lint gate.
    rules: {
      'react-hooks/purity': 'off',
      'react-hooks/refs': 'off',
      'react-hooks/set-state-in-effect': 'off',
    },
  },
  globalIgnores(['.next/**', '.next-*/**', '.test-out/**', 'next-env.d.ts']),
]);
