import type { QueryClient, QueryKey } from '@tanstack/react-query';

/** Bank-scoped disclosure notices; an unrelated numeric response cannot clear a lock. */
export type ProtectedDataState = 'available' | 'revoked' | 'unavailable' | 'support';
export const protectedDataMessages = {
  revoked: 'Your bank has turned off aequorOS access to protected data. Contact your bank administrator.',
  unavailable: "Can't reach your bank's key service. Retrying.",
  support: 'Protected data is hidden during staff support access.',
} as const;

const notices = new Map<string, ProtectedDataState>();
const listeners = new Set<() => void>();
export function subscribeProtectedData(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
export function protectedDataState(bankId: string): ProtectedDataState {
  return notices.get(bankId) ?? 'available';
}
export function clearProtectedData() {
  notices.clear();
  listeners.forEach(listener => listener());
}
export async function observeProtectedData(response: Response, url?: string) {
  const raw = response.headers.get('X-Aequoros-Protected-Data');
  try {
    let payload: unknown;
    if (raw) {
      payload = JSON.parse(raw);
    } else {
      if (response.ok) return;
      const bank = /\/banks\/([^/?#]+)/.exec(url ?? response.url)?.[1];
      if (!bank) return;
      const body: unknown = await response.clone().json();
      if (!body || typeof body !== 'object' || !('error' in body)) return;
      const error = body.error;
      if (!error || typeof error !== 'object' || !('code' in error)) return;
      const state = error.code === 'bank_key_revoked' ? 'revoked'
        : error.code === 'bank_key_unavailable' ? 'unavailable'
        : error.code === 'bank_protected_support' ? 'support' : null;
      if (!state) return;
      payload = { [decodeURIComponent(bank)]: state };
    }
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return;
    let changed = false;
    for (const [bank, state] of Object.entries(payload)) {
      if (!['available', 'revoked', 'unavailable', 'support'].includes(state as string)) continue;
      if (notices.get(bank) !== state) {
        notices.set(bank, state as ProtectedDataState);
        changed = true;
      }
    }
    if (changed) listeners.forEach(listener => listener());
  } catch { /* A malformed notice must not interrupt the original response. */ }
}

export function isLockedBankQuery(queryKey: QueryKey): boolean {
  return queryKey.some(
    (part) =>
      typeof part === 'string' && protectedDataState(part) !== 'available',
  );
}

export function bindProtectedDataCache(client: QueryClient): () => void {
  return subscribeProtectedData(() => {
    void client.cancelQueries({ predicate: query => isLockedBankQuery(query.queryKey) }, { revert: false });
    for (const query of client.getQueryCache().getAll()) {
      if (!isLockedBankQuery(query.queryKey)) continue;
      query.setState({
        data: undefined,
        dataUpdatedAt: 0,
        isInvalidated: true,
        status: 'pending',
        error: null,
      });
    }
    void client.refetchQueries({
      type: 'active',
      predicate: (query) =>
        isLockedBankQuery(query.queryKey) && query.state.isInvalidated,
    });
  });
}

export function isProtectedDataError(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false;
  const record = error as Record<string, unknown>;
  return ['bank_key_revoked', 'bank_key_unavailable', 'bank_protected_support']
    .includes(String(record.errorCode ?? record.code ?? ''));
}

export function containsLockedData(value: unknown): boolean {
  if (value === '🔒 Protected data') return true;
  if (!value || typeof value !== 'object') return false;
  if ('_aequoros_protected_data' in value) return true;
  return Object.values(value).some(containsLockedData);
}
