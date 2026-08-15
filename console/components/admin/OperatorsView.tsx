'use client';

import { useState } from 'react';
import { KeyRound, Plus, UserCheck, UserX } from 'lucide-react';
import {
  createOperator,
  deactivateOperator,
  listOperators,
  reactivateOperator,
  resetOperatorPassword,
  type OperatorUser,
} from '@/lib/api';
import { useApi, useMutation } from '@/lib/use-api';
import { fmtDate, fmtTs, relTime } from '@/lib/format';
import {
  Button,
  Chip,
  type Column,
  DataTable,
  EmptyState,
  Field,
  FormError,
  InfoTip,
  Input,
  Modal,
  PageHeader,
  SectionCard,
  Select,
  SkeletonRows,
} from '@/components/ui';
import { ApiError } from '@/lib/api';
import { AdminBoundary } from './AdminBoundary';
import { RoleBadge } from './RoleBadge';
import { OneTimeSecretModal, type OneTimeSecret } from './OneTimeSecretModal';
import { OPERATOR_ROLES, roleRank } from './util';

type ConfirmKind = 'reset' | 'deactivate' | 'reactivate';
interface ConfirmState {
  kind: ConfirmKind;
  op: OperatorUser;
}

const BLANK_FORM = { email: '', display_name: '', role: 'developer' };

export default function OperatorsView() {
  const { data, error, loading, reload } = useApi(listOperators);

  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState(BLANK_FORM);
  const [formErr, setFormErr] = useState<{ email?: string; display_name?: string }>({});
  const [secret, setSecret] = useState<OneTimeSecret | null>(null);
  const [confirm, setConfirm] = useState<ConfirmState | null>(null);

  const createM = useMutation(createOperator, {
    errorContext: 'Create operator',
    successMessage: (r) => `Operator ${r.email} created`,
    onSuccess: (r) => {
      setCreateOpen(false);
      setForm(BLANK_FORM);
      setFormErr({});
      if (r.one_time_password) {
        setSecret({ email: r.email, password: r.one_time_password, title: 'Operator created' });
      }
      reload();
    },
  });

  const resetM = useMutation(resetOperatorPassword, {
    errorContext: 'Reset password',
    successMessage: 'One-time password minted',
    onSuccess: (r) => {
      setConfirm(null);
      setSecret({ email: r.email, password: r.one_time_password, title: 'Password reset' });
      reload();
    },
  });

  const deactivateM = useMutation(deactivateOperator, {
    errorContext: 'Deactivate operator',
    successMessage: (o) => `${o.email} deactivated`,
    onSuccess: () => {
      setConfirm(null);
      reload();
    },
  });

  const reactivateM = useMutation(reactivateOperator, {
    errorContext: 'Reactivate operator',
    successMessage: (o) => `${o.email} reactivated`,
    onSuccess: () => {
      setConfirm(null);
      reload();
    },
  });

  function openCreate() {
    setForm(BLANK_FORM);
    setFormErr({});
    createM.reset();
    setCreateOpen(true);
  }

  function submitCreate() {
    const email = form.email.trim();
    const displayName = form.display_name.trim();
    const errs: { email?: string; display_name?: string } = {};
    if (!email) errs.email = 'Email is required.';
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) errs.email = 'Enter a valid email address.';
    if (!displayName) errs.display_name = 'Display name is required.';
    setFormErr(errs);
    if (Object.keys(errs).length > 0) return;
    void createM.mutate({ email, display_name: displayName, role: form.role });
  }

  const columns: Column<OperatorUser>[] = [
    {
      key: 'operator',
      header: 'Operator',
      sortable: true,
      sortAccessor: (o) => o.email,
      render: (o) => (
        <div className="min-w-0">
          <div className="truncate font-medium text-navy">
            {o.display_name || o.email.split('@')[0]}
          </div>
          <div className="truncate font-mono text-caption text-slate">{o.email}</div>
        </div>
      ),
    },
    {
      key: 'role',
      header: 'Role',
      sortable: true,
      sortAccessor: (o) => roleRank(o.role),
      render: (o) => <RoleBadge role={o.role} />,
    },
    {
      key: 'auth',
      header: 'Auth',
      render: (o) => <Chip mono>{o.auth_provider}</Chip>,
    },
    {
      key: 'status',
      header: 'Status',
      sortable: true,
      sortAccessor: (o) => (o.is_active ? 1 : 0),
      render: (o) =>
        o.is_active ? <Chip tone="ok">active</Chip> : <Chip tone="warn">inactive</Chip>,
    },
    {
      key: 'last_login',
      header: 'Last login',
      sortable: true,
      sortAccessor: (o) => o.last_login_at ?? '',
      render: (o) => (
        <span className="text-slate" title={fmtTs(o.last_login_at)}>
          {relTime(o.last_login_at)}
        </span>
      ),
    },
    {
      key: 'created',
      header: 'Created',
      sortable: true,
      sortAccessor: (o) => o.created_at,
      render: (o) => <span className="text-slate">{fmtDate(o.created_at)}</span>,
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      render: (o) => (
        <div className="flex items-center justify-end gap-1.5">
          <Button
            size="sm"
            variant="secondary"
            icon={<KeyRound size={13} aria-hidden />}
            onClick={() => setConfirm({ kind: 'reset', op: o })}
          >
            Reset
          </Button>
          {o.is_active ? (
            <Button
              size="sm"
              variant="ghost"
              icon={<UserX size={13} aria-hidden />}
              onClick={() => setConfirm({ kind: 'deactivate', op: o })}
            >
              Deactivate
            </Button>
          ) : (
            <Button
              size="sm"
              variant="ghost"
              icon={<UserCheck size={13} aria-hidden />}
              onClick={() => setConfirm({ kind: 'reactivate', op: o })}
            >
              Reactivate
            </Button>
          )}
        </div>
      ),
    },
  ];

  const activeRoleHint = OPERATOR_ROLES.find((r) => r.value === form.role)?.hint;

  return (
    <div>
      <PageHeader
        breadcrumbs={[{ label: 'Admin' }, { label: 'Operators' }]}
        title="Operators"
        subtitle="Global staff identities — roles, one-time passwords, and activation."
        action={
          <Button icon={<Plus size={15} aria-hidden />} onClick={openCreate}>
            Create operator
          </Button>
        }
      />

      <SectionCard
        title={
          <span className="inline-flex items-center gap-1.5">
            Staff operators
            <InfoTip label="About operator roles" width="w-80">
              Roles form a ladder: <strong>developer &lt; operator_admin &lt; super_admin</strong>.
              You can only create or manage operators at or below your own rank — the API returns 403
              otherwise, surfaced here. Governance is four-eyes by email: the operator who acts and
              the operator who approves must be different accounts, identified by their address.
            </InfoTip>
          </span>
        }
        subtitle={
          data
            ? `${data.total} operator${data.total === 1 ? '' : 's'} · global operator_users, separate from tenant identity`
            : undefined
        }
        noPadding
      >
        <AdminBoundary
          loading={loading}
          error={error}
          onRetry={reload}
          surface="Operator management"
          skeleton={<SkeletonRows rows={6} />}
        >
          {data && data.operators.length === 0 ? (
            <EmptyState
              Icon={UserCheck}
              title="No operators yet"
              description="Create the first staff operator — they receive a one-time password to sign in with."
              action={
                <Button icon={<Plus size={15} aria-hidden />} onClick={openCreate}>
                  Create operator
                </Button>
              }
            />
          ) : (
            <DataTable
              columns={columns}
              rows={data?.operators ?? []}
              getFilterText={(o) => `${o.email} ${o.display_name ?? ''} ${o.role} ${o.auth_provider}`}
              filterPlaceholder="Filter operators by email, name, or role…"
              initialSort={{ key: 'role', dir: 'desc' }}
            />
          )}
        </AdminBoundary>
      </SectionCard>

      {/* Create operator */}
      <Modal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="Create operator"
        description="A global staff identity. The one-time password is shown once on success."
        size="md"
        footer={
          <>
            <Button variant="secondary" onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button onClick={submitCreate} loading={createM.loading}>
              Create operator
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Email" required error={formErr.email} htmlFor="op-email">
            <Input
              id="op-email"
              type="email"
              autoComplete="off"
              placeholder="analyst@aequoros.com"
              value={form.email}
              invalid={Boolean(formErr.email)}
              onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))}
            />
          </Field>
          <Field label="Display name" required error={formErr.display_name} htmlFor="op-name">
            <Input
              id="op-name"
              autoComplete="off"
              placeholder="Ama Analyst"
              value={form.display_name}
              invalid={Boolean(formErr.display_name)}
              onChange={(e) => setForm((f) => ({ ...f, display_name: e.target.value }))}
            />
          </Field>
          <Field label="Role" required hint={activeRoleHint} htmlFor="op-role">
            <Select
              id="op-role"
              value={form.role}
              onChange={(e) => setForm((f) => ({ ...f, role: e.target.value }))}
            >
              {OPERATOR_ROLES.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </Select>
          </Field>
          {createM.error && (
            <FormError>
              {createM.error.code} · {createM.error.message}
            </FormError>
          )}
        </div>
      </Modal>

      {/* Reset / (de)activate confirmation */}
      <ConfirmModal
        confirm={confirm}
        onClose={() => setConfirm(null)}
        resetM={resetM}
        deactivateM={deactivateM}
        reactivateM={reactivateM}
      />

      {/* One-time password reveal (create + reset) */}
      <OneTimeSecretModal secret={secret} onClose={() => setSecret(null)} />
    </div>
  );
}

/** The subset of a useMutation handle ConfirmModal needs (structurally typed). */
interface Mutatable {
  mutate: (email: string) => Promise<unknown>;
  loading: boolean;
  error: ApiError | null;
}

function ConfirmModal({
  confirm,
  onClose,
  resetM,
  deactivateM,
  reactivateM,
}: {
  confirm: ConfirmState | null;
  onClose: () => void;
  resetM: Mutatable;
  deactivateM: Mutatable;
  reactivateM: Mutatable;
}) {
  if (!confirm) return null;

  const email = confirm.op.email;
  const meta = {
    reset: {
      title: 'Reset password',
      body: `Mint a new one-time password for ${email}? Their current password stops working immediately, and the new one is shown only once.`,
      cta: 'Reset password',
      variant: 'primary' as const,
      mutation: resetM,
    },
    deactivate: {
      title: 'Deactivate operator',
      body: `Disable sign-in for ${email}? They keep their role but cannot authenticate until reactivated.`,
      cta: 'Deactivate',
      variant: 'danger' as const,
      mutation: deactivateM,
    },
    reactivate: {
      title: 'Reactivate operator',
      body: `Re-enable sign-in for ${email}?`,
      cta: 'Reactivate',
      variant: 'primary' as const,
      mutation: reactivateM,
    },
  }[confirm.kind];

  return (
    <Modal
      open
      onClose={onClose}
      title={meta.title}
      size="sm"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant={meta.variant}
            loading={meta.mutation.loading}
            onClick={() => void meta.mutation.mutate(email)}
          >
            {meta.cta}
          </Button>
        </>
      }
    >
      <p className="text-body text-slate">{meta.body}</p>
      {meta.mutation.error && (
        <FormError>
          {meta.mutation.error.code} · {meta.mutation.error.message}
        </FormError>
      )}
    </Modal>
  );
}
