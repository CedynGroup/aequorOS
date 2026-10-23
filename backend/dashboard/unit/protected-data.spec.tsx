// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { afterEach, expect, it, vi } from 'vitest';
import { useState } from 'react';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { createAuthorityQueryClient } from '../lib/api/QueryAuthorityBoundary';
import ProtectedDataNotice from '../components/ui/ProtectedDataNotice';
import QueryBoundary from '../components/ui/QueryBoundary';
import ProtectedDataPlaceholder from '../components/ui/ProtectedDataPlaceholder';
import {
  bindProtectedDataCache, clearProtectedData, observeProtectedData, protectedDataMessages, protectedDataState,
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

it.each(['revoked', 'unavailable', 'support'] as const)(
  'purges sibling bank caches on %s without losing an editor draft',
  async (reason) => {
    const client = createAuthorityQueryClient();
    const unsubscribe = bindProtectedDataCache(client);
    const bank = 'BK-SAMP0001';
    const legacyKey = ['institution-profile', bank];
    const scopedKey = [
      'bi-dashboard',
      'tenant',
      'authority',
      bank,
      'dashboard',
    ];
    client.setQueryData(legacyKey, { name: 'Synthetic private identity' });
    client.setQueryData(scopedKey, { title: 'Synthetic identity canvas' });
    client.setQueryData(['institution-profile', 'BK-SAMP0002'], {
      name: 'Other bank identity',
    });
    let finishRead: ((value: { name: string }) => void) | undefined;
    function Editor() {
      const [draft, setDraft] = useState('');
      return (
        <input
          aria-label="Draft"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
        />
      );
    }
    function Profile() {
      const query = useQuery({
        queryKey: legacyKey,
        queryFn: async () => {
          return new Promise<{ name: string }>((resolve) => {
            finishRead = resolve;
          });
        },
      });
      return (
        <QueryBoundary
          isLoading={query.isLoading}
          error={query.error}
          skeleton={<p>Loading profile</p>}
        >
          <p>{query.data?.name}</p>
          <Editor />
        </QueryBoundary>
      );
    }
    render(
      <QueryClientProvider client={client}>
        <ProtectedDataNotice />
        <Profile />
      </QueryClientProvider>,
    );
    await userEvent.type(screen.getByLabelText('Draft'), 'Unsaved draft');
    await act(async () => {
      notify(reason);
    });
    expect(client.getQueryData(legacyKey)).toBeUndefined();
    expect(client.getQueryData(scopedKey)).toBeUndefined();
    expect(client.getQueryData(['institution-profile', 'BK-SAMP0002'])).toEqual(
      { name: 'Other bank identity' },
    );
    await waitFor(() => expect(screen.getByText('Synthetic private identity')).not.toBeVisible());
    await act(async () => {
      finishRead?.({ name: '🔒 Protected data' });
    });
    await waitFor(() => expect(screen.getByLabelText('Draft')).toBeVisible());
    expect(screen.getByLabelText('Draft')).toHaveValue('Unsaved draft');
    if (reason !== 'support') {
      await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
      await act(async () => {
        notify('available');
        finishRead?.({ name: 'Restored identity' });
      });
      await waitFor(() =>
        expect(screen.getByText('Restored identity')).toBeVisible(),
      );
      expect(screen.getByLabelText('Draft')).toHaveValue('Unsaved draft');
    }
    unsubscribe();
    client.clear();
  },
);

it('waits for the first successful query before mounting its content', () => {
  function Content({ value }: { value?: { name: string } }) {
    return <p>{value!.name}</p>;
  }
  const view = render(
    <QueryBoundary isLoading error={null} skeleton={<p>Loading</p>}>
      <Content />
    </QueryBoundary>,
  );
  expect(screen.getByText('Loading')).toBeVisible();
  view.rerender(
    <QueryBoundary isLoading={false} error={null}>
      <Content value={{ name: 'Ready' }} />
    </QueryBoundary>,
  );
  expect(screen.getByText('Ready')).toBeVisible();
});


it('prevents an in-flight stale read from restoring decrypted bank data after a lock', async () => {
  const client = createAuthorityQueryClient();
  const unsubscribe = bindProtectedDataCache(client);
  const queryKey = ['institution-profile', 'BK-SAMP0001'];
  await client.fetchQuery({ queryKey, queryFn: async () => 'Cached private identity' });
  let finishRead: ((value: string) => void) | undefined;
  const pending = client.fetchQuery({ queryKey, staleTime: 0, queryFn: async () => {
    return new Promise<string>(resolve => { finishRead = resolve; });
  } }).catch(() => undefined);
  notify('revoked');
  finishRead?.('Stale private identity');
  await pending;
  expect(client.getQueryData(queryKey)).toBeUndefined();
  await client.fetchQuery({ queryKey, queryFn: async () => '🔒 Protected data' });
  expect(client.getQueryData(queryKey)).toBe('🔒 Protected data');
  unsubscribe();
  client.clear();
});
