'use client';

import { useState } from 'react';
import { AlertTriangle, Pencil, Plus, ScrollText, ShieldCheck } from 'lucide-react';
import {
  approveRegulatoryParameter,
  listRegulatoryParameters,
  proposeRegulatoryParameter,
  type RegulatoryParameter,
} from '@/lib/api';
import { useApi, useMutation } from '@/lib/use-api';
import { fmtDate, DASH } from '@/lib/format';
import {
  Button,
  Chip,
  type Column,
  DataTable,
  EmptyState,
  ErrorPanel,
  Field,
  FieldRow,
  FormError,
  InfoTip,
  Input,
  Modal,
  PageHeader,
  SectionCard,
  Select,
  SkeletonRows,
  Textarea,
} from '@/components/ui';
import { CeremonyBanner } from '@/components/curves';
import { AdminBoundary } from './AdminBoundary';

/**
 * /admin/regulatory-parameters — the regulatory-parameter control plane
 * (docs/sdi.md §7 Phase C): the global, class/type-keyed, effective-dated
 * source of truth for every regulatory number an engine reads (CAR floors,
 * exposure limits, paid-up floors, the 8 LMTD liquidity floors, provisioning
 * rates, risk-weight buckets …).
 *
 * Governance is four-eyes: an operator PROPOSES a new effective-dated
 * generation (lands as `draft`, invisible to the resolver) and a DIFFERENT
 * operator APPROVES it — the API 422s if the approver is the proposer. A
 * `pending` confirmation status marks a documented default still awaiting BoG
 * confirmation and is surfaced prominently.
 *
 * Cloned from OperatorsView (table + create modal + client validation) with the
 * amber maker-checker ceremony of the desk methodology register.
 */

type ScopeType = RegulatoryParameter['scope_type'];
type ConfirmationStatus = RegulatoryParameter['confirmation_status'];

interface ProposeForm {
  scope_type: ScopeType;
  scope_key: string;
  param_code: string;
  jurisdiction_code: string;
  value_numeric: string;
  unit: string;
  source_citation: string;
  confirmation_status: ConfirmationStatus;
  effective_from: string;
  change_rationale: string;
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function blankForm(): ProposeForm {
  return {
    scope_type: 'institution_class',
    scope_key: '',
    param_code: '',
    jurisdiction_code: 'GH',
    value_numeric: '',
    unit: '',
    source_citation: '',
    confirmation_status: 'pending',
    effective_from: today(),
    change_rationale: '',
  };
}

type FormErrors = Partial<Record<keyof ProposeForm, string>>;

/** confirmed = a BoG-verified value · pending = a documented default awaiting confirmation. */
function ConfirmationBadge({ status }: { status: ConfirmationStatus }) {
  return status === 'confirmed' ? (
    <Chip tone="ok">confirmed</Chip>
  ) : (
    <Chip tone="warn" title="A documented default awaiting BoG confirmation">
      pending BoG
    </Chip>
  );
}

export default function RegulatoryParametersView() {
  const { data, error, loading, reload } = useApi(() => listRegulatoryParameters());

  const [proposeOpen, setProposeOpen] = useState(false);
  const [form, setForm] = useState<ProposeForm>(blankForm);
  const [formErr, setFormErr] = useState<FormErrors>({});
  const [approveFor, setApproveFor] = useState<RegulatoryParameter | null>(null);
  const [approveRationale, setApproveRationale] = useState('');

  const proposeM = useMutation(proposeRegulatoryParameter, {
    errorContext: 'Propose parameter',
    successMessage: (r) => `Proposed ${r.param_code} for ${r.scope_key} (draft)`,
    onSuccess: () => {
      setProposeOpen(false);
      setForm(blankForm());
      setFormErr({});
      reload();
    },
  });

  const approveM = useMutation(
    (id: string, change_rationale?: string) =>
      approveRegulatoryParameter(id, { change_rationale }),
    {
      errorContext: 'Approve parameter',
      successMessage: (r) => `Approved ${r.param_code} for ${r.scope_key}`,
      onSuccess: () => {
        setApproveFor(null);
        setApproveRationale('');
        reload();
      },
    },
  );

  function openBlankPropose() {
    setForm(blankForm());
    setFormErr({});
    proposeM.reset();
    setProposeOpen(true);
  }

  // Prefill the propose form from an existing row — proposing a NEW generation
  // of the same parameter (the operator sets a new value / effective date).
  function openProposeFrom(row: RegulatoryParameter) {
    setForm({
      scope_type: row.scope_type,
      scope_key: row.scope_key,
      param_code: row.param_code,
      jurisdiction_code: row.jurisdiction_code,
      value_numeric: row.value_numeric ?? '',
      unit: row.unit,
      source_citation: row.source_citation,
      confirmation_status: row.confirmation_status,
      effective_from: today(),
      change_rationale: '',
    });
    setFormErr({});
    proposeM.reset();
    setProposeOpen(true);
  }

  function validate(f: ProposeForm): FormErrors {
    const errs: FormErrors = {};
    if (!f.scope_key.trim()) errs.scope_key = 'Scope key is required.';
    if (!f.param_code.trim()) errs.param_code = 'Parameter code is required.';
    if (!f.jurisdiction_code.trim()) errs.jurisdiction_code = 'Jurisdiction is required.';
    const value = f.value_numeric.trim();
    if (!value) errs.value_numeric = 'A value is required.';
    else if (!Number.isFinite(Number(value))) errs.value_numeric = 'Value must be a number.';
    if (!f.unit.trim()) errs.unit = 'A unit is required.';
    if (!f.source_citation.trim()) errs.source_citation = 'A source citation is required.';
    if (!f.effective_from) errs.effective_from = 'An effective date is required.';
    if (!f.change_rationale.trim()) errs.change_rationale = 'A rationale is required.';
    return errs;
  }

  function submitPropose() {
    const errs = validate(form);
    setFormErr(errs);
    if (Object.keys(errs).length > 0) return;
    void proposeM.mutate({
      scope_type: form.scope_type,
      scope_key: form.scope_key.trim(),
      param_code: form.param_code.trim(),
      jurisdiction_code: form.jurisdiction_code.trim(),
      value_numeric: form.value_numeric.trim(),
      unit: form.unit.trim(),
      source_citation: form.source_citation.trim(),
      confirmation_status: form.confirmation_status,
      effective_from: form.effective_from,
      change_rationale: form.change_rationale.trim(),
    });
  }

  function openApprove(row: RegulatoryParameter) {
    setApproveFor(row);
    setApproveRationale('');
    approveM.reset();
  }

  const approveDualControl =
    approveM.error !== null &&
    (approveM.error.status === 409 || approveM.error.status === 422) &&
    /proposer/i.test(approveM.error.message);

  const rows = data?.parameters ?? [];
  const pendingCount = rows.filter((r) => r.confirmation_status === 'pending').length;
  const draftCount = rows.filter((r) => r.status === 'draft').length;

  const columns: Column<RegulatoryParameter>[] = [
    {
      key: 'param_code',
      header: 'Parameter',
      sortable: true,
      sortAccessor: (r) => r.param_code,
      render: (r) => (
        <div className="min-w-0">
          <div className="truncate font-mono text-caption text-navy">{r.param_code}</div>
          <div
            className="max-w-[22rem] truncate text-micro text-slate"
            title={r.source_citation}
          >
            {r.source_citation}
          </div>
        </div>
      ),
    },
    {
      key: 'scope',
      header: 'Scope',
      sortable: true,
      sortAccessor: (r) => `${r.scope_type} ${r.scope_key}`,
      render: (r) => (
        <div className="min-w-0">
          <Chip mono>{r.scope_key}</Chip>
          <div className="mt-0.5 text-micro text-slate">{r.scope_type.replace(/_/g, ' ')}</div>
        </div>
      ),
    },
    {
      key: 'value',
      header: 'Value',
      render: (r) => (
        <span className="font-mono text-caption text-ink">
          {r.value_numeric ?? (r.value_json ? 'structured' : DASH)}
          {r.value_numeric !== null && <span className="ml-1 text-slate">{r.unit}</span>}
        </span>
      ),
    },
    {
      key: 'confirmation',
      header: 'Confirmation',
      sortable: true,
      sortAccessor: (r) => r.confirmation_status,
      render: (r) => <ConfirmationBadge status={r.confirmation_status} />,
    },
    {
      key: 'effective',
      header: 'Effective',
      sortable: true,
      sortAccessor: (r) => r.effective_from,
      render: (r) => (
        <span className="text-caption text-ink">
          {fmtDate(r.effective_from)}
          {r.effective_to && (
            <span className="text-slate"> → {fmtDate(r.effective_to)}</span>
          )}
        </span>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      sortable: true,
      sortAccessor: (r) => r.status,
      render: (r) =>
        r.status === 'approved' ? (
          <Chip tone="ok" title={r.approved_by ? `approved by ${r.approved_by}` : undefined}>
            approved
          </Chip>
        ) : (
          <Chip tone="warn">draft</Chip>
        ),
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      render: (r) =>
        r.status === 'draft' ? (
          <Button
            size="sm"
            variant="secondary"
            icon={<ShieldCheck size={13} className="text-warning" aria-hidden />}
            className="border-warning/60 bg-warning-light text-warning hover:bg-warning-light"
            onClick={() => openApprove(r)}
          >
            Approve…
          </Button>
        ) : (
          <Button
            size="sm"
            variant="secondary"
            icon={<Pencil size={13} aria-hidden />}
            onClick={() => openProposeFrom(r)}
          >
            Propose change
          </Button>
        ),
    },
  ];

  return (
    <div>
      <PageHeader
        breadcrumbs={[{ label: 'Admin' }, { label: 'Regulatory parameters' }]}
        title="Regulatory parameters"
        subtitle="The class/type-keyed control plane the calculation engines read. Changes are four-eyes and effective-dated — never edited in code."
        action={
          <Button icon={<Plus size={15} aria-hidden />} onClick={openBlankPropose}>
            Propose parameter
          </Button>
        }
      />

      <SectionCard
        title={
          <span className="inline-flex items-center gap-1.5">
            Parameter register
            <InfoTip label="About the control plane" width="w-80">
              Every regulatory number is DATA here, not a code literal. Proposing drafts a new
              effective-dated generation; a <strong>different</strong> operator must approve it
              (four-eyes) before the resolver sees it. A <strong>pending BoG</strong> value is a
              documented default in force until BoG confirms it.
            </InfoTip>
          </span>
        }
        subtitle={
          data ? (
            <span className="inline-flex flex-wrap items-center gap-2">
              <span>
                {data.total} generation{data.total === 1 ? '' : 's'} · global, not tenant-scoped
              </span>
              {pendingCount > 0 && (
                <Chip tone="warn">{pendingCount} pending BoG confirmation</Chip>
              )}
              {draftCount > 0 && <Chip tone="accent">{draftCount} draft awaiting approval</Chip>}
            </span>
          ) : undefined
        }
        noPadding
      >
        <AdminBoundary
          loading={loading}
          error={error}
          onRetry={reload}
          surface="Regulatory parameters"
          skeleton={<SkeletonRows rows={8} />}
        >
          {data && rows.length === 0 ? (
            <EmptyState
              Icon={ScrollText}
              title="No parameters yet"
              description="Propose the first regulatory parameter. It lands as a draft that a second operator must approve before any engine reads it."
              action={
                <Button icon={<Plus size={15} aria-hidden />} onClick={openBlankPropose}>
                  Propose parameter
                </Button>
              }
            />
          ) : (
            <DataTable
              columns={columns}
              rows={rows}
              getFilterText={(r) =>
                `${r.param_code} ${r.scope_type} ${r.scope_key} ${r.unit} ${r.source_citation} ${r.status} ${r.confirmation_status}`
              }
              filterPlaceholder="Filter by code, scope, unit, or citation…"
              initialSort={{ key: 'param_code', dir: 'asc' }}
              pageSize={25}
            />
          )}
        </AdminBoundary>
      </SectionCard>

      {/* -------------------------------------------------- propose (maker) */}
      <Modal
        open={proposeOpen}
        onClose={() => setProposeOpen(false)}
        size="lg"
        title="Propose parameter change (Track: maker)"
        description="Drafts a new effective-dated generation. A second operator must approve it before the resolver sees it."
        footer={
          <>
            <Button variant="secondary" onClick={() => setProposeOpen(false)}>
              Cancel
            </Button>
            <Button
              type="submit"
              form="regparam-propose-form"
              loading={proposeM.loading}
            >
              Propose (draft)
            </Button>
          </>
        }
      >
        <form
          id="regparam-propose-form"
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            submitPropose();
          }}
        >
          <CeremonyBanner>
            <p className="font-medium text-navy">
              Regulatory-parameter change — four-eyes, effective-dated
            </p>
            <p className="mt-1">
              This drafts a new generation with a documented rationale; it is invisible to the
              calculation engines until a different operator approves it. Prior generations are
              never edited — the successor supersedes them from its effective date.
            </p>
          </CeremonyBanner>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Scope type" required htmlFor="rp-scope-type">
              <Select
                id="rp-scope-type"
                value={form.scope_type}
                onChange={(e) =>
                  setForm((f) => ({ ...f, scope_type: e.target.value as ScopeType }))
                }
              >
                <option value="institution_class">institution_class (bank / sdi)</option>
                <option value="institution_type">institution_type (licence code)</option>
              </Select>
            </Field>
            <Field
              label="Scope key"
              required
              error={formErr.scope_key}
              hint="e.g. bank, sdi, or a licence code like savings_and_loans."
              htmlFor="rp-scope-key"
            >
              <Input
                id="rp-scope-key"
                autoComplete="off"
                placeholder="sdi"
                value={form.scope_key}
                invalid={Boolean(formErr.scope_key)}
                onChange={(e) => setForm((f) => ({ ...f, scope_key: e.target.value }))}
              />
            </Field>
            <Field label="Parameter code" required error={formErr.param_code} htmlFor="rp-code">
              <Input
                id="rp-code"
                autoComplete="off"
                className="font-mono"
                placeholder="car_min"
                value={form.param_code}
                invalid={Boolean(formErr.param_code)}
                onChange={(e) => setForm((f) => ({ ...f, param_code: e.target.value }))}
              />
            </Field>
            <Field
              label="Jurisdiction"
              required
              error={formErr.jurisdiction_code}
              htmlFor="rp-jur"
            >
              <Input
                id="rp-jur"
                autoComplete="off"
                className="font-mono uppercase"
                maxLength={8}
                value={form.jurisdiction_code}
                invalid={Boolean(formErr.jurisdiction_code)}
                onChange={(e) =>
                  setForm((f) => ({ ...f, jurisdiction_code: e.target.value.toUpperCase() }))
                }
              />
            </Field>
            <Field label="Value" required error={formErr.value_numeric} htmlFor="rp-value">
              <Input
                id="rp-value"
                autoComplete="off"
                inputMode="decimal"
                className="font-mono"
                placeholder="10"
                value={form.value_numeric}
                invalid={Boolean(formErr.value_numeric)}
                onChange={(e) => setForm((f) => ({ ...f, value_numeric: e.target.value }))}
              />
            </Field>
            <Field
              label="Unit"
              required
              error={formErr.unit}
              hint="e.g. pct, ratio, GHS_m."
              htmlFor="rp-unit"
            >
              <Input
                id="rp-unit"
                autoComplete="off"
                placeholder="pct"
                value={form.unit}
                invalid={Boolean(formErr.unit)}
                onChange={(e) => setForm((f) => ({ ...f, unit: e.target.value }))}
              />
            </Field>
            <Field
              label="Effective from"
              required
              error={formErr.effective_from}
              htmlFor="rp-effective"
            >
              <Input
                id="rp-effective"
                type="date"
                value={form.effective_from}
                invalid={Boolean(formErr.effective_from)}
                onChange={(e) => setForm((f) => ({ ...f, effective_from: e.target.value }))}
              />
            </Field>
            <Field label="Confirmation status" required htmlFor="rp-confirmation">
              <Select
                id="rp-confirmation"
                value={form.confirmation_status}
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    confirmation_status: e.target.value as ConfirmationStatus,
                  }))
                }
              >
                <option value="pending">pending — default awaiting BoG confirmation</option>
                <option value="confirmed">confirmed — verified against a BoG source</option>
              </Select>
            </Field>
          </div>

          <Field
            label="Source citation"
            required
            error={formErr.source_citation}
            hint="The regulation / notice the value comes from — the audit evidence."
            htmlFor="rp-citation"
          >
            <Input
              id="rp-citation"
              autoComplete="off"
              placeholder="Act 930 s.29 / NBFI r.11"
              value={form.source_citation}
              invalid={Boolean(formErr.source_citation)}
              onChange={(e) => setForm((f) => ({ ...f, source_citation: e.target.value }))}
            />
          </Field>
          <Field label="Change rationale" required error={formErr.change_rationale}>
            <Textarea
              rows={3}
              value={form.change_rationale}
              invalid={Boolean(formErr.change_rationale)}
              onChange={(e) => setForm((f) => ({ ...f, change_rationale: e.target.value }))}
              placeholder="Why is this value changing? Cite the confirmation or regulation change."
            />
          </Field>

          {proposeM.error && <ErrorPanel error={proposeM.error} context="Proposing the parameter" />}
        </form>
      </Modal>

      {/* ------------------------------------------------- approve (checker) */}
      <Modal
        open={approveFor !== null}
        onClose={() => setApproveFor(null)}
        size="md"
        title="Approve parameter (Track: checker)"
        description={
          approveFor ? (
            <span>
              <span className="font-mono">{approveFor.param_code}</span> ·{' '}
              {approveFor.scope_key} · effective {fmtDate(approveFor.effective_from)}
            </span>
          ) : undefined
        }
        footer={
          <>
            <Button variant="secondary" onClick={() => setApproveFor(null)}>
              Cancel
            </Button>
            <Button
              loading={approveM.loading}
              onClick={() => {
                if (!approveFor) return;
                void approveM.mutate(approveFor.id, approveRationale.trim() || undefined);
              }}
            >
              Approve
            </Button>
          </>
        }
      >
        {approveFor && (
          <div className="space-y-4">
            <CeremonyBanner>
              <p className="font-medium text-navy">Four-eyes approval</p>
              <p className="mt-1">
                Approving makes this the governing value from its effective date and supersedes the
                prior generation. Dual control applies: the proposer cannot approve their own row.
              </p>
            </CeremonyBanner>

            <div className="rounded border border-border-light bg-surface p-3">
              <div className="grid gap-x-10 sm:grid-cols-2">
                <FieldRow label="Value">
                  <span className="font-mono">
                    {approveFor.value_numeric ?? DASH} {approveFor.unit}
                  </span>
                </FieldRow>
                <FieldRow label="Confirmation">
                  <ConfirmationBadge status={approveFor.confirmation_status} />
                </FieldRow>
                <FieldRow label="Proposed by">
                  <span className="font-mono text-caption">{approveFor.proposed_by}</span>
                </FieldRow>
                <FieldRow label="Source">
                  <span className="text-caption">{approveFor.source_citation}</span>
                </FieldRow>
              </div>
              {approveFor.change_rationale && (
                <p className="mt-2 border-t border-border-light pt-2 text-caption text-slate">
                  <span className="font-medium text-ink">Rationale:</span>{' '}
                  {approveFor.change_rationale}
                </p>
              )}
            </div>

            <Field
              label="Approval note (optional)"
              hint="Recorded on the row and in the operator audit log."
            >
              <Textarea
                rows={2}
                value={approveRationale}
                onChange={(e) => setApproveRationale(e.target.value)}
                placeholder="Optional note explaining the approval."
              />
            </Field>

            {approveM.error && approveDualControl && (
              <div className="flex items-start gap-2 rounded border border-warning/50 bg-warning-light p-3">
                <ShieldCheck size={14} className="mt-0.5 shrink-0 text-warning" aria-hidden />
                <div className="min-w-0">
                  <p className="text-body font-medium text-navy">
                    Dual control: the proposer cannot approve their own parameter — sign in as a
                    second operator.
                  </p>
                  <p className="mt-1 text-caption text-slate">
                    API said: “{approveM.error.message}”
                  </p>
                </div>
              </div>
            )}
            {approveM.error && !approveDualControl && (
              <div className="flex items-start gap-2 rounded border border-critical/40 bg-critical-light p-3">
                <AlertTriangle size={14} className="mt-0.5 shrink-0 text-critical" aria-hidden />
                <FormError>
                  {approveM.error.code} · {approveM.error.message}
                </FormError>
              </div>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}
