'use client';

import { useMemo, useState } from 'react';
import { AlertTriangle, History, Pencil, Plus, ScrollText, ShieldCheck } from 'lucide-react';
import {
  approveRegulatoryParameter,
  getWorkforceSession,
  listRegulatoryParameters,
  proposeRegulatoryParameter,
  shapeErrorOf,
  type RegulatoryParameter,
} from '@/lib/api';
import { useApi, useMutation } from '@/lib/use-api';
import { fmtDate, fmtTs, DASH } from '@/lib/format';
import {
  approvalEligibility,
  buildChains,
  confirmationLabel,
  displayValue,
  formatStructuredValue,
  isProposalValid,
  lifecycleOf,
  parseStructuredValue,
  scopeTypeLabel,
  shapeName,
  validateProposal,
  valueMode,
  type ParameterChain,
  type ProposeForm,
  type ProposeFormErrors,
  type ValueMode,
} from '@/lib/regulatory-parameters';
import {
  Button,
  Chip,
  type Column,
  DataTable,
  Drawer,
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
 * /admin/regulatory-parameters — the regulatory-parameter control plane.
 *
 * The global, scope-keyed, effective-dated source of truth for every regulatory
 * number the calculation engines read (capital floors, exposure limits, paid-up
 * floors, liquidity floors, provisioning rates …). Held as DATA so a number can
 * be corrected, cited, and dated without a deploy.
 *
 * Four capabilities, one screen:
 *  - BROWSE the register as effective-dated chains, with the citation and the
 *    confirmation state on the row rather than behind a detail view;
 *  - PROPOSE a new generation (maker) — lands as a proposal no engine reads;
 *  - APPROVE from a dedicated queue (checker) — a DIFFERENT operator only;
 *  - HISTORY per parameter + scope, so a superseded value is visibly superseded.
 *
 * Dual control is enforced by the operator API on every call
 * (`regulatory_parameters.approve` 422s when the approver is the proposer). The
 * eligibility shown here is advisory-only: it exists so the operator learns they
 * proposed the row from the row itself instead of from a server error, and it
 * never grants anything — an unknown viewer is allowed to attempt and let the
 * server decide.
 *
 * No regulatory VALUE is ever prefilled, defaulted, or suggested anywhere on
 * this screen: an empty field is correct, a plausible wrong number is a filing
 * risk. Absent values fail closed as "Not set" — never as 0.
 */

type ScopeType = RegulatoryParameter['scope_type'];
type ConfirmationStatus = RegulatoryParameter['confirmation_status'];

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

/**
 * A blank proposal. Every regulatory field starts EMPTY — including the
 * jurisdiction, which is part of the parameter's identity and must be a
 * decision, not a default (a defaulted jurisdiction is how one country's floors
 * get filed under another).
 */
function blankForm(): ProposeForm {
  return {
    scope_type: 'institution_class',
    scope_key: '',
    param_code: '',
    jurisdiction_code: '',
    value_numeric: '',
    value_json: '',
    unit: '',
    source_citation: '',
    confirmation_status: 'pending',
    effective_from: today(),
    change_rationale: '',
  };
}

/**
 * A table-valued parameter, rendered read-only.
 *
 * The console has always shown a structural row as the words "Structured
 * value", which is honest but useless: an operator could not see what an SDI's
 * risk-weight composition or an ICAAP band table actually said without reading
 * the database. This renders the body itself — as a table when its form is one
 * this screen recognises, and as pretty JSON otherwise, which is never wrong,
 * only plainer.
 */
function StructuredValuePreview({ body }: { body: Record<string, unknown> }) {
  const bands = Array.isArray(body.bands) ? (body.bands as Record<string, unknown>[]) : null;

  if (bands && bands.length > 0 && bands.every((b) => b && typeof b === 'object')) {
    const columns = [...new Set(bands.flatMap((band) => Object.keys(band)))];
    return (
      <div className="overflow-x-auto">
        <table className="w-full text-caption">
          <thead>
            <tr className="border-b border-hair text-slate">
              {columns.map((column) => (
                <th key={column} className="px-2 py-1 text-left font-medium">
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {bands.map((band, index) => (
              <tr key={index} className="border-b border-hair/60">
                {columns.map((column) => (
                  <td key={column} className="px-2 py-1 font-mono text-ink">
                    {band[column] === null || band[column] === undefined
                      ? DASH
                      : String(band[column])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <pre className="max-h-80 overflow-auto rounded bg-surface p-3 font-mono text-caption text-ink">
      {JSON.stringify(body, null, 2)}
    </pre>
  );
}

/** Confirmation state, worded for a reader who has never seen the enum. */
function ConfirmationBadge({ status }: { status: ConfirmationStatus }) {
  const { label, tone, detail } = confirmationLabel(status);
  return (
    <Chip tone={tone} title={detail}>
      {label}
    </Chip>
  );
}

/** Where one generation sits on its chain (in force / scheduled / superseded). */
function LifecycleBadge({ row, asOf }: { row: RegulatoryParameter; asOf: string }) {
  const life = lifecycleOf(row, asOf);
  return (
    <Chip tone={life.tone} title={life.detail}>
      {life.label}
    </Chip>
  );
}

/** The value as the API sent it — a string, never parsed. Absence is explicit. */
function ValueCell({ row }: { row: RegulatoryParameter }) {
  const shown = displayValue(row);
  if (!shown.isSet) {
    return (
      <Chip tone="warn" title="This record carries no value. Nothing is being applied.">
        Not set
      </Chip>
    );
  }
  if (row.value_json !== null) {
    // A structural row's body is shown in full in the history drawer; here the
    // form's own name is more useful than a truncated table.
    return (
      <Chip tone="neutral" title={JSON.stringify(row.value_json)}>
        {shapeName(row.param_code) ?? 'Structured value'}
      </Chip>
    );
  }
  return (
    <span className="font-mono text-caption text-ink">
      {shown.text}
      {shown.unit && <span className="ml-1 text-slate">{shown.unit}</span>}
    </span>
  );
}

export default function RegulatoryParametersView() {
  const asOf = today();

  // --- filters (server-side where the API supports them) -------------------
  const [scopeType, setScopeType] = useState<'' | ScopeType>('');
  const [paramCode, setParamCode] = useState('');
  const [confirmation, setConfirmation] = useState<'' | ConfirmationStatus>('');
  const [includeDrafts, setIncludeDrafts] = useState(true);
  const [jurisdiction, setJurisdiction] = useState('');

  const { data, error, loading, reload } = useApi(
    () =>
      listRegulatoryParameters({
        scopeType: scopeType || undefined,
        paramCode: paramCode.trim() || undefined,
        confirmationStatus: confirmation || undefined,
        includeDrafts,
      }),
    [scopeType, paramCode, confirmation, includeDrafts],
  );

  // Who is signed in — needed only to SHOW the four-eyes rule. Null under the
  // local dev-token session, which carries no identity; the UI then says so
  // rather than guessing.
  const session = useApi(() => getWorkforceSession(), []);
  const viewerEmail = session.data?.email ?? null;

  const [proposeOpen, setProposeOpen] = useState(false);
  const [form, setForm] = useState<ProposeForm>(blankForm);
  const [formErr, setFormErr] = useState<ProposeFormErrors>({});
  /**
   * Which value arm the form is showing.
   *
   * For a REGISTERED code the declared shape decides and this is ignored
   * (D-037) — a band table cannot be typed into the number field and a
   * percentage cannot be pasted as JSON. For an unregistered code there is no
   * declared shape, so the operator chooses; the default stays the number
   * field the console has always shown.
   */
  const [valueArm, setValueArm] = useState<ValueMode>('scalar');
  const mode = valueMode(form.param_code.trim(), valueArm);
  const declaredShape = shapeName(form.param_code.trim());
  const structuredPreview = parseStructuredValue(form.value_json);
  const [approveFor, setApproveFor] = useState<RegulatoryParameter | null>(null);
  const [approveNote, setApproveNote] = useState('');
  const [historyFor, setHistoryFor] = useState<ParameterChain | null>(null);

  const proposeM = useMutation(proposeRegulatoryParameter, {
    errorContext: 'Propose parameter',
    successMessage: (r) => `Proposed ${r.param_code} for ${r.scope_key} — awaiting approval`,
    onSuccess: () => {
      setProposeOpen(false);
      setForm(blankForm());
      setFormErr({});
      reload();
    },
  });

  /**
   * The server's own shape refusal, shown against the path IT named.
   *
   * The client mirror can disagree with the server — it is a mirror, and a
   * mirror can be out of date. When it does, the server's sentence is the one
   * displayed, because the server is what decides whether the row is written.
   */
  const serverShapeError = shapeErrorOf(proposeM.error);

  const approveM = useMutation(
    (id: string, change_rationale?: string) =>
      approveRegulatoryParameter(id, { change_rationale }),
    {
      errorContext: 'Approve parameter',
      successMessage: (r) => `Approved ${r.param_code} for ${r.scope_key}`,
      onSuccess: () => {
        setApproveFor(null);
        setApproveNote('');
        reload();
      },
    },
  );

  function openBlankPropose() {
    setForm(blankForm());
    setFormErr({});
    setValueArm('scalar');
    proposeM.reset();
    setProposeOpen(true);
  }

  /**
   * Propose a NEW generation of an existing parameter (supersession). The
   * identity fields are carried over — they are what makes it the same
   * parameter — but the VALUE is deliberately left blank: prefilling the
   * outgoing number is exactly how a stale value gets re-approved unchanged.
   */
  function openSupersede(row: RegulatoryParameter) {
    setForm({
      scope_type: row.scope_type,
      scope_key: row.scope_key,
      param_code: row.param_code,
      jurisdiction_code: row.jurisdiction_code,
      value_numeric: '',
      // Blank, for the same reason the scalar is: re-approving the outgoing
      // table unchanged is exactly the mistake supersession exists to prevent.
      value_json: '',
      unit: row.unit,
      source_citation: '',
      confirmation_status: 'pending',
      effective_from: today(),
      change_rationale: '',
    });
    setFormErr({});
    setValueArm(row.value_json !== null ? 'structural' : 'scalar');
    proposeM.reset();
    setHistoryFor(null);
    setProposeOpen(true);
  }

  function submitPropose() {
    const errs = validateProposal(form, today(), mode);
    setFormErr(errs);
    if (!isProposalValid(errs)) return;
    // Exactly one arm is sent. The backend refuses a row carrying both, and a
    // registered structural code refuses a number (and vice versa) at propose
    // AND at approve.
    const structuredArm =
      mode === 'structural' ? parseStructuredValue(form.value_json) : null;
    void proposeM.mutate({
      scope_type: form.scope_type,
      scope_key: form.scope_key.trim(),
      param_code: form.param_code.trim(),
      jurisdiction_code: form.jurisdiction_code.trim(),
      // Sent as the STRING the operator typed — never through Number().
      value_numeric: mode === 'scalar' ? form.value_numeric.trim() : null,
      value_json: structuredArm && structuredArm.ok ? structuredArm.value : null,
      unit: form.unit.trim(),
      source_citation: form.source_citation.trim(),
      confirmation_status: form.confirmation_status,
      effective_from: form.effective_from,
      change_rationale: form.change_rationale.trim(),
    });
  }

  function openApprove(row: RegulatoryParameter) {
    setApproveFor(row);
    setApproveNote('');
    approveM.reset();
  }

  const rows = useMemo(() => data?.parameters ?? [], [data]);
  const visible = useMemo(
    () =>
      jurisdiction.trim()
        ? rows.filter(
            (r) =>
              r.jurisdiction_code.toLowerCase() === jurisdiction.trim().toLowerCase(),
          )
        : rows,
    [rows, jurisdiction],
  );

  const chains = useMemo(() => buildChains(visible, asOf), [visible, asOf]);
  const queue = useMemo(() => visible.filter((r) => r.status === 'draft'), [visible]);
  const unconfirmed = visible.filter((r) => r.confirmation_status === 'pending').length;

  // A filter hides generations, so what a chain shows is "in force AMONG THE
  // FILTERED SET" — which can read as "nothing governs this" when the governing
  // generation was simply filtered out. Say so rather than let the register be
  // misread.
  const filtered =
    scopeType !== '' ||
    paramCode.trim() !== '' ||
    confirmation !== '' ||
    jurisdiction.trim() !== '' ||
    !includeDrafts;

  const approveEligibility = approveFor
    ? approvalEligibility(approveFor, viewerEmail)
    : null;

  // ------------------------------------------------------------------ queue
  const queueColumns: Column<RegulatoryParameter>[] = [
    {
      key: 'param',
      header: 'Parameter',
      render: (r) => (
        <div className="min-w-0">
          <div className="truncate font-mono text-caption text-navy">{r.param_code}</div>
          <div className="text-micro text-slate">
            {scopeTypeLabel(r.scope_type)} · {r.scope_key} · {r.jurisdiction_code}
          </div>
        </div>
      ),
    },
    { key: 'value', header: 'Proposed value', render: (r) => <ValueCell row={r} /> },
    {
      key: 'confirmation',
      header: 'Confirmation',
      render: (r) => <ConfirmationBadge status={r.confirmation_status} />,
    },
    {
      key: 'effective',
      header: 'Takes effect',
      render: (r) => <span className="text-caption text-ink">{fmtDate(r.effective_from)}</span>,
    },
    {
      key: 'proposed_by',
      header: 'Proposed by',
      render: (r) => <span className="font-mono text-micro text-slate">{r.proposed_by}</span>,
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      render: (r) => {
        const el = approvalEligibility(r, viewerEmail);
        if (el.state === 'blocked_own_proposal') {
          // The four-eyes rule, visible on the row — not discovered on submit.
          return (
            <span
              className="inline-flex items-center gap-1.5 text-micro text-warning"
              title={el.message}
            >
              <ShieldCheck size={13} aria-hidden />
              Needs a second operator
            </span>
          );
        }
        return (
          <Button
            size="sm"
            variant="secondary"
            icon={<ShieldCheck size={13} className="text-warning" aria-hidden />}
            className="border-warning/60 bg-warning-light text-warning hover:bg-warning-light"
            onClick={() => openApprove(r)}
          >
            Review and approve
          </Button>
        );
      },
    },
  ];

  // --------------------------------------------------------------- register
  const chainColumns: Column<ParameterChain>[] = [
    {
      key: 'param_code',
      header: 'Parameter',
      sortable: true,
      sortAccessor: (c) => c.param_code,
      render: (c) => (
        <div className="min-w-0">
          <div className="truncate font-mono text-caption text-navy">{c.param_code}</div>
          <div
            className="max-w-[24rem] truncate text-micro text-slate"
            title={c.inForce?.source_citation ?? undefined}
          >
            {c.inForce ? c.inForce.source_citation : 'No value in force'}
          </div>
        </div>
      ),
    },
    {
      key: 'scope',
      header: 'Applies to',
      sortable: true,
      sortAccessor: (c) => `${c.scope_key} ${c.scope_type}`,
      render: (c) => (
        <div className="min-w-0">
          <Chip mono>{c.scope_key}</Chip>
          <div className="mt-0.5 text-micro text-slate">
            {scopeTypeLabel(c.scope_type)} · {c.jurisdiction_code}
          </div>
        </div>
      ),
    },
    {
      key: 'value',
      header: 'Value in force',
      render: (c) =>
        c.inForce ? (
          <ValueCell row={c.inForce} />
        ) : (
          // Fail closed: nothing governs this key today. Never a 0, never blank.
          <Chip
            tone="warn"
            title="No approved generation covers today. The engines have no value to read for this scope."
          >
            None in force
          </Chip>
        ),
    },
    {
      key: 'confirmation',
      header: 'Confirmation',
      sortable: true,
      sortAccessor: (c) => c.inForce?.confirmation_status ?? 'zz',
      render: (c) =>
        c.inForce ? (
          <ConfirmationBadge status={c.inForce.confirmation_status} />
        ) : (
          <span className="text-micro text-slate">{DASH}</span>
        ),
    },
    {
      key: 'effective',
      header: 'Effective',
      sortable: true,
      sortAccessor: (c) => c.inForce?.effective_from ?? '',
      render: (c) => (
        <div className="min-w-0">
          <span className="text-caption text-ink">
            {c.inForce ? fmtDate(c.inForce.effective_from) : DASH}
          </span>
          <div className="mt-0.5 flex flex-wrap gap-1">
            {c.generations.length > 1 && (
              <span className="text-micro text-slate">
                {c.generations.length} generations
              </span>
            )}
            {c.awaitingApproval > 0 && (
              <Chip tone="accent">{c.awaitingApproval} awaiting approval</Chip>
            )}
          </div>
        </div>
      ),
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      render: (c) => (
        <div className="flex justify-end gap-1.5">
          <Button
            size="sm"
            variant="secondary"
            icon={<History size={13} aria-hidden />}
            onClick={() => setHistoryFor(c)}
          >
            History
          </Button>
          {c.inForce && (
            <Button
              size="sm"
              variant="secondary"
              icon={<Pencil size={13} aria-hidden />}
              onClick={() => openSupersede(c.inForce as RegulatoryParameter)}
            >
              Propose change
            </Button>
          )}
        </div>
      ),
    },
  ];

  return (
    <div>
      <PageHeader
        breadcrumbs={[{ label: 'Admin' }, { label: 'Regulatory parameters' }]}
        title="Regulatory parameters"
        subtitle="The regulatory numbers the calculation engines read, held as cited, dated data. Changes need two operators and never take effect retrospectively."
        action={
          <Button icon={<Plus size={15} aria-hidden />} onClick={openBlankPropose}>
            Propose parameter
          </Button>
        }
      />

      {/* ------------------------------------------------------------ filters */}
      <SectionCard title="Find a parameter">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <Field label="Applies to" htmlFor="rp-f-scope">
            <Select
              id="rp-f-scope"
              value={scopeType}
              onChange={(e) => setScopeType(e.target.value as '' | ScopeType)}
            >
              <option value="">Any</option>
              <option value="institution_class">Institution class</option>
              <option value="institution_type">Licence type</option>
            </Select>
          </Field>
          <Field label="Parameter code" htmlFor="rp-f-code">
            <Input
              id="rp-f-code"
              autoComplete="off"
              className="font-mono"
              value={paramCode}
              onChange={(e) => setParamCode(e.target.value)}
            />
          </Field>
          <Field label="Confirmation" htmlFor="rp-f-conf">
            <Select
              id="rp-f-conf"
              value={confirmation}
              onChange={(e) => setConfirmation(e.target.value as '' | ConfirmationStatus)}
            >
              <option value="">Any</option>
              <option value="confirmed">Confirmed</option>
              <option value="pending">Not confirmed</option>
            </Select>
          </Field>
          <Field label="Jurisdiction" htmlFor="rp-f-jur">
            <Input
              id="rp-f-jur"
              autoComplete="off"
              className="font-mono uppercase"
              maxLength={8}
              value={jurisdiction}
              onChange={(e) => setJurisdiction(e.target.value.toUpperCase())}
            />
          </Field>
          <Field label="Proposals" htmlFor="rp-f-drafts">
            <Select
              id="rp-f-drafts"
              value={includeDrafts ? 'yes' : 'no'}
              onChange={(e) => setIncludeDrafts(e.target.value === 'yes')}
            >
              <option value="yes">Include proposals awaiting approval</option>
              <option value="no">Approved values only</option>
            </Select>
          </Field>
        </div>
      </SectionCard>

      {/* -------------------------------------------------------------- queue */}
      {queue.length > 0 && (
        <SectionCard
          title="Awaiting approval"
          subtitle="Proposed values. None of these is read by any calculation until a second operator approves it."
          noPadding
        >
          <DataTable columns={queueColumns} rows={queue} pageSize={10} />
        </SectionCard>
      )}

      {/* ----------------------------------------------------------- register */}
      <SectionCard
        title={
          <span className="inline-flex items-center gap-1.5">
            Parameter register
            <InfoTip label="How this works" width="w-80">
              Every regulatory number lives here as data, never as a figure written into
              code. Proposing creates a new dated generation; a <strong>different</strong>{' '}
              operator must approve it before any calculation reads it. Earlier generations
              are never edited — the successor supersedes them from its effective date.
            </InfoTip>
          </span>
        }
        subtitle={
          data ? (
            <span className="inline-flex flex-wrap items-center gap-2">
              <span>
                {chains.length} parameter{chains.length === 1 ? '' : 's'} ·{' '}
                {visible.length} generation{visible.length === 1 ? '' : 's'} · applies to
                every tenant in scope
              </span>
              {unconfirmed > 0 && (
                <Chip tone="warn">{unconfirmed} not confirmed against a source</Chip>
              )}
              {filtered && (
                <span className="text-micro text-slate">
                  Filtered — generations outside the filter are hidden, so a parameter may
                  show fewer generations, or none in force, than it actually has.
                </span>
              )}
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
          {data && chains.length === 0 ? (
            <EmptyState
              Icon={ScrollText}
              title="Nothing matches"
              description="No regulatory parameter matches these filters. Clear them, or propose a parameter — it is recorded as a proposal that a second operator must approve before any calculation reads it."
              action={
                <Button icon={<Plus size={15} aria-hidden />} onClick={openBlankPropose}>
                  Propose parameter
                </Button>
              }
            />
          ) : (
            <DataTable
              columns={chainColumns}
              rows={chains}
              getFilterText={(c) =>
                `${c.param_code} ${c.scope_key} ${c.jurisdiction_code} ${
                  c.inForce?.unit ?? ''
                } ${c.inForce?.source_citation ?? ''}`
              }
              filterPlaceholder="Filter by code, scope, unit, or citation…"
              initialSort={{ key: 'param_code', dir: 'asc' }}
              pageSize={25}
            />
          )}
        </AdminBoundary>
      </SectionCard>

      {/* ------------------------------------------------------ propose (maker) */}
      <Modal
        open={proposeOpen}
        onClose={() => setProposeOpen(false)}
        size="lg"
        title="Propose a regulatory parameter"
        description="This records a proposal. A second operator must approve it before any calculation reads it."
        footer={
          <>
            <Button variant="secondary" onClick={() => setProposeOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" form="regparam-propose-form" loading={proposeM.loading}>
              Submit proposal
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
            <p className="font-medium text-navy">Two operators, and no retrospective change</p>
            <p className="mt-1">
              Enter the value exactly as the regulation states it. Nothing here is
              pre-filled — a plausible wrong number would be applied to every institution in
              scope. Earlier generations are never edited; this one supersedes them from its
              effective date.
            </p>
          </CeremonyBanner>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Applies to" required htmlFor="rp-scope-type">
              <Select
                id="rp-scope-type"
                value={form.scope_type}
                onChange={(e) =>
                  setForm((f) => ({ ...f, scope_type: e.target.value as ScopeType }))
                }
              >
                <option value="institution_class">
                  An institution class (every institution of that class)
                </option>
                <option value="institution_type">
                  A licence type (only institutions holding that licence)
                </option>
              </Select>
            </Field>
            <Field
              label={form.scope_type === 'institution_class' ? 'Class' : 'Licence code'}
              required
              error={formErr.scope_key}
              hint="The exact class or licence code the value binds to."
              htmlFor="rp-scope-key"
            >
              <Input
                id="rp-scope-key"
                autoComplete="off"
                value={form.scope_key}
                invalid={Boolean(formErr.scope_key)}
                onChange={(e) => setForm((f) => ({ ...f, scope_key: e.target.value }))}
              />
            </Field>
            <Field
              label="Parameter code"
              required
              error={formErr.param_code}
              hint="The identifier the calculation engines resolve."
              htmlFor="rp-code"
            >
              <Input
                id="rp-code"
                autoComplete="off"
                className="font-mono"
                value={form.param_code}
                invalid={Boolean(formErr.param_code)}
                onChange={(e) => setForm((f) => ({ ...f, param_code: e.target.value }))}
              />
            </Field>
            <Field
              label="Jurisdiction"
              required
              error={formErr.jurisdiction_code}
              hint="The country code whose regulation sets this value."
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
            {mode === 'scalar' ? (
              <Field
                label="Value"
                required
                error={formErr.value_numeric}
                hint="Exactly as the regulation states it. Digits and an optional decimal point."
                htmlFor="rp-value"
              >
                <Input
                  id="rp-value"
                  autoComplete="off"
                  inputMode="decimal"
                  className="font-mono"
                  value={form.value_numeric}
                  invalid={Boolean(formErr.value_numeric)}
                  onChange={(e) => setForm((f) => ({ ...f, value_numeric: e.target.value }))}
                />
              </Field>
            ) : (
              <Field
                label="Value"
                hint="This parameter carries a table, not a number. It is edited below."
              >
                <p className="text-caption text-slate">
                  {declaredShape
                    ? `Declared form: ${declaredShape}.`
                    : 'Structured value.'}
                </p>
              </Field>
            )}
            <Field
              label="Unit"
              required
              error={formErr.unit}
              hint="What the number is measured in, so it is never read on the wrong scale."
              htmlFor="rp-unit"
            >
              <Input
                id="rp-unit"
                autoComplete="off"
                value={form.unit}
                invalid={Boolean(formErr.unit)}
                onChange={(e) => setForm((f) => ({ ...f, unit: e.target.value }))}
              />
            </Field>
            <Field
              label="Effective from"
              required
              error={formErr.effective_from}
              hint="Cannot be in the past — filings already submitted keep the value they used."
              htmlFor="rp-effective"
            >
              <Input
                id="rp-effective"
                type="date"
                min={today()}
                value={form.effective_from}
                invalid={Boolean(formErr.effective_from)}
                onChange={(e) => setForm((f) => ({ ...f, effective_from: e.target.value }))}
              />
            </Field>
            <Field label="Confirmation" required htmlFor="rp-confirmation">
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
                <option value="pending">
                  Not confirmed — a working value, still to be verified
                </option>
                <option value="confirmed">
                  Confirmed — verified against the published regulation
                </option>
              </Select>
            </Field>
          </div>

          {/* ------------------------------------------- structured value (D-037) */}
          {!declaredShape && (
            <Field
              label="How this parameter is expressed"
              hint="This code has no declared form, so say which one it carries. A code the platform knows decides for itself."
              htmlFor="rp-arm"
            >
              <Select
                id="rp-arm"
                value={valueArm}
                onChange={(e) => setValueArm(e.target.value as ValueMode)}
              >
                <option value="scalar">A single number</option>
                <option value="structural">A table or mapping (JSON)</option>
              </Select>
            </Field>
          )}

          {mode === 'structural' && (
            <Field
              label="Structured value"
              required
              error={formErr.value_json}
              hint={
                declaredShape
                  ? `Checked against the declared ${declaredShape} form before it is sent, and again by the server at proposal and at approval.`
                  : 'A JSON object. The server checks it against the code\'s declared form, when it has one.'
              }
              htmlFor="rp-value-json"
            >
              <Textarea
                id="rp-value-json"
                rows={12}
                spellCheck={false}
                className="font-mono text-caption"
                value={form.value_json}
                invalid={Boolean(formErr.value_json)}
                onChange={(e) => setForm((f) => ({ ...f, value_json: e.target.value }))}
              />
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <Button
                  variant="secondary"
                  onClick={() =>
                    setForm((f) => ({ ...f, value_json: formatStructuredValue(f.value_json) }))
                  }
                >
                  Tidy the JSON
                </Button>
                {structuredPreview.ok ? (
                  <span className="text-caption text-slate">
                    Parsed. The preview below is what will be sent.
                  </span>
                ) : (
                  form.value_json.trim() !== '' && (
                    <span className="text-caption text-danger">
                      {structuredPreview.message}
                    </span>
                  )
                )}
              </div>
              {structuredPreview.ok && (
                <div className="mt-3">
                  <StructuredValuePreview body={structuredPreview.value} />
                </div>
              )}
            </Field>
          )}

          {serverShapeError && (
            <FormError>
              The server refused this value:{' '}
              <span className="font-mono">{serverShapeError.path}</span> —{' '}
              {serverShapeError.message}
            </FormError>
          )}

          <Field
            label="Source"
            required
            error={formErr.source_citation}
            hint="The regulation, notice, or directive this value comes from. This is the audit evidence."
            htmlFor="rp-citation"
          >
            <Input
              id="rp-citation"
              autoComplete="off"
              value={form.source_citation}
              invalid={Boolean(formErr.source_citation)}
              onChange={(e) => setForm((f) => ({ ...f, source_citation: e.target.value }))}
            />
          </Field>
          <Field
            label="Reason for this change"
            required
            error={formErr.change_rationale}
            hint="Why this value is being set or changed. Kept with the record permanently."
          >
            <Textarea
              rows={3}
              value={form.change_rationale}
              invalid={Boolean(formErr.change_rationale)}
              onChange={(e) => setForm((f) => ({ ...f, change_rationale: e.target.value }))}
            />
          </Field>

          {proposeM.error && (
            <ErrorPanel error={proposeM.error} context="Proposing the parameter" />
          )}
        </form>
      </Modal>

      {/* ----------------------------------------------------- approve (checker) */}
      <Modal
        open={approveFor !== null}
        onClose={() => setApproveFor(null)}
        size="md"
        title="Approve this value"
        description={
          approveFor ? (
            <span>
              <span className="font-mono">{approveFor.param_code}</span> ·{' '}
              {approveFor.scope_key} · takes effect {fmtDate(approveFor.effective_from)}
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
              disabled={approveEligibility ? !approveEligibility.canAttempt : true}
              onClick={() => {
                if (!approveFor) return;
                void approveM.mutate(approveFor.id, approveNote.trim() || undefined);
              }}
            >
              Approve
            </Button>
          </>
        }
      >
        {approveFor && approveEligibility && (
          <div className="space-y-4">
            {approveEligibility.state === 'blocked_own_proposal' ? (
              /* Four-eyes, stated up front — the approve control above is disabled.
                 The server refuses this independently on every call. */
              <div className="flex items-start gap-2 rounded border border-warning/50 bg-warning-light p-3">
                <ShieldCheck size={15} className="mt-0.5 shrink-0 text-warning" aria-hidden />
                <div className="min-w-0 text-caption text-slate">
                  <p className="text-body font-medium text-navy">
                    You proposed this value, so you cannot approve it
                  </p>
                  <p className="mt-1">
                    Two different operators are required: one to propose a regulatory value
                    and another to approve it. Ask a second operator to review this.
                  </p>
                </div>
              </div>
            ) : (
              <CeremonyBanner>
                <p className="font-medium text-navy">
                  Approving makes this the governing value
                </p>
                <p className="mt-1">
                  From its effective date the calculations read this value for every
                  institution in scope, and the previous generation is superseded.{' '}
                  {approveEligibility.state === 'viewer_unknown'
                    ? 'Approval is refused if you are the operator who proposed it.'
                    : `Proposed by ${approveFor.proposed_by}.`}
                </p>
              </CeremonyBanner>
            )}

            <div className="rounded border border-border-light bg-surface p-3">
              <div className="grid gap-x-10 sm:grid-cols-2">
                <FieldRow label="Value">
                  <ValueCell row={approveFor} />
                </FieldRow>
                <FieldRow label="Confirmation">
                  <ConfirmationBadge status={approveFor.confirmation_status} />
                </FieldRow>
                <FieldRow label="Applies to">
                  <span className="text-caption">
                    {approveFor.scope_key} · {scopeTypeLabel(approveFor.scope_type)} ·{' '}
                    {approveFor.jurisdiction_code}
                  </span>
                </FieldRow>
                <FieldRow label="Proposed by">
                  <span className="font-mono text-caption">{approveFor.proposed_by}</span>
                </FieldRow>
              </div>
              <p className="mt-2 border-t border-border-light pt-2 text-caption text-slate">
                <span className="font-medium text-ink">Source:</span>{' '}
                {approveFor.source_citation}
              </p>
              {approveFor.change_rationale && (
                <p className="mt-1 text-caption text-slate">
                  <span className="font-medium text-ink">Reason:</span>{' '}
                  {approveFor.change_rationale}
                </p>
              )}
            </div>

            {approveEligibility.canAttempt && (
              <Field
                label="Approval note (optional)"
                hint="Kept on the record and in the operator audit log."
              >
                <Textarea
                  rows={2}
                  value={approveNote}
                  onChange={(e) => setApproveNote(e.target.value)}
                />
              </Field>
            )}

            {approveM.error && (
              <div className="flex items-start gap-2 rounded border border-critical/40 bg-critical-light p-3">
                <AlertTriangle size={14} className="mt-0.5 shrink-0 text-critical" aria-hidden />
                <FormError>{approveM.error.message}</FormError>
              </div>
            )}
          </div>
        )}
      </Modal>

      {/* ------------------------------------------------------------- history */}
      <Drawer
        open={historyFor !== null}
        onClose={() => setHistoryFor(null)}
        width="w-[560px]"
        title={historyFor ? `History — ${historyFor.param_code}` : undefined}
        description={
          historyFor ? (
            <span>
              {historyFor.scope_key} · {scopeTypeLabel(historyFor.scope_type)} ·{' '}
              {historyFor.jurisdiction_code}
            </span>
          ) : undefined
        }
      >
        {historyFor && (
          <div className="space-y-3">
            <p className="text-caption text-slate">
              Every generation of this value, newest first. Values are never edited — each
              change is a new dated generation that supersedes the one before it.
            </p>
            {!historyFor.inForce && (
              <div className="flex items-start gap-2 rounded border border-warning/50 bg-warning-light p-3">
                <AlertTriangle size={14} className="mt-0.5 shrink-0 text-warning" aria-hidden />
                <p className="text-caption text-slate">
                  No approved generation covers today, so the calculations have no value to
                  read for this scope.
                </p>
              </div>
            )}
            {historyFor.generations.map((g) => {
              const life = lifecycleOf(g, asOf);
              return (
                <div
                  key={g.id}
                  className={`rounded border p-3 ${
                    life.key === 'in_force'
                      ? 'border-success/50 bg-success-light/20'
                      : 'border-border-light bg-surface'
                  }`}
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <ValueCell row={g} />
                    <div className="flex flex-wrap gap-1.5">
                      <LifecycleBadge row={g} asOf={asOf} />
                      <ConfirmationBadge status={g.confirmation_status} />
                    </div>
                  </div>
                  <p className="mt-2 text-caption text-slate">
                    {fmtDate(g.effective_from)} →{' '}
                    {g.effective_to ? fmtDate(g.effective_to) : 'open-ended'}
                  </p>
                  <p className="mt-1 text-caption text-slate">
                    <span className="font-medium text-ink">Source:</span> {g.source_citation}
                  </p>
                  {g.change_rationale && (
                    <p className="mt-1 text-caption text-slate">
                      <span className="font-medium text-ink">Reason:</span>{' '}
                      {g.change_rationale}
                    </p>
                  )}
                  <p className="mt-2 border-t border-border-light pt-2 text-micro text-slate">
                    Proposed by {g.proposed_by}
                    {g.approved_by
                      ? ` · approved by ${g.approved_by}${
                          g.approved_at ? ` on ${fmtTs(g.approved_at)}` : ''
                        }`
                      : ' · not yet approved'}
                  </p>
                </div>
              );
            })}
          </div>
        )}
      </Drawer>
    </div>
  );
}
