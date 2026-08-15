'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { AlertTriangle, ArrowRight, Plus, ShieldCheck, Waypoints } from 'lucide-react';
import {
  approveCurveDefinitionVersion,
  createCurveDefinition,
  listCurveDefinitions,
  proposeCurveDefinitionVersion,
  type DeskCurveDefinition,
  type DeskCurveDefinitionFields,
} from '@/lib/api';
import { useApi, useMutation } from '@/lib/use-api';
import { fmtDate, fmtTs, DASH } from '@/lib/format';
import {
  CeremonyBanner,
  CurveDefinitionStatusPill,
  DefinitionForm,
} from '@/components/curves';
import { DefinitionVersionDiff } from '@/components/curves/DefinitionVersionDiff';
import {
  Button,
  Chip,
  type Column,
  DataTable,
  EmptyState,
  ErrorPanel,
  Field,
  Input,
  KpiStat,
  Modal,
  PageHeader,
  QueryBoundary,
  SectionCard,
  StatusPill,
} from '@/components/ui';

/**
 * /desk/curves — governed forward-curve DEFINITIONS register (FC-4, spec §4.1).
 *
 * The Eikon "Curve 1/2/3" analogue as versioned, dual-controlled objects: each
 * curve code carries a version history (draft ▸ approved), and a definition is
 * the immutable recipe the construction workspace applies to a cob's quotes.
 * Creating, proposing, and approving are Track-2 events behind an amber
 * ceremony — deliberately heavier than a Track-1 run (spec §2.6).
 */

function currentApproved(versions: DeskCurveDefinition[]): DeskCurveDefinition | null {
  const approved = versions.filter((v) => v.status === 'approved');
  return approved.length > 0 ? approved[approved.length - 1] : null;
}

export default function CurveDefinitionsPage() {
  const { data, error, loading, reload } = useApi(() => listCurveDefinitions());

  const byCode = useMemo(() => {
    const map = new Map<string, DeskCurveDefinition[]>();
    for (const row of data?.definitions ?? []) {
      const bucket = map.get(row.curve_code) ?? [];
      bucket.push(row);
      map.set(row.curve_code, bucket);
    }
    return map;
  }, [data]);

  const stats = useMemo(() => {
    const codes = [...byCode.values()];
    return {
      codes: codes.length,
      approved: codes.filter((v) => currentApproved(v) !== null).length,
      drafts: (data?.definitions ?? []).filter((d) => d.status === 'draft').length,
    };
  }, [byCode, data]);

  // ---- ceremony state -----------------------------------------------------
  const [creating, setCreating] = useState(false);
  const [proposeFor, setProposeFor] = useState<string | null>(null);
  const [approveFor, setApproveFor] = useState<{ code: string; version: number } | null>(null);
  const [effectiveFrom, setEffectiveFrom] = useState(() => new Date().toISOString().slice(0, 10));

  // ---- mutations (Track 2) ------------------------------------------------
  const createMut = useMutation(
    (fields: DeskCurveDefinitionFields, curveCode: string) =>
      createCurveDefinition({ ...fields, curve_code: curveCode }),
    { toast: false, onSuccess: () => { setCreating(false); reload(); } },
  );
  const proposeMut = useMutation(
    (code: string, fields: DeskCurveDefinitionFields) => proposeCurveDefinitionVersion(code, fields),
    { toast: false, onSuccess: () => { setProposeFor(null); reload(); } },
  );
  const approveMut = useMutation(
    (code: string, version: number, from: string) =>
      approveCurveDefinitionVersion(code, version, { effective_from: from }),
    { toast: false, onSuccess: () => { setApproveFor(null); reload(); } },
  );

  const approveDualControl =
    approveMut.error !== null &&
    (approveMut.error.status === 409 || approveMut.error.status === 422) &&
    /proposer/i.test(approveMut.error.message);

  // ---- version table columns ----------------------------------------------
  const versionColumns = (code: string): Column<DeskCurveDefinition>[] => [
    {
      key: 'version',
      header: 'Version',
      sortable: true,
      sortAccessor: (v) => v.version,
      render: (v) => <span className="font-mono text-navy">v{v.version}</span>,
    },
    {
      key: 'status',
      header: 'Status',
      render: (v) => <CurveDefinitionStatusPill status={v.status} />,
    },
    {
      key: 'effective',
      header: 'Effective from',
      sortable: true,
      sortAccessor: (v) => v.effective_from ?? '',
      render: (v) => <span className="text-caption text-ink">{fmtDate(v.effective_from)}</span>,
    },
    {
      key: 'projection',
      header: 'Projection',
      render: (v) => <span className="font-mono text-micro text-slate">{v.projection_index ?? DASH}</span>,
    },
    {
      key: 'interp',
      header: 'Interp',
      render: (v) => <span className="font-mono text-micro text-slate">{v.interpolation_method}</span>,
    },
    {
      key: 'proposed_by',
      header: 'Proposed by',
      render: (v) => <span className="font-mono text-micro text-slate">{v.proposed_by}</span>,
    },
    {
      key: 'approved_by',
      header: 'Approved by',
      render: (v) =>
        v.approved_by ? (
          <span className="font-mono text-micro text-slate" title={fmtTs(v.approved_at)}>
            {v.approved_by}
          </span>
        ) : (
          <span className="text-slate-light">{DASH}</span>
        ),
    },
    {
      key: 'action',
      header: '',
      align: 'right',
      render: (v) =>
        v.status === 'draft' ? (
          <Button
            variant="secondary"
            size="sm"
            icon={<ShieldCheck size={12} />}
            onClick={() => {
              setApproveFor({ code, version: v.version });
              approveMut.reset();
            }}
          >
            Approve…
          </Button>
        ) : null,
    },
  ];

  const proposeVersions = proposeFor ? byCode.get(proposeFor) ?? [] : [];
  const proposeBase = proposeFor
    ? currentApproved(proposeVersions) ?? proposeVersions[proposeVersions.length - 1]
    : undefined;
  const proposeNext = proposeVersions.length
    ? proposeVersions[proposeVersions.length - 1].version + 1
    : 1;

  return (
    <div>
      <PageHeader
        title="Curve definitions"
        sub="Governed forward-curve recipes (the Eikon Curve 1/2/3 analogue) under Track-2 dual control. The construction workspace applies an APPROVED definition to a cob's quotes; changing a definition is a rare, effective-dated, second-line-approved event."
        action={
          <Button
            icon={<Plus size={15} />}
            onClick={() => {
              setCreating(true);
              createMut.reset();
            }}
          >
            New definition
          </Button>
        }
      />

      <div className="mb-6 grid gap-3 sm:grid-cols-3">
        <KpiStat label="Curve codes" value={stats.codes} hint="Governed definitions" />
        <KpiStat
          label="With approved version"
          value={stats.approved}
          status={stats.approved === stats.codes ? 'ok' : 'warn'}
          hint="Constructable today"
        />
        <KpiStat
          label="Draft versions"
          value={stats.drafts}
          status={stats.drafts > 0 ? 'warn' : undefined}
          hint="Awaiting second line"
        />
      </div>

      <QueryBoundary
        loading={loading}
        error={error}
        onRetry={reload}
        context="Loading curve definitions"
      >
        {data && data.definitions.length === 0 ? (
          <SectionCard title="Register" noPadding>
            <EmptyState
              title="No curve definitions yet"
              description="Register a definition to start. It needs a currency, calendar, instrument set, interpolation method and output basis — then a second operator approves it before construction can run."
              action={
                <Button icon={<Plus size={14} />} onClick={() => setCreating(true)}>
                  New definition
                </Button>
              }
            />
          </SectionCard>
        ) : (
          <div className="space-y-6">
            {[...byCode.entries()].map(([code, versions]) => {
              const active = currentApproved(versions);
              const head = versions[0];
              return (
                <SectionCard
                  key={code}
                  noPadding
                  title={
                    <span className="inline-flex items-center gap-2">
                      <Waypoints size={16} className="text-slate" aria-hidden />
                      <span className="font-mono">{code}</span>
                    </span>
                  }
                  subtitle={
                    <span className="inline-flex flex-wrap items-center gap-2">
                      {active ? (
                        <StatusPill tone="success">
                          approved · v{active.version}
                          {active.effective_from ? ` · from ${fmtDate(active.effective_from)}` : ''}
                        </StatusPill>
                      ) : (
                        <StatusPill tone="amber">no approved version — construction refused</StatusPill>
                      )}
                      {head && <Chip mono>{head.currency}</Chip>}
                      {head && (
                        <span className="text-micro uppercase tracking-wide text-slate">
                          {head.curve_kind}
                        </span>
                      )}
                    </span>
                  }
                  actions={
                    <>
                      <Link
                        href={`/desk/curves/${encodeURIComponent(code)}`}
                        className="inline-flex items-center gap-1.5 rounded-md border border-action/50 bg-action-light px-3 py-1.5 text-caption font-medium text-action hover:opacity-90"
                      >
                        Open workspace <ArrowRight size={13} aria-hidden />
                      </Link>
                      <Button
                        variant="secondary"
                        size="sm"
                        icon={<AlertTriangle size={13} />}
                        onClick={() => {
                          setProposeFor(code);
                          proposeMut.reset();
                        }}
                      >
                        Propose version
                      </Button>
                    </>
                  }
                >
                  <DataTable
                    columns={versionColumns(code)}
                    rows={versions}
                    density="compact"
                    initialSort={{ key: 'version', dir: 'desc' }}
                    rowClassName={(v) =>
                      active?.version === v.version ? 'bg-success-light/30' : ''
                    }
                  />
                  <div className="border-t border-border-light">
                    <div className="px-5 pt-4 text-micro font-medium uppercase tracking-wider text-slate">
                      Version diff — what a proposed recipe actually changes
                    </div>
                    <DefinitionVersionDiff versions={versions} />
                  </div>
                </SectionCard>
              );
            })}
          </div>
        )}
      </QueryBoundary>

      {/* Create ceremony (Track 2) */}
      <Modal
        open={creating}
        onClose={() => setCreating(false)}
        size="xl"
        title="Register a new curve definition"
        description="Track-2 — creates v1 as a draft under dual control"
      >
        <div className="space-y-4">
          <CeremonyBanner>
            <p className="font-medium text-navy">Track-2 — register a new curve definition</p>
            <p className="mt-1">
              Creates v1 as a DRAFT with a documented rationale. A second operator must approve it
              (dual control) before any construction or publish can run against this code. The curve
              code is the AEQ.* golden-copy name and cannot change later.
            </p>
          </CeremonyBanner>
          <DefinitionForm
            mode="create"
            busy={createMut.loading}
            submitLabel="Create v1 draft"
            onSubmit={(fields, curveCode) => void createMut.mutate(fields, curveCode)}
            onCancel={() => setCreating(false)}
          />
          {createMut.error && (
            <ErrorPanel error={createMut.error} context="Creating the curve definition" />
          )}
        </div>
      </Modal>

      {/* Propose ceremony (Track 2) */}
      <Modal
        open={proposeFor !== null}
        onClose={() => setProposeFor(null)}
        size="xl"
        title={
          <span>
            Propose v{proposeNext} of <span className="font-mono">{proposeFor}</span>
          </span>
        }
        description="Track-2 definition change — not part of a weekly run"
      >
        {proposeFor && (
          <div className="space-y-4">
            <CeremonyBanner>
              <p className="font-medium text-navy">
                Track-2 definition change — NOT part of a weekly run
              </p>
              <p className="mt-1">
                Drafts v{proposeNext} prefilled from the current version. Requires approval by a
                second operator and is effective-dated; running constructions keep their bound
                version. The latest version must be approved before a new one can be proposed.
              </p>
            </CeremonyBanner>
            <DefinitionForm
              mode="propose"
              base={proposeBase}
              busy={proposeMut.loading}
              submitLabel={`Propose v${proposeNext}`}
              onSubmit={(fields) => void proposeMut.mutate(proposeFor, fields)}
              onCancel={() => setProposeFor(null)}
            />
            {proposeMut.error && (
              <ErrorPanel error={proposeMut.error} context="Proposing a new version" />
            )}
          </div>
        )}
      </Modal>

      {/* Approve ceremony (Track 2, dual control) */}
      <Modal
        open={approveFor !== null}
        onClose={() => setApproveFor(null)}
        title={
          <span>
            Approve v{approveFor?.version} of <span className="font-mono">{approveFor?.code}</span>
          </span>
        }
        description="Track-2 approval — dual control (the proposer cannot approve)"
        footer={
          <>
            <Button variant="secondary" onClick={() => setApproveFor(null)}>
              Cancel
            </Button>
            <Button
              loading={approveMut.loading}
              disabled={!effectiveFrom}
              icon={<ShieldCheck size={14} />}
              onClick={() =>
                approveFor &&
                void approveMut.mutate(approveFor.code, approveFor.version, effectiveFrom)
              }
            >
              Approve v{approveFor?.version}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <CeremonyBanner>
            <p className="font-medium text-navy">Track-2 approval</p>
            <p className="mt-1">
              Approving makes this the governing definition from its effective date. Dual control
              applies: the proposer cannot approve their own version. Construction picks the latest
              approved version whose effective date is on or before the cob.
            </p>
          </CeremonyBanner>
          <Field label="Effective from" required className="max-w-xs">
            <Input
              type="date"
              value={effectiveFrom}
              onChange={(e) => setEffectiveFrom(e.target.value)}
            />
          </Field>
          {approveMut.error && approveDualControl && (
            <div className="flex items-start gap-2 rounded border border-warning/50 bg-warning-light p-3">
              <ShieldCheck size={14} className="mt-0.5 shrink-0 text-warning" />
              <div className="min-w-0">
                <p className="text-body font-medium text-navy">
                  Dual control: the proposer cannot approve their own curve definition — sign in as a
                  second operator.
                </p>
                <p className="mt-1 text-caption text-slate">
                  API said: “{approveMut.error.message}”
                </p>
              </div>
            </div>
          )}
          {approveMut.error && !approveDualControl && (
            <ErrorPanel error={approveMut.error} context="Approving the version" />
          )}
        </div>
      </Modal>
    </div>
  );
}
