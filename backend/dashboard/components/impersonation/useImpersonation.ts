'use client';

import { useSyncExternalStore } from 'react';
import {
  getImpersonationSnapshot,
  getImpersonationServerSnapshot,
  subscribeImpersonation,
  type ImpersonationState,
} from '@/lib/api/impersonation';

/**
 * Read the current act-as-examiner state. Backed by the framework-free store in
 * lib/api/impersonation, so it stays in lock-step with the API-client bearer
 * selector. On a normal (non-impersonation) session this is a stable IDLE value
 * and never causes a re-render.
 */
export function useImpersonation(): ImpersonationState {
  return useSyncExternalStore(
    subscribeImpersonation,
    getImpersonationSnapshot,
    getImpersonationServerSnapshot,
  );
}
