// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import RelatedPartiesPage from '../app/(app)/institution/parties/page';
import ProtectedDataNotice from '../components/ui/ProtectedDataNotice';
import { createAuthorityQueryClient } from '../lib/api/QueryAuthorityBoundary';
import { QueryAuthorityScopeProvider } from '../lib/api/useQueryScope';
import { useDeactivateBiAlert } from '../lib/api/bi';
import { setAccessToken } from '../lib/api/token';
import {
  bindProtectedDataCache,
  clearProtectedData,
  observeProtectedData,
  protectedDataMessages,
  protectedDataState,
} from '../lib/api/protectedData';

const BANK = 'BK-SAMP0001';
const PARTY = '00000000-0000-4000-8000-000000000017';
const profileKey = ['institution-profile', BANK];
const disposals: (() => void)[] = [];

vi.mock('../components/shell/BankContext', () => ({
  useBankContext: () => ({ bank: { id: 'BK-SAMP0001', currency: 'GHS' } }),
}));

afterEach(() => {
  cleanup();
  disposals.splice(0).forEach((dispose) => dispose());
  clearProtectedData();
  setAccessToken(null);
  vi.unstubAllGlobals();
});

function party(name = 'Synthetic owner') {
  return {
    id: PARTY,
    bank_id: BANK,
    full_name: name,
    party_type: 'individual',
    status: 'active',
    contact: {},
    roles: [],
    shareholdings: [],
    regulated_elsewhere: false,
    regulated_jurisdiction: null,
    created_at: '2026-06-30T00:00:00Z',
    updated_at: '2026-06-30T00:00:00Z',
  };
}

function profileResponse(state = 'available', name = 'Synthetic owner') {
  return new Response(
    JSON.stringify({
      bank_id: BANK,
      profile: null,
      related_parties: [
        party(state === 'available' ? name : '🔒 Protected data'),
      ],
      name_history: [],
      outlets: [],
      licenses: [],
      products: [],
    }),
    {
      headers: {
        'Content-Type': 'application/json',
        'X-Aequoros-Protected-Data': JSON.stringify({ [BANK]: state }),
      },
    },
  );
}

function refusal(state: 'revoked' | 'unavailable') {
  return new Response(
    JSON.stringify({
      error: {
        code: state === 'revoked' ? 'bank_key_revoked' : 'bank_key_unavailable',
        message: protectedDataMessages[state],
      },
    }),
    { status: 503, headers: { 'Content-Type': 'application/json' } },
  );
}

function mount(children: ReactNode) {
  setAccessToken('synthetic-session');
  const client = createAuthorityQueryClient();
  const unsubscribe = bindProtectedDataCache(client);
  disposals.push(() => {
    unsubscribe();
    client.clear();
  });
  render(
    <QueryAuthorityScopeProvider
      scope={{ tenantId: 'tenant', authorityId: 'actor' }}
    >
      <QueryClientProvider client={client}>
        <ProtectedDataNotice />
        {children}
      </QueryClientProvider>
    </QueryAuthorityScopeProvider>,
  );
  return client;
}

it.each(['party', 'shareholding'] as const)(
  'preserves a conditional existing-party %s editor through cache purge and recovery',
  async (editor) => {
    let state = 'available';
    let holdRead = false;
    let finishRead: ((response: Response) => void) | undefined;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        holdRead
          ? new Promise<Response>((resolve) => {
              finishRead = resolve;
            })
          : profileResponse(state),
      ),
    );
    const client = mount(<RelatedPartiesPage />);
    await userEvent.click(await screen.findByText('Synthetic owner'));
    await userEvent.click(
      screen.getByRole('button', {
        name: editor === 'party' ? 'Edit' : 'Add shareholding',
        exact: true,
      }),
    );
    const label = editor === 'party' ? /Full name/ : /Share type/;
    await userEvent.clear(screen.getByLabelText(label));
    await userEvent.type(screen.getByLabelText(label), 'Unsaved draft');
    holdRead = true;
    state = 'revoked';
    await act(async () => {
      await observeProtectedData(profileResponse(state));
    });
    expect(client.getQueryData(profileKey)).toBeUndefined();
    await waitFor(() => expect(finishRead).toBeTypeOf('function'));
    await act(async () => {
      finishRead?.(profileResponse(state));
    });
    await waitFor(() => expect(screen.getByLabelText(label)).toBeVisible());
    expect(screen.getByLabelText(label)).toHaveValue('Unsaved draft');
    expect(screen.queryByText('Synthetic owner')).toBeNull();
    holdRead = false;
    state = 'available';
    await userEvent.click(
      screen.getByRole('button', { name: 'Retry', exact: true }),
    );
    await waitFor(() => expect(protectedDataState(BANK)).toBe('available'));
    expect(screen.getByLabelText(label)).toHaveValue('Unsaved draft');
  },
);

it.each(['revoked', 'unavailable'] as const)(
  'learns %s from a generated-client mutation refusal and keeps the party draft',
  async (reason) => {
    let state = 'available';
    let name = 'Synthetic owner';
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === 'PUT') {
          if (state !== 'available') return refusal(reason);
          const payload = JSON.parse(String(init.body)) as {
            full_name: string;
          };
          name = payload.full_name;
          return new Response(JSON.stringify(party(name)), {
            headers: { 'Content-Type': 'application/json' },
          });
        }
        return profileResponse(state, name);
      }),
    );
    const client = mount(<RelatedPartiesPage />);
    client.setQueryData(['de-batch', BANK, 'other'], {
      name: 'Cached private owner',
    });
    await userEvent.click(await screen.findByText('Synthetic owner'));
    await userEvent.click(
      screen.getByRole('button', { name: 'Edit', exact: true }),
    );
    await userEvent.clear(screen.getByLabelText(/Full name/));
    await userEvent.type(
      screen.getByLabelText(/Full name/),
      'Unsaved new name',
    );
    await userEvent.type(
      screen.getByLabelText(/Reason for change/),
      'Synthetic edit',
    );
    state = reason;
    await userEvent.click(
      screen.getByRole('button', { name: 'Save party', exact: true }),
    );
    await waitFor(() => expect(protectedDataState(BANK)).toBe(reason));
    await waitFor(() =>
      expect(screen.getByLabelText(/Full name/)).toBeVisible(),
    );
    expect(client.getQueryData(['de-batch', BANK, 'other'])).toBeUndefined();
    expect(screen.getByLabelText(/Full name/)).toHaveValue('Unsaved new name');
    expect(
      screen.getAllByText(protectedDataMessages[reason]).length,
    ).toBeGreaterThan(0);
    state = 'available';
    await userEvent.click(
      screen.getByRole('button', { name: 'Retry', exact: true }),
    );
    await waitFor(() => expect(protectedDataState(BANK)).toBe('available'));
    await userEvent.click(
      screen.getByRole('button', { name: 'Save party', exact: true }),
    );
    await waitFor(() =>
      expect(screen.queryByLabelText(/Full name/)).toBeNull(),
    );
    expect(screen.getAllByText('Unsaved new name').length).toBeGreaterThan(0);
  },
);

function StopAlert() {
  const mutation = useDeactivateBiAlert(BANK);
  return (
    <button
      onClick={() =>
        mutation.mutate({ alertId: PARTY, reason: 'Synthetic edit' })
      }
    >
      Stop alert
    </button>
  );
}

it.each(['revoked', 'unavailable'] as const)(
  'learns %s from the BI notification client and purges sibling plaintext',
  async (reason) => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => refusal(reason)),
    );
    const client = mount(<StopAlert />);
    client.setQueryData(profileKey, { name: 'Cached private owner' });
    await userEvent.click(screen.getByRole('button', { name: 'Stop alert' }));
    await waitFor(() => expect(protectedDataState(BANK)).toBe(reason));
    expect(client.getQueryData(profileKey)).toBeUndefined();
    expect(screen.getByText(protectedDataMessages[reason])).toBeVisible();
  },
);
