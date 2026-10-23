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
export function observeProtectedData(response: Response) {
  const raw = response.headers.get('X-Aequoros-Protected-Data');
  if (!raw) return;
  try {
    const payload: unknown = JSON.parse(raw);
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
