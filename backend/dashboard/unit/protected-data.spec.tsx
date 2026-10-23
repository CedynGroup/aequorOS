// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { createAuthorityQueryClient } from '../lib/api/QueryAuthorityBoundary';
import ProtectedDataNotice from '../components/ui/ProtectedDataNotice';
import ProtectedDataPlaceholder from '../components/ui/ProtectedDataPlaceholder';
import {
  clearProtectedData, observeProtectedData, protectedDataMessages, protectedDataState,
} from '../lib/api/protectedData';

vi.mock('../components/shell/BankContext', () => ({
  useBankContext: () => ({ bank: { id: 'BK-SAMP0001' } }),
}));

afterEach(() => { cleanup(); clearProtectedData(); vi.useRealTimers(); });

function notify(state: string, bank = 'BK-SAMP0001') {
  observeProtectedData(new Response(null, {
    headers: { 'X-Aequoros-Protected-Data': JSON.stringify({ [bank]: state }) },
  }));
}

it.each(['revoked', 'unavailable', 'support'] as const)('shows the %s report placeholder', reason => {
  render(<ProtectedDataPlaceholder value={{ _aequoros_protected_data: reason }} />);
  expect(screen.getByText('Protected data locked')).toBeVisible();
  expect(screen.getByText(protectedDataMessages[reason])).toBeVisible();
});

it('keeps unrelated numeric replies from clearing a bank lock', () => {
  notify('revoked');
  observeProtectedData(new Response(JSON.stringify({ ratio: 1.25 })));
  notify('available', 'BK-SAMP0002');
  expect(protectedDataState('BK-SAMP0001')).toBe('revoked');
});

it('retries a locked read automatically and restores the visible value', async () => {
  vi.useFakeTimers();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  let available = false;
  function ProtectedRead() {
    const query = useQuery({ queryKey: ['synthetic-identity'], queryFn: async () => {
      notify(available ? 'available' : 'unavailable');
      return available ? 'Synthetic Borrower' : '🔒 Protected data';
    } });
    return <p>{query.data}</p>;
  }
  render(<QueryClientProvider client={client}><ProtectedDataNotice /><ProtectedRead /></QueryClientProvider>);
  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  expect(screen.getByText(protectedDataMessages.unavailable)).toBeVisible();
  expect(screen.getByText('🔒 Protected data')).toBeVisible();
  available = true;
  await act(async () => { await vi.advanceTimersByTimeAsync(5_100); });
  expect(screen.getByText('Synthetic Borrower')).toBeVisible();
  expect(screen.queryByText(protectedDataMessages.unavailable)).toBeNull();
  client.clear();
});

it('offers manual retry after revocation', async () => {
  const client = new QueryClient();
  let reads = 0;
  function ProtectedRead() {
    useQuery({ queryKey: ['manual-retry'], queryFn: async () => { reads++; return '🔒 Protected data'; } });
    return null;
  }
  render(<QueryClientProvider client={client}><ProtectedDataNotice /><ProtectedRead /></QueryClientProvider>);
  await act(async () => { notify('revoked'); });
  const before = reads;
  await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(reads).toBeGreaterThan(before);
  client.clear();
});

it('discards decrypted cache data when a protected refetch is refused', async () => {
  const client = createAuthorityQueryClient();
  const queryKey = ['saved-protected-canvas'];
  await client.fetchQuery({ queryKey, queryFn: async () => 'Synthetic private identity' });
  const refused = Object.assign(new Error(protectedDataMessages.revoked), { code: 'bank_key_revoked' });
  await expect(client.fetchQuery({ queryKey, staleTime: 0, retry: false, queryFn: async () => { throw refused; } })).rejects.toThrow();
  expect(client.getQueryData(queryKey)).toBeUndefined();
  client.clear();
});
