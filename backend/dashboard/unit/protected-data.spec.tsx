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
  bindProtectedDataCache, clearProtectedData, observeProtectedData, protectedDataFetch, protectedDataMessages, protectedDataState,
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


it.each(['revoked', 'unavailable'] as const)(
  'refreshes an active protected error for only the recovered bank after %s',
  async (reason) => {
    const bank = 'BK-SAMP0001';
    const otherKey = ['institution-profile', 'BK-SAMP0002'];
    const client = createAuthorityQueryClient();
    const unsubscribe = bindProtectedDataCache(client);
    let restored = false;
    const error = Object.assign(new Error(protectedDataMessages[reason]), {
      code: reason === 'revoked' ? 'bank_key_revoked' : 'bank_key_unavailable',
    });
    function ProtectedRead() {
      const query = useQuery({
        queryKey: ['bi-dashboard', 'tenant', 'authority', bank, 'saved'],
        retry: false,
        queryFn: async () => {
          if (!restored) throw error;
          return 'Restored protected content';
        },
      });
      return <p>{query.error?.message ?? query.data}</p>;
    }
    notify(reason);
    client.setQueryData(otherKey, { name: '🔒 Protected data' });
    const view = render(<QueryClientProvider client={client}><ProtectedRead /></QueryClientProvider>);
    try {
      await screen.findByText(protectedDataMessages[reason]);
      restored = true;
      await act(async () => { notify('available'); });
      expect(await screen.findByText('Restored protected content')).toBeVisible();
      expect(screen.queryByText(protectedDataMessages[reason])).toBeNull();
      expect(client.getQueryData(otherKey)).toEqual({ name: '🔒 Protected data' });
      expect(client.getQueryState(otherKey)?.isInvalidated).toBe(false);
    } finally {
      view.unmount();
      unsubscribe();
      client.clear();
    }
  },
);

it.each([
  { scoped: false, structured: false, reason: 'revoked' },
  { scoped: true, structured: true, reason: 'revoked' },
  { scoped: false, structured: true, reason: 'unavailable' },
  { scoped: true, structured: false, reason: 'unavailable' },
] as const)(
  'refreshes a cached $reason profile on return after another screen recovers (scoped=$scoped, structured=$structured)',
  async ({ scoped, structured, reason }) => {
    const bank = 'BK-SAMP0001';
    const profileKey = scoped
      ? ['institution-profile', 'tenant', 'authority', bank]
      : ['institution-profile', bank];
    const client = createAuthorityQueryClient();
    const unsubscribe = bindProtectedDataCache(client);
    let restored = false;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const profile = String(input).endsWith('/institution-profile');
      return new Response(JSON.stringify(
        profile
          ? restored
            ? { name: 'Restored profile identity' }
            : structured
              ? { report: { _aequoros_protected_data: reason } }
              : { name: '🔒 Protected data' }
          : { ratio: 1.25 },
      ), {
        headers: {
          'Content-Type': 'application/json',
          'X-Aequoros-Protected-Data': JSON.stringify({ [bank]: restored ? 'available' : reason }),
        },
      });
    }));
    function Profile() {
      const query = useQuery({
        queryKey: profileKey,
        queryFn: async () => {
          const response = await protectedDataFetch(`/banks/${bank}/institution-profile`);
          return await response.json() as { name?: string; report?: unknown };
        },
      });
      return <><p>{query.data?.name}</p><ProtectedDataPlaceholder value={query.data?.report} /></>;
    }
    function Batch() {
      const query = useQuery({
        queryKey: ['de-batch', bank, 'synthetic'],
        queryFn: async () => {
          const response = await protectedDataFetch(`/banks/${bank}/ingestion/synthetic`);
          return await response.json() as { ratio: number };
        },
      });
      return <p>{query.data ? `Batch ratio ${query.data.ratio}` : 'Loading batch'}</p>;
    }
    function Screens() {
      const [profile, setProfile] = useState(true);
      return <>
        <ProtectedDataNotice />
        <button onClick={() => setProfile(false)}>Open batch</button>
        <button onClick={() => setProfile(true)}>Open profile</button>
        {profile ? <Profile /> : <Batch />}
      </>;
    }
    const view = render(<QueryClientProvider client={client}><Screens /></QueryClientProvider>);
    try {
      await screen.findByText(structured ? 'Protected data locked' : '🔒 Protected data');
      restored = true;
      await userEvent.click(screen.getByRole('button', { name: 'Open batch' }));
      expect(await screen.findByText('Batch ratio 1.25')).toBeVisible();
      expect(screen.queryByText(protectedDataMessages[reason])).toBeNull();
      await userEvent.click(screen.getByRole('button', { name: 'Open profile' }));
      expect(await screen.findByText('Restored profile identity')).toBeVisible();
      expect(screen.queryByText('Protected data locked')).toBeNull();
      expect(screen.queryByText('🔒 Protected data')).toBeNull();
    } finally {
      view.unmount();
      unsubscribe();
      client.clear();
      vi.unstubAllGlobals();
    }
  },
);
