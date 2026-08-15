'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { ArrowUpRight, Eye, Plus, ShieldAlert, XCircle } from 'lucide-react';
import {
  endInspectorSession,
  listInspectorSessions,
  listTenants,
  startInspectorSession,
  type InspectorMode,
  type InspectorSession,
} from '@/lib/api';
import { useApi, useMutation } from '@/lib/use-api';
import { useInspector } from '@/lib/inspector';
import { DASH, fmtTimestamp, fmtTs, relTime } from '@/lib/format';
import {
  Button,
  Chip,
  type Column,
  DataTable,
  Field,
  FormError,
  InfoTip,
  Input,
  KpiStat,
  MonoId,
  Modal,
  PageHeader,
  SectionCard,
  Select,
  SkeletonRows,
  StatusPill,
  Textarea,
} from '@/components/ui';
import { AdminBoundary } from './AdminBoundary';
import { isSessionLive } from './util';

interface StartForm {
  organization_id: string;
  reason: string;
  mode: InspectorMode;
  ttl_minutes: number;
}

const BLANK_FORM: StartForm = {
  organization_id: '',
  reason: '',
  mode: 'consent',
  ttl_minutes: 30,
};

export default function InspectorView() {
  const { active, setActive, refresh } = useInspector();

  const [activeOnly, setActiveOnly] = useState(false);
  const [orgFilter, setOrgFilter] = useState('');
  const [startOpen, setStartOpen] = useState(false);
  const [form, setForm] = useState<StartForm>(BLANK_FORM);
  const [formErr, setFormErr] = useState<Partial<Record<keyof StartForm, string>>>({});

  // A 1s tick so expiry countdowns and live/expired state stay current.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const tenants = useApi(listTenants);
  const sessions = useApi(
    () =>
      listInspectorSessions({
        active: activeOnly ? true : undefined,
        organizationId: orgFilter || undefined,
      }),
    [activeOnly, orgFilter],
  );

  const orgName = useMemo(() => {
    const map = new Map<string, string>();
    for (const t of tenants.data?.tenants ?? []) map.set(t.organization_id, t.organization_name);
    return map;
  }, [tenants.data]);

  const startM = useMutation(startInspectorSession, {
    errorContext: 'Start inspection',
    successMessage: (s) => `Inspecting ${s.organization_id}`,
    onSuccess: (s) => {
      setActive(s);
      setStartOpen(false);
      setForm(BLANK_FORM);
      setFormErr({});
      sessions.reload();
      refresh();
    },
  });

  const endM = useMutation(endInspectorSession, {
    errorContext: 'End session',
    successMessage: 'Inspection ended',
    onSuccess: (_result, sessionId) => {
      if (active && active.session_id === sessionId) setActive(null);
      sessions.reload();
      refresh();
    },
  });

  function openStart() {
    setForm(BLANK_FORM);
    setFormErr({});
    startM.reset();
    setStartOpen(true);
  }

  function submitStart() {
    const errs: Partial<Record<keyof StartForm, string>> = {};
    if (!form.organization_id) errs.organization_id = 'Choose a tenant to inspect.';
    if (!form.reason.trim()) errs.reason = 'A reason is required and is written to the audit log.';
    if (!Number.isFinite(form.ttl_minutes) || form.ttl_minutes < 15 || form.ttl_minutes > 60) {
      errs.ttl_minutes = 'TTL must be between 15 and 60 minutes.';
    }
    setFormErr(errs);
    if (Object.keys(errs).length > 0) return;
    void startM.mutate({
      organization_id: form.organization_id,
      reason: form.reason.trim(),
      mode: form.mode,
      ttl_minutes: form.ttl_minutes,
    });
  }

  const rows = sessions.data?.sessions ?? [];
  const liveCount = rows.filter((s) => isSessionLive(s.expires_at, now)).length;
  const breakGlassLive = rows.filter(
    (s) => isSessionLive(s.expires_at, now) && s.mode === 'break_glass',
  ).length;

  const hasTenants = (tenants.data?.tenants.length ?? 0) > 0;

  const columns: Column<InspectorSession>[] = [
    {
      key: 'org',
      header: 'Organization',
      sortable: true,
      sortAccessor: (s) => orgName.get(s.organization_id) ?? s.organization_id,
      render: (s) => (
        <div className="min-w-0">
          <div className="truncate text-navy">{orgName.get(s.organization_id) ?? 'Unknown org'}</div>
          <MonoId id={s.organization_id} />
        </div>
      ),
    },
    {
      key: 'mode',
      header: 'Mode',
      render: (s) =>
        s.mode === 'break_glass' ? (
          <StatusPill tone="breach">break-glass</StatusPill>
        ) : (
          <StatusPill tone="approaching">consent</StatusPill>
        ),
    },
    {
      key: 'state',
      header: 'State',
      sortable: true,
      sortAccessor: (s) => (isSessionLive(s.expires_at, now) ? 1 : 0),
      render: (s) =>
        isSessionLive(s.expires_at, now) ? (
          <Chip tone="ok">active</Chip>
        ) : (
          <Chip>expired</Chip>
        ),
    },
    {
      key: 'started_by',
      header: 'Started by',
      sortable: true,
      sortAccessor: (s) => s.started_by,
      render: (s) => <span className="text-ink">{s.started_by}</span>,
    },
    {
      key: 'started',
      header: 'Started',
      sortable: true,
      sortAccessor: (s) => s.started_at,
      render: (s) => (
        <span className="font-mono text-caption text-slate" title={fmtTs(s.started_at)}>
          {fmtTimestamp(s.started_at)}
        </span>
      ),
    },
    {
      key: 'expires',
      header: 'Expires',
      sortable: true,
      sortAccessor: (s) => s.expires_at,
      render: (s) => {
        const live = isSessionLive(s.expires_at, now);
        return (
          <span className={live ? 'text-ink' : 'text-slate-light'} title={fmtTs(s.expires_at)}>
            {relTime(s.expires_at)}
          </span>
        );
      },
    },
    {
      key: 'reason',
      header: 'Reason',
      render: (s) => (
        <span className="block max-w-[16rem] truncate text-slate" title={s.reason}>
          {s.reason || DASH}
        </span>
      ),
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      render: (s) => (
        <div className="flex items-center justify-end gap-1.5">
          <Link
            href={`/tenants/${s.organization_id}`}
            className="inline-flex items-center gap-1 rounded border border-border px-2.5 py-1 text-caption font-medium text-ink transition-colors hover:bg-surface"
          >
            View tenant
            <ArrowUpRight size={13} aria-hidden />
          </Link>
          {isSessionLive(s.expires_at, now) && (
            <Button
              size="sm"
              variant="ghost"
              icon={<XCircle size={13} aria-hidden />}
              loading={endM.loading}
              onClick={() => void endM.mutate(s.session_id)}
            >
              End
            </Button>
          )}
        </div>
      ),
    },
  ];

  return (
    <div>
      <PageHeader
        breadcrumbs={[{ label: 'Admin' }, { label: 'Tenant Inspector' }]}
        title="Tenant Inspector"
        subtitle="Time-boxed, audited cross-tenant inspection sessions."
        action={
          <Button icon={<Plus size={15} aria-hidden />} onClick={openStart}>
            Start session
          </Button>
        }
      />

      {/* Prominent what-this-is-not explainer */}
      <div className="mb-4 flex items-start gap-3 rounded-lg border border-action/30 bg-action-light px-4 py-3">
        <Eye size={18} className="mt-0.5 shrink-0 text-action" aria-hidden />
        <div className="text-caption text-navy">
          <p className="font-medium">Read-only, audited, time-boxed inspection.</p>
          <p className="mt-0.5 text-slate">
            Inspection lets staff view a tenant&rsquo;s data to diagnose an issue. It does{' '}
            <strong>not</strong> sign you in as a tenant user and is not act-as-user. Every session
            is recorded in the operator audit log, carries a required reason, and self-expires. While
            one is active, an un-dismissable banner shows across the console until you end it.
          </p>
        </div>
      </div>

      <div className="mb-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
        <KpiStat label="Active sessions" value={liveCount} status={liveCount > 0 ? 'warn' : 'ok'} />
        <KpiStat
          label="Break-glass active"
          value={breakGlassLive}
          status={breakGlassLive > 0 ? 'crit' : 'ok'}
        />
        <KpiStat label="Sessions shown" value={rows.length} />
      </div>

      {active && (
        <div className="mb-4 flex items-center gap-2 rounded-md border border-warning/30 bg-warning-light px-3 py-2 text-caption text-warning">
          <ShieldAlert size={14} className="shrink-0" aria-hidden />
          You have an active inspection on <span className="font-mono">{active.organization_id}</span>{' '}
          — the banner above stays until you end it.
        </div>
      )}

      <SectionCard
        title={
          <span className="inline-flex items-center gap-1.5">
            Inspection sessions
            <InfoTip label="About inspection sessions" width="w-80">
              All inspection is read-only. Consent sessions view tenant data with the tenant's
              knowledge; break-glass is emergency access without consent and requires the
              operator_admin role — the API returns 403 otherwise, surfaced here. Sessions self-expire
              at their TTL; state is derived from the expiry (the log is append-only).
            </InfoTip>
          </span>
        }
        actions={
          <>
            <div className="w-36">
              <Select
                aria-label="Session state filter"
                value={activeOnly ? 'active' : 'all'}
                onChange={(e) => setActiveOnly(e.target.value === 'active')}
              >
                <option value="all">All sessions</option>
                <option value="active">Active only</option>
              </Select>
            </div>
            <div className="w-52">
              <Select
                aria-label="Organization filter"
                value={orgFilter}
                onChange={(e) => setOrgFilter(e.target.value)}
                disabled={!hasTenants}
              >
                <option value="">All organizations</option>
                {(tenants.data?.tenants ?? []).map((t) => (
                  <option key={t.organization_id} value={t.organization_id}>
                    {t.organization_name}
                  </option>
                ))}
              </Select>
            </div>
          </>
        }
        noPadding
      >
        <AdminBoundary
          loading={sessions.loading}
          error={sessions.error}
          onRetry={sessions.reload}
          surface="Tenant Inspector"
          skeleton={<SkeletonRows rows={6} />}
        >
          <DataTable
            columns={columns}
            rows={rows}
            initialSort={{ key: 'started', dir: 'desc' }}
            emptyMessage={
              activeOnly || orgFilter
                ? 'No sessions match these filters.'
                : 'No inspection sessions yet. Start one to view a tenant read-only.'
            }
          />
        </AdminBoundary>
      </SectionCard>

      {/* Start session */}
      <Modal
        open={startOpen}
        onClose={() => setStartOpen(false)}
        title="Start inspection session"
        description="Open a time-boxed, audited read-only session against one tenant."
        size="md"
        footer={
          <>
            <Button variant="secondary" onClick={() => setStartOpen(false)}>
              Cancel
            </Button>
            <Button onClick={submitStart} loading={startM.loading}>
              Start session
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field
            label="Organization"
            required
            error={formErr.organization_id}
            htmlFor="insp-org"
          >
            {hasTenants ? (
              <Select
                id="insp-org"
                value={form.organization_id}
                invalid={Boolean(formErr.organization_id)}
                onChange={(e) => setForm((f) => ({ ...f, organization_id: e.target.value }))}
              >
                <option value="">Select a tenant…</option>
                {(tenants.data?.tenants ?? []).map((t) => (
                  <option key={t.organization_id} value={t.organization_id}>
                    {t.organization_name} · {t.organization_id}
                  </option>
                ))}
              </Select>
            ) : (
              <Input
                id="insp-org"
                placeholder="OR-XXXXXXXX"
                value={form.organization_id}
                invalid={Boolean(formErr.organization_id)}
                onChange={(e) => setForm((f) => ({ ...f, organization_id: e.target.value }))}
              />
            )}
          </Field>

          <Field
            label="Reason"
            required
            error={formErr.reason}
            hint="Written verbatim to the audit log and shown on the active-session banner."
            htmlFor="insp-reason"
          >
            <Textarea
              id="insp-reason"
              rows={2}
              placeholder="Investigating ticket #1234 — LCR figures look wrong after last ingestion."
              value={form.reason}
              invalid={Boolean(formErr.reason)}
              onChange={(e) => setForm((f) => ({ ...f, reason: e.target.value }))}
            />
          </Field>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field
              label="Mode"
              required
              hint={
                form.mode === 'break_glass'
                  ? 'Break-glass — requires operator_admin.'
                  : 'Consent — read-only.'
              }
              htmlFor="insp-mode"
            >
              <Select
                id="insp-mode"
                value={form.mode}
                onChange={(e) =>
                  setForm((f) => ({ ...f, mode: e.target.value as InspectorMode }))
                }
              >
                <option value="consent">Consent — with tenant knowledge</option>
                <option value="break_glass">Break-glass — emergency, admin</option>
              </Select>
            </Field>

            <Field label="TTL (minutes)" required error={formErr.ttl_minutes} htmlFor="insp-ttl">
              <Input
                id="insp-ttl"
                type="number"
                min={15}
                max={60}
                step={5}
                value={String(form.ttl_minutes)}
                invalid={Boolean(formErr.ttl_minutes)}
                onChange={(e) => setForm((f) => ({ ...f, ttl_minutes: Number(e.target.value) }))}
              />
            </Field>
          </div>

          {form.mode === 'break_glass' && (
            <div className="flex items-start gap-2 rounded-md border border-critical/30 bg-critical-light px-3 py-2 text-caption text-critical">
              <ShieldAlert size={14} className="mt-0.5 shrink-0" aria-hidden />
              Break-glass grants emergency read access without tenant consent and is reserved for
              operator_admin. The request is refused (403) if your role is insufficient.
            </div>
          )}

          {startM.error && (
            <FormError>
              {startM.error.code} · {startM.error.message}
            </FormError>
          )}
        </div>
      </Modal>
    </div>
  );
}
