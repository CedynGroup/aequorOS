'use client';

/**
 * Settings → Authentication (SSO) — admin-only card for the org's own-OIDC
 * connection. The bank's IT registers an app in THEIR IdP (Google Workspace,
 * Entra, Okta, …) with our redirect URI, then enters the issuer / client ID /
 * client secret here. The secret is write-only: it is sealed server-side and
 * never displayed again — the UI only shows whether one is set.
 */

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { ShieldCheck } from 'lucide-react';
import type { SsoConnectionResponse } from '@aequoros/risk-service-api';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import CopyButton from '@/components/ui/CopyButton';
import StatusPill from '@/components/ui/StatusPill';
import { SkeletonLine } from '@/components/ui/Skeleton';
import { authApi, normalizeApiError } from '@/lib/api/client';

const QUERY_KEY = ['settings', 'sso-connection'];
const REQUESTS_KEY = ['settings', 'sso-access-requests'];

interface FormState {
  issuer: string;
  clientId: string;
  clientSecret: string; // blank = keep the stored secret
  domains: string; // comma-separated in the UI
  enabled: boolean;
  jitEnabled: boolean;
}

function toForm(connection: SsoConnectionResponse | null): FormState {
  return {
    issuer: connection?.issuer ?? '',
    clientId: connection?.clientId ?? '',
    clientSecret: '',
    domains: (connection?.allowedEmailDomains ?? []).join(', '),
    enabled: connection?.enabled ?? false,
    jitEnabled: connection?.jitEnabled ?? false,
  };
}

export default function AuthenticationPanel() {
  const { data: session } = useSession();
  const isAdmin = (session?.user?.roles ?? []).includes('admin');
  if (!isAdmin) return null;
  return <AuthenticationPanelInner />;
}

function AuthenticationPanelInner() {
  const queryClient = useQueryClient();
  const connectionQuery = useQuery({
    queryKey: QUERY_KEY,
    queryFn: async () =>
      (await authApi.authGetSsoConnection()) as SsoConnectionResponse | null,
    retry: false,
  });

  const connection = connectionQuery.data ?? null;
  const [form, setForm] = useState<FormState | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // Lazily seed the form from the fetched connection; afterwards user edits win.
  const draft = form ?? toForm(connection);

  const redirectUri = useMemo(
    () =>
      typeof window !== 'undefined'
        ? `${window.location.origin}/api/auth/callback/sso`
        : '/api/auth/callback/sso',
    []
  );

  const save = useMutation({
    mutationFn: async (payload: FormState) =>
      authApi.authPutSsoConnection({
        ssoConnectionUpdateRequest: {
          issuer: payload.issuer.trim(),
          clientId: payload.clientId.trim(),
          clientSecret: payload.clientSecret ? payload.clientSecret : null,
          allowedEmailDomains: payload.domains
            .split(',')
            .map((domain) => domain.trim())
            .filter(Boolean),
          enabled: payload.enabled,
          jitEnabled: payload.jitEnabled,
        },
      }),
    onSuccess: (saved) => {
      queryClient.setQueryData(QUERY_KEY, saved);
      setForm(null); // re-seed from the saved state (clears the secret field)
      setNotice('Saved. Changes apply to new sign-ins within a minute.');
    },
    onError: async (error) => {
      const normalized = await normalizeApiError(error);
      setNotice(normalized.message);
    },
  });

  function update(patch: Partial<FormState>) {
    setNotice(null);
    setForm({ ...draft, ...patch });
  }

  const inputClass =
    'w-full px-3 py-2 border border-border rounded-md bg-surface text-body text-navy font-mono';

  return (
    <Card>
      <CardHeader
        title="Authentication (SSO)"
        subtitle="Sign-in through your institution's identity provider — OpenID Connect"
        action={
          connectionQuery.isLoading ? undefined : (
            <StatusPill tone={connection?.enabled ? 'success' : 'slate'}>
              {connection?.enabled ? 'Enabled' : 'Disabled'}
            </StatusPill>
          )
        }
      />
      <CardBody className="space-y-4">
        {connectionQuery.isLoading ? (
          <div className="space-y-3">
            <SkeletonLine width="70%" />
            <SkeletonLine width="55%" />
            <SkeletonLine width="62%" />
          </div>
        ) : (
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              save.mutate(draft);
            }}
          >
            <div>
              <p className="text-micro font-medium uppercase tracking-wider text-slate">
                Redirect URI — register this in your IdP
              </p>
              <div className="mt-1 flex items-center gap-2">
                <code className="font-mono text-caption text-navy break-all">
                  {redirectUri}
                </code>
                <CopyButton text={redirectUri} label="Redirect URI" className="shrink-0" />
              </div>
            </div>

            <label className="block">
              <span className="block text-caption font-medium text-navy mb-1.5">
                Issuer URL
              </span>
              <input
                type="url"
                required
                placeholder="https://accounts.google.com"
                value={draft.issuer}
                onChange={(e) => update({ issuer: e.target.value })}
                className={inputClass}
              />
            </label>

            <label className="block">
              <span className="block text-caption font-medium text-navy mb-1.5">
                Client ID
              </span>
              <input
                type="text"
                required
                value={draft.clientId}
                onChange={(e) => update({ clientId: e.target.value })}
                className={inputClass}
              />
            </label>

            <label className="block">
              <span className="block text-caption font-medium text-navy mb-1.5">
                Client secret
                {connection?.clientSecretSet && (
                  <span className="ml-2 font-normal text-slate">
                    — one is stored; leave blank to keep it
                  </span>
                )}
              </span>
              <input
                type="password"
                autoComplete="off"
                placeholder={connection?.clientSecretSet ? '••••••••  (unchanged)' : ''}
                value={draft.clientSecret}
                onChange={(e) => update({ clientSecret: e.target.value })}
                className={inputClass}
              />
            </label>

            <label className="block">
              <span className="block text-caption font-medium text-navy mb-1.5">
                Allowed email domains
                <span className="ml-2 font-normal text-slate">
                  — comma-separated; blank allows any (provisioned users only either way)
                </span>
              </span>
              <input
                type="text"
                placeholder="yourbank.com.gh"
                value={draft.domains}
                onChange={(e) => update({ domains: e.target.value })}
                className={inputClass}
              />
            </label>

            <label className="flex items-center gap-2.5 py-1 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={draft.enabled}
                onChange={(e) => update({ enabled: e.target.checked })}
                className="h-4 w-4 accent-teal"
              />
              <span className="text-body text-navy">
                Enable SSO sign-in on the login page
              </span>
            </label>

            <label className="flex items-start gap-2.5 py-1 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={draft.jitEnabled}
                onChange={(e) => update({ jitEnabled: e.target.checked })}
                className="mt-0.5 h-4 w-4 accent-teal"
              />
              <span className="text-body text-navy">
                Let employees request access on first sign-in
                <span className="block text-caption text-slate">
                  A sign-in from an allowed domain records a request below —{' '}
                  <strong>no access is granted</strong> until you approve it with a
                  role. Requires at least one allowed email domain.
                </span>
              </span>
            </label>

            {notice && (
              <p role="status" className="text-caption text-slate">
                {notice}
              </p>
            )}

            <div className="flex items-center gap-3">
              <button
                type="submit"
                disabled={save.isPending}
                className="inline-flex items-center gap-2 px-4 py-2.5 btn-primary font-medium transition-colors disabled:opacity-60"
              >
                <ShieldCheck size={15} aria-hidden />
                {save.isPending ? 'Saving…' : 'Save connection'}
              </button>
              <p className="text-caption text-slate">
                SSO never grants access by itself — accounts are provisioned or
                approved by an administrator.
              </p>
            </div>
          </form>
        )}

        {!connectionQuery.isLoading && <AccessRequests />}
      </CardBody>
    </Card>
  );
}

const ROLE_OPTIONS = ['viewer', 'analyst', 'approver', 'admin'] as const;

/** JIT sign-ins awaiting approval. Approval — with an explicit role — is the
 * authorization act; rejection deletes the never-activated stub. */
function AccessRequests() {
  const queryClient = useQueryClient();
  const requests = useQuery({
    queryKey: REQUESTS_KEY,
    queryFn: () => authApi.authListSsoAccessRequests(),
    refetchInterval: 60_000,
  });
  const [roles, setRoles] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  const refresh = () => queryClient.invalidateQueries({ queryKey: REQUESTS_KEY });
  const approve = useMutation({
    mutationFn: (userId: string) =>
      authApi.authApproveSsoAccessRequest({
        userId,
        ssoAccessRequestApprove: {
          role: (roles[userId] ?? 'viewer') as (typeof ROLE_OPTIONS)[number],
        },
      }),
    onSuccess: refresh,
    onError: async (e) => setError((await normalizeApiError(e)).message),
  });
  const reject = useMutation({
    mutationFn: (userId: string) => authApi.authRejectSsoAccessRequest({ userId }),
    onSuccess: refresh,
    onError: async (e) => setError((await normalizeApiError(e)).message),
  });

  const rows = requests.data ?? [];
  if (requests.isLoading || rows.length === 0) return null;

  return (
    <div className="pt-4 border-t border-border-light">
      <div className="flex items-center gap-2">
        <p className="text-body font-medium text-navy">Access requests</p>
        <StatusPill tone="amber">{rows.length} pending</StatusPill>
      </div>
      <ul className="mt-2 divide-y divide-border-light">
        {rows.map((request) => (
          <li key={request.userId} className="py-2.5 flex items-center gap-3">
            <div className="min-w-0 flex-1">
              <p className="text-body text-navy truncate">
                {request.displayName ?? request.email}
              </p>
              <p className="text-caption text-slate truncate">{request.email}</p>
            </div>
            <select
              aria-label={`Role for ${request.email}`}
              value={roles[request.userId] ?? 'viewer'}
              onChange={(e) =>
                setRoles((prev) => ({ ...prev, [request.userId]: e.target.value }))
              }
              className="px-2 py-1.5 border border-border rounded-md bg-surface text-caption text-navy"
            >
              {ROLE_OPTIONS.map((role) => (
                <option key={role} value={role}>
                  {role}
                </option>
              ))}
            </select>
            <button
              type="button"
              disabled={approve.isPending}
              onClick={() => approve.mutate(request.userId)}
              className="px-3 py-1.5 btn-primary text-caption font-medium disabled:opacity-60"
            >
              Approve
            </button>
            <button
              type="button"
              disabled={reject.isPending}
              onClick={() => reject.mutate(request.userId)}
              className="px-3 py-1.5 border border-border rounded-md text-caption font-medium text-navy hover:bg-surface-muted disabled:opacity-60"
            >
              Reject
            </button>
          </li>
        ))}
      </ul>
      {error && (
        <p role="alert" className="mt-2 text-caption text-danger">
          {error}
        </p>
      )}
    </div>
  );
}
